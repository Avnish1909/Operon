"""The browser tool against the live sandbox: perception, actions and the network-level policy gate."""

from __future__ import annotations

import pytest

from operon.company import SystemRegistry, Vault
from operon.llm.scripted import find_ref
from operon.policy import load_policy
from operon.tools.base import ToolError
from operon.tools.browser import BrowserSession

pytestmark = pytest.mark.browser


@pytest.fixture()
def session(live_world, browser_host, settings, tmp_path):
    registry = SystemRegistry.load(settings.company_dir, settings.sandbox_url)
    vault = Vault(settings.vault_path)
    policy = load_policy(settings.company_dir)
    s = BrowserSession(browser_host, registry, policy, vault, tmp_path / "evidence")
    yield s, policy, vault
    s.close()


def sign_in(s, page_text: str, system: str):
    return s.fill([{"ref": find_ref(page_text, r'textbox "Username"'), "value": "ops.agent"},
                   {"ref": find_ref(page_text, r'password "Password"'), "value": f"{{{{secret:{system}.password}}}}"}],
                  submit_ref=find_ref(page_text, r'button "Sign in"'))


def test_snapshot_renders_tables_forms_and_refs(session):
    s, _, _ = session
    page = sign_in(s, s.navigate("/erp/customers/C-1003").text, "erp").text
    assert "URL: /erp/customers/C-1003" in page
    assert "| P-5013 |" in page and "$349.00" in page
    assert '[e' in page and 'link "Refund" -> /erp/payments/P-5013/refund' in page


def test_secrets_never_appear_in_observations(session):
    s, _, vault = session
    first = s.navigate("/erp/").text
    result = sign_in(s, first, "erp")
    assert vault.get("erp.password") not in result.text
    assert "(secret)" in result.text


def test_held_action_is_not_sent_and_resubmits_after_approval(session):
    s, policy, _ = session
    sign_in(s, s.navigate("/erp/").text, "erp")
    page = s.navigate("/erp/payments/P-5013/refund").text
    held = s.fill([{"ref": find_ref(page, r'textbox "Refund amount'), "value": "349.00"},
                   {"ref": find_ref(page, r'select "Reason"'), "value": "Duplicate charge"}],
                  submit_ref=find_ref(page, r'button "Issue refund"'))
    assert held.is_error and "HELD FOR APPROVAL" in held.text
    assert 'value="349.00"' in held.text                     # the form kept its values (204, not an error page)
    pending = next(iter(policy.pending))
    policy.record_approval(pending, True, "Maria Gomez")
    done = s.click(find_ref(held.text, r'button "Issue refund"'))
    assert not done.is_error and "Refund R-9001 issued" in done.text
    assert done.data.get("evidence")                          # screenshot captured after the write


def test_approval_survives_a_validation_error(session):
    s, policy, _ = session
    sign_in(s, s.navigate("/erp/").text, "erp")
    page = s.navigate("/erp/bills/new").text
    fields = [{"ref": find_ref(page, r'select "Vendor"'), "value": "TechSource Solutions"},
              {"ref": find_ref(page, r'textbox "PO number"'), "value": "PO-7792"},
              {"ref": find_ref(page, r'textbox "Vendor invoice number"'), "value": "TS-30412"},
              {"ref": find_ref(page, r'textbox "Invoice date"'), "value": "2026-10-08"},
              {"ref": find_ref(page, r'textbox "Amount'), "value": "12400.00"}]
    held = s.fill(fields, submit_ref=find_ref(page, r'button "Save bill"'))
    policy.record_approval(next(iter(policy.pending)), True, "Raj Patel")
    rejected = s.click(find_ref(held.text, r'button "Save bill"'))
    assert "Expected format DD/MM/YYYY" in rejected.text and "approval remains valid" in rejected.text
    fixed = s.fill([{"ref": find_ref(rejected.text, r'textbox "Invoice date"'), "value": "08/10/2026"}],
                   submit_ref=find_ref(rejected.text, r'button "Save bill"'))
    assert "Bill B-4008 created" in fixed.text


def test_forbidden_action_is_denied_whatever_the_ui_says(session):
    s, _, _ = session
    sign_in(s, s.navigate("/erp/").text, "erp")
    page = s.navigate("/erp/customers/C-1002").text
    page = s.click(find_ref(page, r'disclosure "Danger zone"')).text
    result = s.click(find_ref(page, r'button "Delete customer"'))
    assert result.is_error and "DENIED erp.delete_customer" in result.text
    assert "Globex Corporation" in s.navigate("/erp/customers").text


def test_egress_is_limited_to_company_systems(session):
    s, _, _ = session
    with pytest.raises(ToolError, match="not a registered company system"):
        s.navigate("https://example.com/")


def test_stale_refs_are_reported_clearly(session):
    s, _, _ = session
    s.navigate("/erp/")
    with pytest.raises(ToolError, match="refs change"):
        s.click("e999")
