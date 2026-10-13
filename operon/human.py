"""Human-in-the-loop channel: questions and approvals.

The operator never decides approvals itself. When the policy engine holds an
action, the operator asks through this channel and the answer is bound to that
exact action. Implementations:

* ConsoleHuman — a person at the terminal (default for `operon run`);
* ScriptedHuman — deterministic answers for evals and tests;
* AutoHuman — approve/deny everything (CI smoke runs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from rich.markup import escape


@dataclass
class ApprovalRequest:
    pending_id: str
    action_id: str
    summary: str
    params: dict
    rule_id: str | None
    policy_message: str
    approver: dict | None
    justification: str


@dataclass
class ApprovalDecision:
    approved: bool
    approver: str
    comment: str = ""


class HumanChannel(Protocol):
    def ask(self, question: str, context: str = "", options: list[str] | None = None) -> str: ...

    def approve(self, request: ApprovalRequest) -> ApprovalDecision: ...


class ConsoleHuman:
    def __init__(self, console=None) -> None:
        from rich.console import Console

        self.console = console or Console()

    def ask(self, question: str, context: str = "", options: list[str] | None = None) -> str:
        from rich.panel import Panel
        from rich.prompt import Prompt

        body = escape(question) + (f"\n\n[dim]{escape(context)}[/dim]" if context else "")
        if options:
            body += "\n\n" + "\n".join(f"  {i + 1}. {escape(o)}" for i, o in enumerate(options))
        self.console.print(Panel(body, title="The operator has a question", border_style="cyan"))
        answer = Prompt.ask("[cyan]Your answer[/cyan]", console=self.console).strip()
        if options and answer.isdigit() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        return answer

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        from rich.panel import Panel
        from rich.prompt import Confirm, Prompt

        who = f"{request.approver['name']} ({request.approver['title']})" if request.approver else "an approver"
        params = "\n".join(f"  {k}: {v}" for k, v in request.params.items() if k != "body")
        if "body" in request.params:
            params += "\n  body:\n    " + str(request.params["body"]).replace("\n", "\n    ")
        self.console.print(Panel(
            f"[bold]{escape(request.summary)}[/bold]\n\nPolicy {escape(str(request.rule_id))}: "
            f"{escape(request.policy_message)}\nApproval needed from: {escape(who)}\n\nExact action:\n"
            f"{escape(params)}\n\nOperator's justification: {escape(request.justification)}",
            title=f"Approval request {request.pending_id}", border_style="yellow"))
        approved = Confirm.ask(f"Approve as {who}?", console=self.console)
        comment = Prompt.ask("Comment (optional)", default="", console=self.console)
        return ApprovalDecision(approved, request.approver["name"] if request.approver else "approver", comment)


@dataclass
class ScriptedHuman:
    """Answers questions by regex and approvals by predicate; records everything it was asked."""

    answers: list[tuple[str, str]] = field(default_factory=list)          # (question regex, answer)
    approvals: list[tuple[Callable[[ApprovalRequest], bool], bool, str]] = field(default_factory=list)
    default_answer: str = "I don't have more information — please follow company policy."
    default_approval: bool = False
    asked: list[str] = field(default_factory=list)
    approval_requests: list[ApprovalRequest] = field(default_factory=list)

    def ask(self, question: str, context: str = "", options: list[str] | None = None) -> str:
        self.asked.append(question)
        haystack = f"{question}\n{context}"
        for pattern, answer in self.answers:
            if re.search(pattern, haystack, re.IGNORECASE | re.DOTALL):
                return answer
        return self.default_answer

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        self.approval_requests.append(request)
        name = request.approver["name"] if request.approver else "approver"
        for predicate, approved, comment in self.approvals:
            if predicate(request):
                return ApprovalDecision(approved, name, comment)
        return ApprovalDecision(self.default_approval, name, "No standing instruction for this request.")


class AutoHuman(ScriptedHuman):
    def __init__(self, approve_all: bool = True) -> None:
        super().__init__(default_approval=approve_all)
