"""Policy-as-code: classification, thresholds, approval binding, read-only mode."""

from __future__ import annotations

import pytest

from operon.policy import ActionRequest, load_policy


@pytest.fixture()
def policy(company_dir):
    return load_policy(company_dir)


def refund(amount: str, payment: str = "P-5013", reason: str = "Duplicate charge") -> ActionRequest:
    return ActionRequest("POST", f"/erp/payments/{payment}/refund", {"amount": amount, "reason": reason})


def test_small_refund_is_allowed(policy):
    d = policy.evaluate(refund("89.50"))
    assert d.outcome == "allow" and d.action_id == "erp.issue_refund"
    assert d.params["amount"] == 89.5 and d.params["payment_id"] == "P-5013"


def test_refund_over_250_is_held_for_finance_manager(policy):
    d = policy.evaluate(refund("$349.00"))
    assert d.outcome == "require_approval"
    assert d.rule_id == "APPROVAL-REFUND-250"
    assert d.approver["name"] == "Maria Gomez"
    assert d.pending_id in policy.pending


def test_refund_over_5000_is_denied_outright(policy):
    assert policy.evaluate(refund("5,000.01")).outcome == "deny"


def test_unreadable_amount_is_refused_not_guessed(policy):
    d = policy.evaluate(refund("three hundred"))
    assert d.outcome == "deny" and d.rule_id == "UNREADABLE-AMOUNT"


def test_approval_is_single_use_and_bound_to_the_exact_action(policy):
    held = policy.evaluate(refund("349.00"))
    policy.record_approval(held.pending_id, True, "Maria Gomez")

    assert policy.evaluate(refund("394.00")).outcome == "require_approval"          # different amount
    assert policy.evaluate(refund("349.00", payment="P-5012")).outcome == "require_approval"  # other payment
    first = policy.evaluate(refund("349.00", reason="Other"))                       # reason is not material
    assert first.outcome == "allow" and first.grant_id
    second = policy.evaluate(refund("349.00"))                                      # approval already used
    assert second.outcome == "require_approval" and second.pending_id != held.pending_id


def test_rejected_submission_gives_the_approval_back(policy):
    held = policy.evaluate(refund("349.00"))
    policy.record_approval(held.pending_id, True, "Maria Gomez")
    used = policy.evaluate(refund("349.00"))
    policy.restore_grant(used.grant_id)          # e.g. the form came back with a validation error
    assert policy.evaluate(refund("349.00")).outcome == "allow"


def test_declined_action_stays_declined(policy):
    held = policy.evaluate(refund("349.00"))
    policy.record_approval(held.pending_id, False, "Maria Gomez")
    again = policy.evaluate(refund("349.00"))
    assert again.outcome == "deny"


def test_identical_held_requests_share_one_pending_id(policy):
    assert policy.evaluate(refund("349.00")).pending_id == policy.evaluate(refund("349.00")).pending_id


def test_customer_deletion_is_always_denied(policy):
    d = policy.evaluate(ActionRequest("POST", "/erp/customers/C-1002/delete"))
    assert d.outcome == "deny" and d.rule_id == "DP-4"


def test_external_email_needs_comms_approval_internal_does_not(policy):
    internal = ActionRequest("POST", "/mail/api/send", {"to": ["ap-team@northwind.example"], "subject": "s", "body": "b"})
    external = ActionRequest("POST", "/mail/api/send", {"to": ["ap-team@northwind.example"],
                                                       "cc": ["someone@globex.example"], "subject": "s", "body": "b"})
    assert policy.evaluate(internal).outcome == "allow"
    assert policy.evaluate(external).outcome == "require_approval"


def test_privileged_access_needs_it_security(policy):
    form = ActionRequest("POST", "/hr/employees/E-205/access", {"entitlements": ["email", "erp:admin"]})
    basic = ActionRequest("POST", "/hr/employees/E-205/access", {"entitlements": ["email", "crm:sales-user"]})
    assert policy.evaluate(form).outcome == "require_approval"
    assert policy.evaluate(basic).outcome == "allow"


def test_large_bill_needs_controller(policy):
    req = ActionRequest("POST", "/erp/bills/new", {"vendor_id": "V-304", "po_number": "PO-7792",
                                                   "vendor_invoice_no": "TS-30412", "amount": "12400.00",
                                                   "invoice_date": "08/10/2026"})
    assert policy.evaluate(req).approver["name"] == "Raj Patel"


def test_unknown_writes_are_held(policy):
    d = policy.evaluate(ActionRequest("POST", "/erp/something-new"))
    assert d.outcome == "require_approval" and d.rule_id == "UNKNOWN-WRITE"


def test_unknown_api_write_suggests_documented_endpoints(company_dir, settings):
    from operon.company import SystemRegistry, Vault
    from operon.tools.http_api import ApiClient

    policy = load_policy(company_dir)
    client = ApiClient(SystemRegistry.load(company_dir, settings.sandbox_url), policy, Vault(settings.vault_path))
    result = client.call("desk", "POST", "/desk/api/tickets/1045/replies", {"body": "hi"})
    assert result.is_error and "HELD FOR APPROVAL" in result.text
    assert "POST /desk/api/tickets/{ticket_id}/messages" in result.text


def test_read_only_mode_allows_sign_in_only(company_dir):
    ro = load_policy(company_dir, readonly=True)
    assert ro.evaluate(ActionRequest("POST", "/erp/login", {"username": "x"})).outcome == "allow"
    assert ro.evaluate(refund("10.00")).outcome == "deny"
    assert ro.evaluate(ActionRequest("POST", "/erp/unknown")).outcome == "deny"


def test_form_bodies_are_parsed_from_raw_requests():
    req = ActionRequest.from_raw("POST", "http://h/hr/employees/E-205/access?x=1",
                                 "entitlements=email&entitlements=crm%3Asales-user", "application/x-www-form-urlencoded")
    assert req.body["entitlements"] == ["email", "crm:sales-user"] and req.query == {"x": "1"}
