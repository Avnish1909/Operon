"""End-to-end harness tests with a scripted model.

These drive the real operator loop, tools, browser, policy gate, verifier, ledger and
report against the live sandbox. The model's moves are scripted so the harness's
guarantees can be tested deterministically and without an API key.
"""

from __future__ import annotations

import json

import pytest

from operon.agent.dispatcher import Dispatcher
from operon.company import Vault
from operon.human import ScriptedHuman
from operon.llm.scripted import ScriptedModel, find_ref, last_tool_result, text, tool
from operon.runlog import Ledger
from operon.runtime import Runtime
from operon.tools.base import Tool, Toolbox, ToolResult, TransientError, schema
from sandbox import db as sandbox_db

pytestmark = pytest.mark.browser

BRIEF = dict(
    intended_outcome="Refund Hooli's damaged desk organisers order (P-5020, $89.50) in full.",
    procedures=["sop-refunds"],
    success_criteria=[{"id": "C1", "criterion": "Refund of $89.50 on P-5020, reason Damaged goods",
                       "how_to_verify": "ERP customer page C-1004, refunds table"}],
    plan=[{"id": "1", "step": "Issue the refund in the ERP"}, {"id": "2", "step": "Verify"}],
)


def login_turn(system: str):
    def turn(messages):
        page = last_tool_result(messages)
        return [tool("browser_fill", fields=[
            {"ref": find_ref(page, r'textbox "Username"'), "value": "ops.agent"},
            {"ref": find_ref(page, r'password "Password"'), "value": f"{{{{secret:{system}.password}}}}"}],
            submit_ref=find_ref(page, r'button "Sign in"'))]
    return turn


def refund_turn(amount: str, reason: str):
    def turn(messages):
        page = last_tool_result(messages)
        return [tool("browser_fill", fields=[
            {"ref": find_ref(page, r'textbox "Refund amount'), "value": amount},
            {"ref": find_ref(page, r'select "Reason"'), "value": reason}],
            submit_ref=find_ref(page, r'button "Issue refund"'))]
    return turn


def verifier_script(expect: str) -> ScriptedModel:
    def verdict(messages):
        page = last_tool_result(messages)
        ok = expect in page
        return [tool("report_verification", assessment="Checked the ERP customer page.", results=[
            {"criterion_id": "C1", "status": "pass" if ok else "fail",
             "evidence": "Refund row present" if ok else "No matching refund on the customer page"}])]
    return ScriptedModel([[tool("browser_navigate", url="/erp/customers/C-1004")], login_turn("erp"), verdict])


@pytest.fixture(autouse=True)
def _host(browser_host):
    global HOST
    HOST = browser_host


def make_runtime(settings, operator: ScriptedModel, verifier: ScriptedModel | None = None, human=None, **overrides):
    s = settings.with_overrides(**overrides) if overrides else settings
    return Runtime(s, human or ScriptedHuman(), model=operator, verifier_model=verifier or ScriptedModel([]),
                   quiet=True, browser_host=HOST)


def refunds(settings):
    conn = sandbox_db.connect(settings.var_dir / "sandbox.db")
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM refunds WHERE id != 'R-8990'")]
    finally:
        conn.close()


