"""Policy-as-code: classifies state-changing requests and decides allow / approve / deny.

The engine sits at the network boundary of every tool that can change company
state (browser requests are intercepted, API calls pass through it). That makes
the guarantees independent of what the model believes or is told:

* a request that needs approval is held — never sent — until a human approves
  that *exact* action (same action type and parameters);
* each approval is single-use, so it cannot be replayed for a second refund;
* denied actions are refused no matter which UI path or API the agent tries;
* in read-only mode (used by the verifier) every write is refused.
"""

from __future__ import annotations

import itertools
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class ActionRequest:
    method: str
    path: str
    body: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    via: str = "browser"  # browser | api

    @classmethod
    def from_raw(cls, method: str, url: str, body_text: str | None, content_type: str | None,
                 via: str = "browser") -> "ActionRequest":
        parsed = urllib.parse.urlparse(url)
        return cls(method=method.upper(), path=parsed.path, body=parse_body(body_text, content_type),
                   query=_flatten(urllib.parse.parse_qs(parsed.query, keep_blank_values=True)), via=via)


def parse_body(text: str | None, content_type: str | None) -> dict:
    if not text:
        return {}
    if content_type and "json" in content_type:
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {"_": data}
        except json.JSONDecodeError:
            return {}
    return _flatten(urllib.parse.parse_qs(text, keep_blank_values=True))


def _flatten(multi: dict[str, list[str]]) -> dict[str, Any]:
    return {k: (v[0] if len(v) == 1 else v) for k, v in multi.items()}


@dataclass
class Rule:
    id: str
    decision: str
    when: dict | None = None
    approver: str | None = None
    message: str = ""


@dataclass
class ActionSpec:
    id: str
    method: str
    path_template: str
    pattern: re.Pattern
    params: dict[str, Any]
    money: list[str]
    summary: str
    rules: list[Rule]
    readonly_safe: bool = False
    approval_keys: list[str] | None = None   # params an approval is bound to (default: all)

    def key(self, params: dict) -> str:
        keys = self.approval_keys or sorted(params)
        return grant_key(self.id, {k: params.get(k) for k in keys})


@dataclass
class Decision:
    outcome: str                      # allow | require_approval | deny
    action_id: str
    summary: str
    params: dict[str, Any]
    rule_id: str | None = None
    message: str = ""
    approver: dict | None = None
    pending_id: str | None = None     # set when outcome == require_approval
    grant_id: str | None = None       # set when an approval was consumed to allow this
    key: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    def describe(self) -> str:
        if self.outcome == "allow":
            via = f" (using approval {self.grant_id})" if self.grant_id else ""
            return f"ALLOWED {self.action_id}: {self.summary}{via}"
        if self.outcome == "deny":
            return f"DENIED {self.action_id}: {self.summary} — {self.message} [rule {self.rule_id}]"
        who = f"{self.approver['name']} ({self.approver['title']})" if self.approver else "an approver"
        return (f"HELD FOR APPROVAL {self.action_id}: {self.summary} — {self.message} "
                f"[rule {self.rule_id}; approver: {who}; pending id: {self.pending_id}]")


@dataclass
class PendingAction:
    id: str
    action_id: str
    summary: str
    params: dict[str, Any]
    rule_id: str | None
    message: str
    approver: dict | None
    status: str = "pending"           # pending | approved | denied | used
    key: str = ""


@dataclass
class Grant:
    id: str
    key: str
    pending_id: str
    approver: str
    uses_left: int = 1


