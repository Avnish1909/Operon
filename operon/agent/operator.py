"""The operator: turns a company request into completed, verified work.

Control flow (a state machine around a tool-using model):

    UNDERSTAND ──create_brief──▶ EXECUTE ──submit_for_verification──▶ VERIFY ──pass──▶ DONE
        │ (systems read-only)      ▲   │                                  │
        │                          │   └─ escalate ─▶ ESCALATED           │ fail (≤ N rounds)
        └──────────────────────────┴──────────────────────────────────────┘

The harness, not the model, owns: the phase rules (read-only until a brief with
verifiable success criteria exists), the policy gate on every write, retries of
transient failures, loop detection, budgets, and the completion gate (only an
independent verifier can mark the work complete).

The transcript is append-only: each turn adds the model's content and the tool
results (plus a one-line status). Nothing earlier is ever rewritten, which keeps
the prompt-cache prefix and the model's thinking blocks valid for the whole run.
"""

from __future__ import annotations

import json
import time
from datetime import date

from ..company import SystemRegistry, Vault
from ..config import Settings
from ..human import ApprovalRequest, HumanChannel
from ..knowledge import KnowledgeBase, LessonStore
from ..llm import ModelClient
from ..llm.base import ModelError
from ..policy import PolicyEngine
from ..runlog import Ledger
from ..tools.base import Tool, Toolbox, ToolError, ToolResult, schema
from .dispatcher import Dispatcher
from .prompts import OPERATOR_SYSTEM, operator_kickoff
from .state import PlanItem, RunState
from .verifier import Verifier

PLAN_STATUSES = ["pending", "in_progress", "done", "blocked", "skipped"]
BOOKKEEPING = {"update_plan", "note"}
NUDGE = ("[harness] You ended your turn without calling a tool. If the work is complete, call "
         "submit_for_verification. If you are blocked, call ask_human or escalate. Otherwise continue with the "
         "next step of your plan.")


