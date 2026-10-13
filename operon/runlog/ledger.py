"""Run ledger: an append-only, secret-redacted event log plus a live console view.

Every decision the operator makes — what it read, what it did, what the policy
engine said, what humans answered — lands in runs/<id>/events.jsonl. The HTML
report is generated from it, so the record of a run is the evidence of a run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from ..company import Vault

_STYLE = {
    "allow": "green", "require_approval": "yellow", "deny": "red",
}


class Ledger:
    def __init__(self, run_dir: Path, vault: Vault | None = None, console: Console | None = None,
                 quiet: bool = False) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "events.jsonl"
        self.vault = vault
        self.console = console or Console(highlight=False)
        self.quiet = quiet
        self.events: list[dict] = []
        self._t0 = time.monotonic()

    def emit(self, kind: str, actor: str = "operator", **data: Any) -> dict:
        event = {"t": round(time.monotonic() - self._t0, 2), "kind": kind, "actor": actor, **data}
        line = json.dumps(event, default=str, ensure_ascii=False)
        if self.vault:
            line = self.vault.redact(line)
            event = json.loads(line)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.events.append(event)
        if not self.quiet:
            self._render(event)
        return event

    # ------------------------------------------------------------------ console

    def _render(self, e: dict) -> None:
        kind, actor = e["kind"], e["actor"]
        stamp = f"[dim]{int(e['t']) // 60:02d}:{int(e['t']) % 60:02d}[/dim]"
        who = "" if actor == "operator" else f"[magenta]{actor}[/magenta] "
        p = self.console.print
        if kind == "run_started":
            p(f"\n[bold]Operon[/bold] · run {e['run_id']} · model {e['model']}")
            p(f"[bold cyan]Goal:[/bold cyan] {escape(e['goal'])}\n")
        elif kind == "context_loaded":
            p(f"{stamp} {who}[dim]context: {len(e.get('sections', []))} handbook sections, "
              f"{len(e.get('lessons', []))} lessons from earlier runs[/dim]")
        elif kind == "phase":
            p(f"{stamp} {who}[bold blue]── {e['phase'].upper()} ──[/bold blue] {escape(e.get('detail', ''))}")
        elif kind == "thinking":
            p(f"{stamp} {who}[italic dim]thinking: {escape(_clip(e['text'], 300))}[/italic dim]")
        elif kind == "say":
            p(f"{stamp} {who}[white]{escape(_clip(e['text'], 400))}[/white]")
        elif kind == "tool_call":
            p(f"{stamp} {who}[cyan]→ {e['name']}[/cyan]([dim]{escape(_clip(_args(e['input']), 160))}[/dim])")
        elif kind == "tool_result":
            colour = "red" if not e["ok"] else "dim"
            retry = f" [yellow](recovered after {e['retries']} retries)[/yellow]" if e.get("retries") else ""
            p(f"{stamp} {who}  [{colour}]{escape(_clip(e['summary'], 220))}[/{colour}]{retry}")
        elif kind == "policy":
            colour = _STYLE.get(e["outcome"], "white")
            label = {"allow": "allowed", "require_approval": "HELD for approval", "deny": "DENIED"}[e["outcome"]]
            if e["outcome"] == "allow" and e.get("action", "").endswith(("sign_in", "sign_out")):
                return
            p(f"{stamp} {who}  [{colour}]policy: {label} — {escape(e['summary'])}[/{colour}]")
        elif kind == "approval":
            colour = "green" if e["approved"] else "red"
            verdict = "approved" if e["approved"] else "declined"
            p(f"{stamp} [{colour}]✔ {e['approver']} {verdict} {e['pending_id']}[/{colour}] {escape(e.get('comment', ''))}")
        elif kind == "question":
            p(f"{stamp} [cyan]? asked: {escape(e['question'])}[/cyan]")
        elif kind == "answer":
            p(f"{stamp} [cyan]  answer: {escape(e['answer'])}[/cyan]")
        elif kind == "brief":
            b = e["brief"]
            p(f"{stamp} [bold]Brief:[/bold] {escape(b.get('intended_outcome', ''))}")
            for c in b.get("success_criteria", []):
                p(f"        [dim]criterion {c.get('id')}: {escape(c.get('criterion', ''))}[/dim]")
            for s in b.get("plan", []):
                p(f"        [dim]step {s.get('id')}: {escape(s.get('step', ''))}[/dim]")
        elif kind == "plan":
            done = sum(1 for s in e["plan"] if s["status"] == "done")
            p(f"{stamp} [dim]plan: {done}/{len(e['plan'])} steps done[/dim]")
        elif kind == "note":
            p(f"{stamp} [dim]note {escape(e['key'])} = {escape(_clip(str(e['value']), 120))}[/dim]")
        elif kind == "lesson":
            p(f"{stamp} [magenta]lesson recorded ({e['system']}): {escape(e['lesson'])}[/magenta]")
        elif kind == "guard":
            p(f"{stamp} [yellow]⚠ {escape(e['message'])}[/yellow]")
        elif kind == "verification":
            colour = "green" if e["passed"] else "red"
            p(f"{stamp} [{colour}]verification round {e['round']}: {'PASSED' if e['passed'] else 'FAILED'}[/{colour}]")
            for r in e.get("results", []):
                mark = {"pass": "[green]✓[/green]", "fail": "[red]✗[/red]"}.get(r["status"], "[yellow]?[/yellow]")
                p(f"        {mark} {escape(r['criterion_id'])}: {escape(_clip(r.get('evidence', ''), 200))}")
        elif kind == "run_finished":
            colour = "green" if e["status"] == "completed" else "yellow" if e["status"] == "escalated" else "red"
            p(f"\n[bold {colour}]Run {e['status'].upper()}[/bold {colour}] · {e.get('steps')} steps · "
              f"{e.get('model_calls')} model calls · {e.get('duration_s')}s"
              + (f" · ~${e['cost_usd']:.2f}" if e.get("cost_usd") is not None else ""))
            if e.get("summary"):
                p(escape(e["summary"]))
            p(f"[dim]Report: {e.get('report')}[/dim]")
        elif kind == "error":
            p(f"{stamp} [bold red]error: {escape(e['message'])}[/bold red]")


def _args(data: dict) -> str:
    parts = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"