class PolicyEngine:
    def __init__(self, actions: list[ActionSpec], approvers: dict[str, dict], defaults: dict,
                 readonly: bool = False, on_decision: Callable[[Decision, ActionRequest], None] | None = None) -> None:
        self.actions = actions
        self.approvers = approvers
        self.defaults = defaults
        self.readonly = readonly
        self.readonly_reason = "This session is read-only (verification); no changes are allowed."
        self.on_decision = on_decision
        self.pending: dict[str, PendingAction] = {}
        self.grants: dict[str, Grant] = {}
        self.decisions: list[Decision] = []
        self._ids = itertools.count(1)
        self._grant_ids = itertools.count(1)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ classification

    def classify(self, req: ActionRequest) -> tuple[ActionSpec | None, dict]:
        for spec in self.actions:
            if spec.method != req.method:
                continue
            m = spec.pattern.fullmatch(req.path)
            if m:
                return spec, self._extract(spec, req, m.groupdict())
        return None, {}

    def _extract(self, spec: ActionSpec, req: ActionRequest, path_params: dict) -> dict:
        def resolve(ref: str):
            source, _, name = ref.partition(".")
            data = {"path": path_params, "body": req.body, "query": req.query}.get(source, {})
            return data.get(name)

        params: dict[str, Any] = {}
        for key, ref in spec.params.items():
            if isinstance(ref, list):
                merged: list = []
                for r in ref:
                    value = resolve(r)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        value = [v.strip() for v in value.split(",") if v.strip()]
                    merged.extend(value if isinstance(value, list) else [value])
                params[key] = merged
            else:
                params[key] = resolve(ref)
        for key in spec.money:
            params[key] = parse_money(params.get(key))
        return params

    # ------------------------------------------------------------------ decisions

    def evaluate(self, req: ActionRequest) -> Decision:
        with self._lock:
            decision = self._evaluate(req)
            self.decisions.append(decision)
        if self.on_decision:
            self.on_decision(decision, req)
        return decision

    def _evaluate(self, req: ActionRequest) -> Decision:
        spec, params = self.classify(req)
        if spec is None:
            summary = f"{req.method} {req.path} (not in the action catalog)"
            if self.readonly:
                return Decision("deny", "unknown", summary, {}, "READ-ONLY", self.readonly_reason)
            outcome = self.defaults.get("unknown_write", "require_approval")
            approver = self.approvers.get(self.defaults.get("unknown_write_approver", ""))
            decision = Decision(outcome, "unknown", summary, {"method": req.method, "path": req.path, "body": req.body},
                                "UNKNOWN-WRITE", "This action is not in the company action catalog.", approver)
            return self._hold_if_needed(decision)

        summary = render_summary(spec.summary, params)
        key = spec.key(params)
        if self.readonly and not spec.readonly_safe:
            return Decision("deny", spec.id, summary, params, "READ-ONLY", self.readonly_reason, key=key)
        for name in _money_params_in_rules(spec):
            if params.get(name) is None:
                return Decision("deny", spec.id, summary, params, "UNREADABLE-AMOUNT",
                                f"Could not read '{name}' as an amount; enter a plain number such as 349.00.",
                                key=key)
        for rule in spec.rules:
            if rule.when is None or condition_holds(rule.when, params):
                approver = self.approvers.get(rule.approver or "")
                decision = Decision(rule.decision, spec.id, summary, params, rule.id, rule.message.strip(), approver,
                                    key=key)
                return self._hold_if_needed(decision)
        return Decision("allow", spec.id, summary, params, key=key)

    def _hold_if_needed(self, decision: Decision) -> Decision:
        if decision.outcome != "require_approval":
            return decision
        key = decision.key or grant_key(decision.action_id, decision.params)
        grant = self.grants.get(key)
        if grant and grant.uses_left > 0:
            grant.uses_left -= 1
            self.pending[grant.pending_id].status = "used"
            decision.outcome = "allow"
            decision.grant_id = grant.id
            return decision
        if grant and self.pending[grant.pending_id].status == "used":
            decision.message += " (The earlier approval for this exact action has already been used.)"
        # Reuse an identical outstanding request instead of piling up duplicates.
        for pending in self.pending.values():
            if pending.status in ("pending", "denied") and pending.key == key:
                decision.pending_id = pending.id
                if pending.status == "denied":
                    decision.outcome = "deny"
                    decision.message = f"An approver already declined this exact action ({pending.id})."
                return decision
        pending_id = f"A-{next(self._ids)}"
        self.pending[pending_id] = PendingAction(pending_id, decision.action_id, decision.summary, decision.params,
                                                 decision.rule_id, decision.message, decision.approver, key=key)
        decision.pending_id = pending_id
        return decision

    # ------------------------------------------------------------------ approvals

    def record_approval(self, pending_id: str, approved: bool, approver: str) -> Grant | None:
        with self._lock:
            pending = self.pending[pending_id]
            if not approved:
                pending.status = "denied"
                return None
            pending.status = "approved"
            grant = Grant(f"G-{next(self._grant_ids)}", pending.key, pending_id, approver)
            self.grants[grant.key] = grant
            return grant

    def known_actions(self, path_prefix: str) -> list[str]:
        """Catalogued write actions under a path prefix, e.g. a system's API (used in error hints)."""
        return [f"{spec.method} {spec.path_template} — {spec.summary}" for spec in self.actions
                if spec.path_template.startswith(path_prefix)]

    def restore_grant(self, grant_id: str) -> None:
        """The approved request reached the system but was rejected (e.g. a validation error),
        so the action did not happen: give the approval back for the corrected retry."""
        with self._lock:
            for grant in self.grants.values():
                if grant.id == grant_id:
                    grant.uses_left += 1
                    self.pending[grant.pending_id].status = "approved"