class Operator:
    def __init__(self, *, model: ModelClient, settings: Settings, tools: list[Tool], policy: PolicyEngine,
                 human: HumanChannel, kb: KnowledgeBase, lessons: LessonStore, registry: SystemRegistry,
                 vault: Vault, ledger: Ledger, state: RunState, verifier: Verifier, today: date) -> None:
        self.model, self.settings, self.policy, self.human = model, settings, policy, human
        self.kb, self.lessons, self.registry, self.vault = kb, lessons, registry, vault
        self.ledger, self.state, self.verifier, self.today = ledger, state, verifier, today
        self.toolbox = Toolbox(tools + self._control_tools())
        self.dispatcher = Dispatcher(self.toolbox, ledger, vault)
        # Nothing changes until there is a brief with verifiable success criteria.
        self.policy.readonly = True
        self.policy.readonly_reason = ("Not yet: company systems stay read-only until you have understood the "
                                       "request and called create_brief.")
        self._wrapping_up = False
        self._wrap_up_limit = 0
        self._warned = False

    # ------------------------------------------------------------------ main loop

    def run(self) -> RunState:
        st = self.state
        self.ledger.emit("run_started", run_id=st.run_id, goal=st.goal, model=self.model.model,
                         requester=st.requester)
        sections = self.kb.briefing(st.goal)
        lessons = self.lessons.relevant(st.goal)
        self.ledger.emit("context_loaded", sections=[f"{s['doc_id']} › {s['section']}" for s in sections],
                         lessons=[l["lesson"] for l in lessons])
        self.ledger.emit("phase", phase="understand", detail="research the request; systems are read-only")
        messages: list[dict] = [{"role": "user", "content": operator_kickoff(
            st.goal, st.requester, self.today, self.registry.describe(), self.kb.catalog(), sections, lessons)}]

        nudges = 0
        try:
            while not st.terminal:
                if self._wrapping_up and st.model_calls >= self._wrap_up_limit:
                    self._finish("budget_exhausted", "Stopped: the run budget was exhausted before the work "
                                 "was verified complete.")
                    break
                response = self.model.create(system=OPERATOR_SYSTEM, tools=self.toolbox.specs(),
                                             messages=messages, effort=self.settings.effort)
                st.model_calls += 1
                st.usage.add(response.usage)
                messages.append({"role": "assistant", "content": response.content})
                for summary in response.thinking_summaries:
                    self.ledger.emit("thinking", text=summary)
                if response.text:
                    self.ledger.emit("say", text=response.text)

                if response.stop_reason == "refusal":
                    self._finish("refused", f"The model declined to continue (category: {response.refusal_category}).")
                    break
                if not response.tool_uses:
                    if response.stop_reason == "pause_turn":
                        continue
                    nudges += 1
                    if nudges > 3:
                        self._finish("stalled", "The operator stopped without completing or escalating.")
                        break
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                nudges = 0

                blocks = []
                for call in response.tool_uses:
                    if st.terminal:
                        break
                    if response.stop_reason == "max_tokens":
                        blocks.append(Dispatcher.to_block(call["id"], ToolResult(
                            "Not executed: your response was cut off (max tokens). Issue this call again, "
                            "with shorter inputs if needed.", is_error=True)))
                        continue
                    if self._wrapping_up and call["name"] not in ("escalate", "submit_for_verification"):
                        blocks.append(Dispatcher.to_block(call["id"], ToolResult(
                            "Not executed: the run budget is exhausted. Call escalate now.", is_error=True)))
                        continue
                    st.steps += 1
                    block, _ = self.dispatcher.execute(call)
                    blocks.append(block)
                if st.terminal:
                    break
                status = self._status_text()
                if all(call["name"] in BOOKKEEPING for call in response.tool_uses):
                    status += ("\n[harness] That turn only updated your plan/notes. Each turn spends budget: next "
                               "time, include the bookkeeping in the same turn as your next real action.")
                blocks.append({"type": "text", "text": status})
                messages.append({"role": "user", "content": blocks})
        except ModelError as exc:
            self.ledger.emit("error", message=str(exc))
            self._finish("error", str(exc))
        except KeyboardInterrupt:
            self._finish("error", "Interrupted by the user.")
        finally:
            (self.state.run_dir / "transcript.json").write_text(
                json.dumps(messages, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
        return st

    def _status_text(self) -> str:
        st, s = self.state, self.settings
        pending = [p.id for p in self.policy.pending.values() if p.status == "pending"]
        text = st.status_line(s.max_steps, pending)
        elapsed = time.time() - st.started_at
        over = st.steps >= s.max_steps or st.model_calls >= s.max_model_calls or elapsed >= s.max_wall_seconds
        if over and not self._wrapping_up:
            self._wrapping_up = True
            self._wrap_up_limit = st.model_calls + 2
            self.ledger.emit("guard", message="budget exhausted — asking the operator to escalate")
            return text + ("\n[harness] BUDGET EXHAUSTED. Stop working now and call escalate with what was done, "
                           "what remains, and what you need.")
        if not self._warned and (st.steps >= 0.75 * s.max_steps or elapsed >= 0.75 * s.max_wall_seconds):
            self._warned = True
            self.ledger.emit("guard", message="75% of the run budget used")
            return text + "\n[harness] 75% of the budget is used. Prioritise finishing and verifying, or escalate."
        return text

    def _finish(self, status: str, summary: str) -> None:
        if self.state.terminal:
            return
        self.state.status = status
        self.state.summary = self.state.summary or summary
        self.state.phase = "done"

    # ------------------------------------------------------------------ control tools

    def _control_tools(self) -> list[Tool]:
        criterion = schema({
            "id": {"type": "string", "description": "Short id, e.g. C1"},
            "criterion": {"type": "string", "description": "A checkable statement about the end state"},
            "how_to_verify": {"type": "string", "description": "Where/how someone can confirm it"},
        }, ["id", "criterion", "how_to_verify"])
        step = schema({"id": {"type": "string"}, "step": {"type": "string"}}, ["id", "step"])
        strings = {"type": "array", "items": {"type": "string"}}
        return [
            Tool("create_brief",
                 "Record your understanding of the request before acting: intended outcome, the procedures you "
                 "will follow (handbook doc ids), verifiable success criteria and a step-by-step plan. Systems "
                 "become writable for you (subject to policy) once the brief exists. Call again to revise it if "
                 "your understanding changes.",
                 schema({
                     "intended_outcome": {"type": "string"},
                     "request_type": {"type": "string"},
                     "procedures": strings,
                     "systems": strings,
                     "success_criteria": {"type": "array", "items": criterion},
                     "plan": {"type": "array", "items": step},
                     "approvals_expected": strings,
                     "assumptions": strings,
                     "open_questions": strings,
                 }, ["intended_outcome", "procedures", "success_criteria", "plan"]),
                 self._create_brief),
            Tool("update_plan",
                 "Update plan step statuses (pending, in_progress, done, blocked, skipped) and/or add steps.",
                 schema({
                     "updates": {"type": "array", "items": schema({
                         "id": {"type": "string"},
                         "status": {"type": "string", "enum": PLAN_STATUSES},
                         "note": {"type": "string"}}, ["id", "status"])},
                     "add": {"type": "array", "items": step},
                 }),
                 self._update_plan),
            Tool("note", "Save a key fact for later in this run (an ID, amount, reference or decision).",
                 schema({"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
                 self._note),
            Tool("ask_human",
                 "Ask the requester a question when essential information is missing or ambiguous and cannot be "
                 "found in the company's systems or documents. Ask one clear question; offer options if you can.",
                 schema({"question": {"type": "string"}, "context": {"type": "string"},
                         "options": {"type": "array", "items": {"type": "string"}}}, ["question"]),
                 self._ask_human),
            Tool("request_approval",
                 "Request human approval for an action the policy engine held (use the pending id it gave you). "
                 "Give a short factual justification: what, why, and the evidence. If approved, repeat the exact "
                 "same action.",
                 schema({"pending_id": {"type": "string"}, "justification": {"type": "string"}},
                        ["pending_id", "justification"]),
                 self._request_approval),
            Tool("record_lesson",
                 "Save a durable lesson about how a company system or process behaves (e.g. a format a form "
                 "requires), so future runs avoid the same problem. Only for non-obvious, reusable facts.",
                 schema({"system": {"type": "string"}, "lesson": {"type": "string"}}, ["system", "lesson"]),
                 self._record_lesson),
            Tool("submit_for_verification",
                 "Submit the finished work for independent verification. Give a concise completion report for the "
                 "requester and, for each success criterion, the evidence you observed. An independent reviewer "
                 "checks the systems; the run completes only if every criterion is confirmed.",
                 schema({
                     "summary": {"type": "string", "description": "Completion report for the requester"},
                     "evidence": {"type": "array", "items": schema({
                         "criterion_id": {"type": "string"}, "evidence": {"type": "string"}},
                         ["criterion_id", "evidence"])},
                 }, ["summary", "evidence"]),
                 self._submit),
            Tool("escalate",
                 "Stop and hand the task back to a human when it cannot be completed (approval declined, missing "
                 "access, policy forbids it, repeated failures). Say what was done, what remains and what is needed.",
                 schema({"reason": {"type": "string"}, "work_completed": {"type": "string"},
                         "needed_from_human": {"type": "string"}}, ["reason", "work_completed", "needed_from_human"]),
                 self._escalate),
        ]

    def _create_brief(self, args: dict) -> ToolResult:
        if not args["success_criteria"] or not args["plan"]:
            raise ToolError("A brief needs at least one success criterion and at least one plan step.")
        st = self.state
        revision = (st.brief or {}).get("revision", 0) + 1
        previous = {p.id: p for p in st.plan}
        st.brief = {**args, "revision": revision}
        st.plan = [PlanItem(s["id"], s["step"], previous[s["id"]].status if s["id"] in previous else "pending")
                   for s in args["plan"]]
        self.policy.readonly = False
        first = st.phase == "understand"
        st.phase = "execute"
        self.ledger.emit("brief", brief=st.brief)
        if first:
            self.ledger.emit("phase", phase="execute", detail="brief recorded; systems writable subject to policy")
        return ToolResult(f"Brief recorded (revision {revision}). You may now make changes, subject to company "
                          f"policy.\nPlan:\n{st.plan_text()}")

    def _update_plan(self, args: dict) -> ToolResult:
        st = self.state
        by_id = {p.id: p for p in st.plan}
        unknown = []
        for upd in args.get("updates", []):
            item = by_id.get(upd["id"])
            if item is None:
                unknown.append(upd["id"])
                continue
            item.status = upd["status"]
            item.note = upd.get("note", item.note)
        for new in args.get("add", []):
            if new["id"] not in by_id:
                st.plan.append(PlanItem(new["id"], new["step"]))
        self.ledger.emit("plan", plan=[vars(p) for p in st.plan])
        warn = f"\nUnknown step ids ignored: {unknown}" if unknown else ""
        return ToolResult(f"Plan updated:\n{st.plan_text()}{warn}")

    def _note(self, args: dict) -> ToolResult:
        self.state.notes[args["key"]] = args["value"]
        self.ledger.emit("note", key=args["key"], value=args["value"])
        notes = "\n".join(f"- {k}: {v}" for k, v in self.state.notes.items())
        return ToolResult(f"Noted. All notes so far:\n{notes}")

    def _ask_human(self, args: dict) -> ToolResult:
        self.ledger.emit("question", question=args["question"])
        answer = self.human.ask(args["question"], args.get("context", ""), args.get("options"))
        self.state.questions.append({"question": args["question"], "answer": answer})
        self.ledger.emit("answer", answer=answer)
        return ToolResult(f"Answer from {self.state.requester or 'the requester'}: {answer}")

    def _request_approval(self, args: dict) -> ToolResult:
        pending = self.policy.pending.get(args["pending_id"].strip())
        if pending is None:
            known = ", ".join(self.policy.pending) or "none"
            raise ToolError(f"No held action with id '{args['pending_id']}' (known: {known}). Approval ids are given "
                            "when the policy engine holds an action.")
        if pending.status == "approved":
            return ToolResult(f"{pending.id} is already approved. Repeat the exact action now.")
        if pending.status == "used":
            return ToolResult(f"{pending.id} was approved and has already been used; the action went through.")
        if pending.status == "denied":
            return ToolResult(f"{pending.id} was declined. Do not attempt this action again.", is_error=True)
        request = ApprovalRequest(pending.id, pending.action_id, pending.summary, pending.params, pending.rule_id,
                                  pending.message, pending.approver, args["justification"])
        self.ledger.emit("approval_requested", pending_id=pending.id, summary=pending.summary,
                         justification=args["justification"], approver=(pending.approver or {}).get("name"))
        decision = self.human.approve(request)
        self.policy.record_approval(pending.id, decision.approved, decision.approver)
        record = {"pending_id": pending.id, "summary": pending.summary, "rule": pending.rule_id,
                  "approved": decision.approved, "approver": decision.approver, "comment": decision.comment,
                  "justification": args["justification"]}
        self.state.approvals.append(record)
        self.ledger.emit("approval", **record)
        comment = f" Comment: {decision.comment}" if decision.comment else ""
        if decision.approved:
            return ToolResult(f"APPROVED by {decision.approver}.{comment} Now repeat the exact same action "
                              "(same values); the approval is valid once.")
        return ToolResult(f"DECLINED by {decision.approver}.{comment} Do not perform this action or work around "
                          "it. Adjust the plan within policy, or escalate.", is_error=True)

    def _record_lesson(self, args: dict) -> ToolResult:
        added = self.lessons.add(args["system"], args["lesson"], self.state.run_id)
        if added:
            self.state.lessons.append({"system": args["system"], "lesson": args["lesson"]})
            self.ledger.emit("lesson", system=args["system"], lesson=args["lesson"])
        return ToolResult("Lesson saved for future runs." if added else "That lesson is already recorded.")

    def _submit(self, args: dict) -> ToolResult:
        st = self.state
        if not st.brief:
            raise ToolError("There is no brief yet: create_brief first, then do the work.")
        round_no = len(st.verifications) + 1
        st.phase = "verify"
        report = self.verifier.verify(st.goal, self.today, st.brief, args["summary"], args["evidence"],
                                      self.registry.describe(), round_no)
        st.verifier_usage.add(report.usage)
        st.verifications.append({"round": round_no, "passed": report.passed, "results": report.results,
                                 "assessment": report.assessment, "claimed_summary": args["summary"]})
        self.ledger.emit("verification", round=round_no, passed=report.passed, results=report.results,
                         assessment=report.assessment)
        if report.passed:
            st.status, st.phase, st.summary = "completed", "done", args["summary"]
            return ToolResult(report.feedback(), terminal=True)
        if round_no >= self.settings.max_verification_rounds:
            st.status, st.phase = "unverified", "done"
            st.summary = (f"{args['summary']}\n\nNOT VERIFIED after {round_no} rounds — needs human review: "
                          f"{report.assessment}")
            return ToolResult(report.feedback(), is_error=True, terminal=True)
        st.phase = "execute"
        return ToolResult(report.feedback(), is_error=True)

    def _escalate(self, args: dict) -> ToolResult:
        st = self.state
        st.escalation = args
        st.status = "budget_exhausted" if self._wrapping_up else "escalated"
        st.phase = "done"
        st.summary = (f"Escalated: {args['reason']}\nDone so far: {args['work_completed']}\n"
                      f"Needed: {args['needed_from_human']}")
        return ToolResult("Escalation recorded. The run will stop now.", terminal=True)