def test_full_loop_ask_brief_act_verify_complete(live_world, settings):
    human = ScriptedHuman(answers=[(r"which", "The $89.50 one — it arrived damaged.")])
    operator = ScriptedModel([
        [text("Two Hooli payments last week; I need to know which."), tool("search_knowledge", query="refund damaged goods")],
        [tool("ask_human", question="Which Hooli order should I refund?", options=["P-5020 $89.50", "P-5021 $412.00"])],
        [tool("create_brief", **BRIEF)],
        [tool("browser_navigate", url="/erp/payments/P-5020/refund")],
        login_turn("erp"),
        refund_turn("89.50", "Damaged goods"),
        [tool("update_plan", updates=[{"id": "1", "status": "done"}])],
        [tool("submit_for_verification", summary="Refunded $89.50 on P-5020.",
              evidence=[{"criterion_id": "C1", "evidence": "R-9001 shown on the customer page"}])],
    ])
    with make_runtime(settings, operator, verifier_script("Damaged goods"), human) as rt:
        state = rt.run("Please refund Hooli's order from last week.", "Dana Lee")

    assert state.status == "completed", state.summary
    assert [r["payment_id"] for r in refunds(settings)] == ["P-5020"]
    assert state.questions and state.verifications[0]["passed"]
    assert (state.run_dir / "report.html").exists() and (state.run_dir / "events.jsonl").exists()
    report = (state.run_dir / "report.html").read_text(encoding="utf-8")
    assert "COMPLETED" in report and "Refund of $89.50" in report
    # The transcript must be append-only: every request extends the previous one unchanged.
    calls = operator.calls
    assert all(calls[i] == calls[i + 1][: len(calls[i])] for i in range(len(calls) - 1))
    vault = Vault(settings.vault_path)
    assert vault.get("erp.password") not in (state.run_dir / "events.jsonl").read_text(encoding="utf-8")


def test_no_changes_before_the_brief(live_world, settings):
    operator = ScriptedModel([
        [tool("browser_navigate", url="/erp/payments/P-5020/refund")],
        login_turn("erp"),
        refund_turn("89.50", "Damaged goods"),
        lambda messages: [tool("escalate", reason="blocked: " + last_tool_result(messages)[:80],
                               work_completed="none", needed_from_human="n/a")],
    ])
    with make_runtime(settings, operator) as rt:
        state = rt.run("Refund Hooli's order.")
    assert state.status == "escalated"
    assert refunds(settings) == []
    assert "create_brief" in json.dumps(operator.calls[-1])


def test_failed_verification_feeds_back_then_unverified(live_world, settings):
    operator = ScriptedModel([
        [tool("create_brief", **BRIEF)],
        [tool("submit_for_verification", summary="Done (it is not).", evidence=[{"criterion_id": "C1", "evidence": "trust me"}])],
    ])
    with make_runtime(settings, operator, verifier_script("Damaged goods"), max_verification_rounds=1) as rt:
        state = rt.run("Refund Hooli's order.")
    assert state.status == "unverified"
    assert state.verifications[0]["results"][0]["status"] == "fail"


def test_approval_flow_through_the_operator(live_world, settings):
    human = ScriptedHuman(approvals=[(lambda r: r.params.get("amount") == 412.0, True, "ok")])
    brief = {**BRIEF, "success_criteria": [{"id": "C1", "criterion": "Refund $412 on P-5021", "how_to_verify": "ERP"}]}
    operator = ScriptedModel([
        [tool("create_brief", **brief)],
        [tool("browser_navigate", url="/erp/payments/P-5021/refund")],
        login_turn("erp"),
        refund_turn("412.00", "Damaged goods"),
        lambda m: [tool("request_approval", pending_id="A-1", justification="Customer confirmed damage")],
        lambda m: [tool("browser_snapshot")],
        lambda m: [tool("browser_click", ref=find_ref(last_tool_result(m), r'button "Issue refund"'))],
        [tool("escalate", reason="test end", work_completed="refund", needed_from_human="none")],
    ])
    with make_runtime(settings, operator, human=human) as rt:
        state = rt.run("Refund Hooli's conference room order.")
    assert [r["payment_id"] for r in refunds(settings)] == ["P-5021"]
    assert state.approvals and state.approvals[0]["approved"]
    held = [m for m in operator.calls[4][-1]["content"] if m.get("type") == "tool_result"][0]
    assert held.get("is_error") and "HELD FOR APPROVAL" in held["content"][0]["text"]


