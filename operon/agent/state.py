"""Run state: everything the operator knows about the task in progress.

The model's transcript is one view of the run; this is the structured one. It is
what the harness reasons about (phase, plan, budgets), what is re-surfaced to the
model each turn as a compact status line, and what the report is built from.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..llm import Usage

STATUS_MARK = {"done": "✓", "in_progress": "▶", "pending": "·", "blocked": "!", "skipped": "–"}


@dataclass
class PlanItem:
    id: str
    step: str
    status: str = "pending"
    note: str = ""


@dataclass
class RunState:
    run_id: str
    goal: str
    run_dir: Path
    requester: str = ""
    phase: str = "understand"          # understand → execute → verify → done
    brief: dict | None = None
    plan: list[PlanItem] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    approvals: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    lessons: list[dict] = field(default_factory=list)
    status: str = "running"            # running | completed | escalated | unverified | budget_exhausted | refused | error | stalled
    summary: str = ""
    escalation: dict | None = None
    steps: int = 0
    model_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    verifier_usage: Usage = field(default_factory=Usage)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def terminal(self) -> bool:
        return self.status != "running"

    @property
    def duration_s(self) -> float:
        return round((self.finished_at or time.time()) - self.started_at, 1)

    def plan_text(self) -> str:
        if not self.plan:
            return "(no plan yet)"
        return "\n".join(f"{STATUS_MARK.get(p.status, '?')} {p.id}. {p.step}" + (f" — {p.note}" if p.note else "")
                         for p in self.plan)

    def status_line(self, max_steps: int, pending_approvals: list[str]) -> str:
        done = sum(1 for p in self.plan if p.status == "done")
        parts = [f"step {self.steps}/{max_steps}", f"phase: {self.phase}"]
        if self.plan:
            current = next((p for p in self.plan if p.status == "in_progress"), None)
            parts.append(f"plan {done}/{len(self.plan)} done" + (f", current: {current.id}" if current else ""))
        if pending_approvals:
            parts.append("awaiting approval request for: " + ", ".join(pending_approvals))
        return "[operator status] " + " · ".join(parts)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["run_dir"] = str(self.run_dir)
        data["duration_s"] = self.duration_s
        return data
