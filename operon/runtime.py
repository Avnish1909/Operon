"""Wiring: builds an operator (and its verifier) for a run, against the company's systems."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console

from .agent.operator import Operator
from .agent.state import RunState
from .agent.verifier import Verifier
from .company import SystemRegistry, Vault
from .config import Settings
from .human import HumanChannel
from .knowledge import KnowledgeBase, LessonStore
from .llm import ModelClient, Usage
from .policy import load_policy
from .runlog import Ledger
from .runlog.report import write_reports
from .tools.browser import BrowserHost, BrowserSession, browser_tools
from .tools.files import SharedDrive, file_tools
from .tools.http_api import ApiClient, api_tools
from .tools.knowledge_tools import knowledge_tools


class Runtime:
    def __init__(self, settings: Settings, human: HumanChannel, model: ModelClient | None = None,
                 verifier_model: ModelClient | None = None, console: Console | None = None, quiet: bool = False,
                 manage_sandbox: bool = True, browser_host: BrowserHost | None = None) -> None:
        self.settings, self.human, self.console, self.quiet = settings, human, console or Console(), quiet
        self._model, self._verifier_model = model, verifier_model
        self.manage_sandbox = manage_sandbox
        # Playwright's sync driver is one-per-thread; callers that already own one can share it.
        self._shared_host = browser_host
        self._server = None

    # ------------------------------------------------------------------ lifecycle

    def __enter__(self) -> "Runtime":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        s = self.settings
        if self.manage_sandbox:
            from sandbox import server as sandbox_server

            sandbox_server.ensure_world()
            if not sandbox_server.is_running(s.sandbox_url):
                parsed = urlparse(s.sandbox_url)
                self._server = sandbox_server.SandboxServer(parsed.hostname, parsed.port).start()
        self.registry = SystemRegistry.load(s.company_dir, s.sandbox_url)
        self.vault = Vault(s.vault_path)
        self.kb = KnowledgeBase(s.company_dir, self.registry.url_map())
        self.lessons = LessonStore(s.memory_dir / "lessons.jsonl")
        self.host = self._shared_host or BrowserHost(s.headless, s.slow_mo_ms)

    def close(self) -> None:
        if self._shared_host is None:
            self.host.close()
        if self._server:
            self._server.stop()
            self._server = None

    def reset_world(self) -> None:
        """Recreate the sandbox company (fresh data, files and credentials)."""
        from sandbox import server as sandbox_server

        sandbox_server.reset_world()
        self.vault.reload()
        if self._server:
            self._server.reload_vault()
        else:
            _post(f"{self.settings.sandbox_url}/__admin/reload-vault", {})

    def set_faults(self, faults: dict) -> None:
        _post(f"{self.settings.sandbox_url}/__admin/faults", faults)

    @property
    def model(self) -> ModelClient:
        if self._model is None:
            s = self.settings
            if s.provider == "openrouter":
                from .llm.openrouter_client import OpenRouterModel

                self._model = OpenRouterModel(s.model, s.effort)
            else:
                from .llm.anthropic_client import AnthropicModel

                self._model = AnthropicModel(s.model, s.effort, refusal_fallbacks=s.refusal_fallbacks)
        return self._model

    @property
    def verifier_model(self) -> ModelClient:
        return self._verifier_model or self.model

    def business_date(self) -> date:
        db_path = self.settings.var_dir / "sandbox.db"
        if self.manage_sandbox and db_path.exists():
            from sandbox import db as sandbox_db

            conn = sandbox_db.connect(db_path)
            try:
                return sandbox_db.get_business_date(conn)
            finally:
                conn.close()
        return date.today()

    # ------------------------------------------------------------------ a run

    def run(self, goal: str, requester: str = "Olivia Chen (COO)") -> RunState:
        s = self.settings
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + _slug(goal)
        run_dir = s.runs_dir / run_id
        ledger = Ledger(run_dir, self.vault, self.console, self.quiet)
        state = RunState(run_id=run_id, goal=goal, run_dir=run_dir, requester=requester)

        def on_evidence(actor: str):
            def record(label: str, path: Path) -> None:
                ledger.emit("evidence", actor, label=label, path=str(path))
                state.evidence.append({"actor": actor, "label": label, "path": str(path)})
            return record

        def on_decision(actor: str):
            return lambda d, req: ledger.emit("policy", actor, outcome=d.outcome, action=d.action_id,
                                              summary=d.summary, rule=d.rule_id, message=d.message,
                                              pending_id=d.pending_id, via=req.via)

        policy = load_policy(s.company_dir, on_decision=on_decision("operator"))
        session = BrowserSession(self.host, self.registry, policy, self.vault, run_dir / "evidence", "operator",
                                 on_evidence("operator"))
        tools = (knowledge_tools(self.kb) + file_tools(SharedDrive(s.workspace_dir))
                 + api_tools(ApiClient(self.registry, policy, self.vault)) + browser_tools(session))

        def verifier_tools():
            ro_policy = load_policy(s.company_dir, readonly=True, on_decision=on_decision("verifier"))
            ro_session = BrowserSession(self.host, self.registry, ro_policy, self.vault, run_dir / "evidence",
                                        "verifier", on_evidence("verifier"))
            ro_tools = (knowledge_tools(self.kb) + file_tools(SharedDrive(s.workspace_dir, read_only=True))
                        + api_tools(ApiClient(self.registry, ro_policy, self.vault, read_only=True))
                        + browser_tools(ro_session))
            return ro_tools, ro_session.close

        verifier = Verifier(self.verifier_model, verifier_tools, ledger, self.vault, s.verifier_effort,
                            s.verifier_max_steps)
        operator = Operator(model=self.model, settings=s, tools=tools, policy=policy, human=self.human, kb=self.kb,
                            lessons=self.lessons, registry=self.registry, vault=self.vault, ledger=ledger,
                            state=state, verifier=verifier, today=self.business_date())
        try:
            operator.run()
        finally:
            session.close()
            state.finished_at = time.time()

        total = Usage()
        total.add(state.usage)
        total.add(state.verifier_usage)
        cost = total.cost_usd(self.model.model)
        report = run_dir / "report.html"
        ledger.emit("run_finished", status=state.status, summary=state.summary, steps=state.steps,
                    model_calls=state.model_calls, duration_s=state.duration_s, cost_usd=cost, report=str(report))
        write_reports(state, ledger.events, self.model.model, cost)
        state_data = state.to_dict()
        state_data["cost_usd"] = cost
        (run_dir / "state.json").write_text(json.dumps(state_data, indent=1, default=str, ensure_ascii=False),
                                            encoding="utf-8")
        return state


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return "-".join(words)[:48] or "run"


def _post(url: str, body: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5):
        pass