# ---------------------------------------------------------------------- helpers

def parse_money(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    cleaned = str(value).replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def render_summary(template: str, params: dict) -> str:
    def fmt(value):
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, list):
            return ", ".join(map(str, value))
        return "?" if value is None else str(value)

    return re.sub(r"\{(\w+)\}", lambda m: fmt(params.get(m.group(1))), template)


def grant_key(action_id: str, params: dict) -> str:
    def norm(value):
        if isinstance(value, str):
            return value.strip().lower()
        if isinstance(value, list):
            return sorted(norm(v) for v in value)
        return value

    return action_id + "|" + json.dumps({k: norm(v) for k, v in sorted(params.items())}, sort_keys=True, default=str)


def condition_holds(cond: dict, params: dict) -> bool:
    value = params.get(cond["param"])
    op, target = cond["op"], cond.get("value")
    if value is None:
        # Unknown amount on a gated action: be conservative and treat thresholds as exceeded.
        return op in ("gt", "gte")
    try:
        if op == "gt":
            return float(value) > float(target)
        if op == "gte":
            return float(value) >= float(target)
        if op == "lt":
            return float(value) < float(target)
        if op == "lte":
            return float(value) <= float(target)
    except (TypeError, ValueError):
        return True
    values = value if isinstance(value, list) else [value]
    if op == "eq":
        return value == target
    if op == "ne":
        return value != target
    if op == "contains_any":
        return any(str(v).strip().lower() in {str(t).lower() for t in target} for v in values)
    if op == "any_not_endswith":
        return any(not str(v).strip().lower().endswith(str(target).lower()) for v in values)
    raise ValueError(f"unknown policy operator {op!r}")


def _money_params_in_rules(spec: ActionSpec) -> list[str]:
    return [r.when["param"] for r in spec.rules if r.when and r.when["param"] in spec.money]


def _compile_path(template: str) -> re.Pattern:
    regex = re.escape(template)
    regex = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", regex)
    return re.compile(regex)


def load_policy(company_dir: Path, readonly: bool = False,
                on_decision: Callable[[Decision, ActionRequest], None] | None = None) -> PolicyEngine:
    catalog = yaml.safe_load((company_dir / "actions.yaml").read_text(encoding="utf-8"))
    people = yaml.safe_load((company_dir / "people.yaml").read_text(encoding="utf-8"))
    actions = []
    for item in catalog["actions"]:
        actions.append(ActionSpec(
            id=item["id"],
            method=item["match"]["method"].upper(),
            path_template=item["match"]["path"],
            pattern=_compile_path(item["match"]["path"]),
            params=item.get("params", {}),
            money=item.get("money", []),
            summary=item.get("summary", item["id"]),
            rules=[Rule(id=r["id"], decision=r["decision"], when=r.get("when"), approver=r.get("approver"),
                        message=r.get("message", "")) for r in item.get("rules", [])],
            readonly_safe=bool(item.get("readonly_safe", False)),
            approval_keys=item.get("approval_keys"),
        ))
    return PolicyEngine(actions, people.get("approvers", {}), catalog.get("defaults", {}), readonly, on_decision)
