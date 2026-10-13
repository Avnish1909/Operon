"""Independent verification: a second agent that checks the work in the systems of record.

Why a separate agent rather than the operator checking itself:
* fresh context — it has not "seen itself" do the work, so it cannot be anchored by
  its own narrative; it gets only the request, the criteria and the claims to test;
* fresh browser session and a read-only policy engine — it observes the systems as
  they are, and cannot "fix" anything while verifying;
* it may add criteria the operator forgot (from the request and the SOP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..llm import ModelClient, Usage
from ..llm.base import ModelError
from ..runlog import Ledger
from ..tools.base import Tool, Toolbox, ToolResult, schema
from .dispatcher import Dispatcher
from .prompts import VERIFIER_SYSTEM, verifier_kickoff

GRACE_TURNS = 2


@dataclass
class VerificationReport:
    passed: bool
    results: list[dict]
    assessment: str
    usage: Usage = field(default_factory=Usage)
    completed: bool = True

    def feedback(self) -> str:
        lines = [f"Independent verification {'PASSED' if self.passed else 'FAILED'}. {self.assessment}".strip()]
        for r in self.results:
            lines.append(f"- {r['criterion_id']} [{r['status'].upper()}] {r.get('criterion', '')}: {r.get('evidence', '')}")
        if not self.passed:
            lines.append("Fix every item that did not pass (do not redo work that passed), check it yourself, "
                         "then call submit_for_verification again. If something cannot be fixed, call escalate.")
        return "\n".join(lines)


class Verifier:
    def __init__(self, model: ModelClient, toolbox_factory, ledger: Ledger, vault, effort: str = "medium",
                 max_steps: int = 30) -> None:
        self.model = model
        self.toolbox_factory = toolbox_factory   # () -> (list[Tool], close_fn)
        self.ledger, self.vault = ledger, vault
        self.effort, self.max_steps = effort, max_steps

    def verify(self, goal: str, today: date, brief: dict, report: str, claims: list[dict], systems: str,
               round_no: int) -> VerificationReport:
        outcome: dict = {}

        def report_tool(args: dict) -> ToolResult:
            outcome.update(args)
            return ToolResult("Verification report recorded.", terminal=True)

        tools, close = self.toolbox_factory()
        toolbox = Toolbox(tools + [Tool(
            "report_verification",
            "Record your verdict for every criterion (including any you added). Call exactly once, at the end.",
            schema({
                "results": {"type": "array", "items": schema({
                    "criterion_id": {"type": "string"},
                    "criterion": {"type": "string", "description": "The criterion text"},
                    "status": {"type": "string", "enum": ["pass", "fail", "unclear"]},
                    "evidence": {"type": "string", "description": "What you observed, where (page/record/file)"},
                }, ["criterion_id", "status", "evidence"])},
                "assessment": {"type": "string", "description": "One or two sentences overall"},
            }, ["results", "assessment"]),
            report_tool,
        )])
        dispatcher = Dispatcher(toolbox, self.ledger, self.vault, actor="verifier")
        messages: list[dict] = [{"role": "user", "content": verifier_kickoff(goal, today, brief, report, claims, systems)}]
        usage = Usage()
        self.ledger.emit("phase", "verifier", phase="verify", detail=f"round {round_no}: independent check")
        last_call_warning = ("[harness] This is your final step: call report_verification now with what you have "
                             "observed. Mark anything you could not check as unclear.")
        try:
            # After the step budget, up to GRACE_TURNS more turns in which only report_verification runs:
            # a verifier that keeps browsing past its budget is made to file what it has observed.
            for step in range(self.max_steps + GRACE_TURNS):
                in_grace = step >= self.max_steps
                response = self.model.create(system=VERIFIER_SYSTEM, tools=toolbox.specs(), messages=messages,
                                             effort=self.effort)
                usage.add(response.usage)
                messages.append({"role": "assistant", "content": response.content})
                for summary in response.thinking_summaries:
                    self.ledger.emit("thinking", "verifier", text=summary)
                if response.stop_reason == "refusal":
                    break
                warn = step >= self.max_steps - 2
                if not response.tool_uses:
                    messages.append({"role": "user", "content": last_call_warning if warn else
                                     "Continue checking, then call report_verification."})
                    continue
                results = []
                for block in response.tool_uses:
                    if in_grace and block["name"] != "report_verification":
                        results.append(Dispatcher.to_block(block["id"], ToolResult(
                            "Not executed: you are out of verification steps. Call report_verification now with "
                            "what you have observed; mark anything you could not check as unclear.", is_error=True)))
                        continue
                    result_block, _ = dispatcher.execute(block)
                    results.append(result_block)
                if outcome:
                    break
                if warn:
                    results.append({"type": "text", "text": last_call_warning})
                messages.append({"role": "user", "content": results})
        except ModelError as exc:
            self.ledger.emit("error", "verifier", message=str(exc))
        finally:
            close()

        if not outcome:
            return VerificationReport(False, [{"criterion_id": "*", "status": "unclear",
                                               "evidence": "The verifier could not complete its checks."}],
                                      "Verification did not complete.", usage, completed=False)
        criteria = {c.get("id"): c.get("criterion", "") for c in brief.get("success_criteria", [])}
        results = []
        for r in outcome.get("results", []):
            results.append({**r, "criterion": r.get("criterion") or criteria.get(r["criterion_id"], "")})
        checked = {r["criterion_id"] for r in results}
        for cid, text in criteria.items():
            if cid not in checked:
                results.append({"criterion_id": cid, "criterion": text, "status": "unclear",
                                "evidence": "Not checked by the verifier."})
        passed = bool(results) and all(r["status"] == "pass" for r in results)
        return VerificationReport(passed, results, outcome.get("assessment", ""), usage)