def test_verifier_is_told_to_report_on_its_last_step(live_world, settings):
    def report_when_told(messages):
        told = "final step" in json.dumps(messages[-1])
        return [tool("report_verification", assessment="Out of steps.", results=[
            {"criterion_id": "C1", "status": "unclear", "evidence": "ran out of steps"}])] if told \
            else [tool("search_knowledge", query="refunds")]

    verifier = ScriptedModel([report_when_told] * 3)
    operator = ScriptedModel([
        [tool("create_brief", **BRIEF)],
        [tool("submit_for_verification", summary="Done.", evidence=[{"criterion_id": "C1", "evidence": "x"}])],
        [tool("escalate", reason="not verified", work_completed="x", needed_from_human="y")],
    ])
    with make_runtime(settings, operator, verifier, verifier_max_steps=3) as rt:
        state = rt.run("Refund Hooli's order.")
    result = state.verifications[0]["results"][0]
    assert result["status"] == "unclear" and result["evidence"] == "ran out of steps"   # a real report, not a timeout
    assert len(verifier.calls) == 3


def test_verifier_that_ignores_the_warning_is_made_to_report(live_world, settings):
    def stubborn(messages):
        if "out of verification steps" in json.dumps(messages[-1]):
            return [tool("report_verification", assessment="Forced.", results=[
                {"criterion_id": "C1", "status": "unclear", "evidence": "budget ran out"}])]
        return [tool("search_knowledge", query="refunds")]       # ignores the final-step instruction

    verifier = ScriptedModel([stubborn] * 6)
    operator = ScriptedModel([
        [tool("create_brief", **BRIEF)],
        [tool("submit_for_verification", summary="Done.", evidence=[{"criterion_id": "C1", "evidence": "x"}])],
        [tool("escalate", reason="not verified", work_completed="x", needed_from_human="y")],
    ])
    with make_runtime(settings, operator, verifier, verifier_max_steps=2) as rt:
        state = rt.run("Refund Hooli's order.")
    assert state.verifications[0]["results"][0]["evidence"] == "budget ran out"
    assert len(verifier.calls) == 4          # 2 budgeted steps + 2 grace turns at most


def test_stalling_model_is_stopped(live_world, settings):
    operator = ScriptedModel([[text("Thinking about it...")]] * 6)
    with make_runtime(settings, operator) as rt:
        state = rt.run("Do something.")
    assert state.status == "stalled"


def test_bookkeeping_only_turns_are_flagged(live_world, settings):
    operator = ScriptedModel([
        [tool("note", key="k", value="v")],
        [tool("escalate", reason="test", work_completed="none", needed_from_human="none")],
    ])
    with make_runtime(settings, operator) as rt:
        rt.run("Take a note.")
    assert "only updated your plan/notes" in json.dumps(operator.calls[1][-1])


def test_budget_exhaustion_asks_to_escalate(live_world, settings):
    operator = ScriptedModel([[tool("note", key=f"k{i}", value="v")] for i in range(3)]
                             + [[tool("escalate", reason="budget", work_completed="notes", needed_from_human="more time")]])
    with make_runtime(settings, operator, max_steps=2) as rt:
        state = rt.run("Keep taking notes.")
    assert state.status == "budget_exhausted"
    assert "BUDGET EXHAUSTED" in json.dumps(operator.calls[-1])


def test_dispatcher_retries_only_idempotent_calls(tmp_path, settings):
    attempts = {"get": 0, "post": 0}

    def flaky(kind):
        def handler(args):
            attempts[kind] += 1
            if attempts[kind] < 3:
                raise TransientError("HTTP 503")
            return ToolResult("ok")
        return handler

    box = Toolbox([Tool("read_thing", "", schema({}), flaky("get"), idempotent=True),
                   Tool("write_thing", "", schema({}), flaky("post"), idempotent=False)])
    d = Dispatcher(box, Ledger(tmp_path, quiet=True), Vault(tmp_path / "none.json"))
    _, ok = d.execute({"id": "1", "name": "read_thing", "input": {}})
    assert not ok.is_error and "Recovered automatically after 2" in ok.text
    _, failed = d.execute({"id": "2", "name": "write_thing", "input": {}})
    assert failed.is_error and attempts["post"] == 1
    _, invalid = d.execute({"id": "3", "name": "read_thing", "input": {"x": 1}})
    assert invalid.is_error and "not a recognised field" in invalid.text
