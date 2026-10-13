"""The simulated company behaves like the real, imperfect systems it stands in for."""

from __future__ import annotations

import pytest

from sandbox import faults, server


@pytest.fixture()
def client(world):
    app = server.create_app()
    return app.test_client(), world


def login(c, system, vault):
    resp = c.post(f"/{system}/login", data={"username": "ops.agent", "password": vault[f"{system}.password"]})
    assert resp.status_code == 302


def test_pages_require_login_and_keep_the_target(client):
    c, vault = client
    resp = c.get("/erp/customers/C-1003")
    assert resp.status_code == 302 and "next=/erp/customers/C-1003" in resp.headers["Location"]
    login(c, "erp", vault)
    assert b"Initech LLC" in c.get("/erp/customers/C-1003").data


def test_bill_form_insists_on_uk_dates_and_does_not_detect_duplicates(client):
    c, vault = client
    login(c, "erp", vault)
    form = {"vendor_id": "V-303", "po_number": "PO-7785", "vendor_invoice_no": "OH-0091", "amount": "612.40"}
    resp = c.post("/erp/bills/new", data={**form, "invoice_date": "2026-09-20"})
    assert b"Expected format DD/MM/YYYY" in resp.data
    resp = c.post("/erp/bills/new", data={**form, "invoice_date": "20/09/2026"})
    assert resp.status_code == 302  # legacy quirk: the duplicate is accepted


def test_refund_cannot_exceed_refundable_balance(client):
    c, vault = client
    login(c, "erp", vault)
    assert c.post("/erp/payments/P-5013/refund", data={"amount": "349.00", "reason": "Duplicate charge"}).status_code == 302
    resp = c.post("/erp/payments/P-5013/refund", data={"amount": "1.00", "reason": "Duplicate charge"})
    assert b"exceeds the refundable balance" in resp.data


def test_faults_transient_and_session_expiry(client):
    c, vault = client
    login(c, "erp", vault)
    faults.configure(transient={"/erp/invoices": 1}, session_expiry={"erp": 2})
    assert c.get("/erp/invoices").status_code == 503
    assert c.get("/erp/invoices").status_code == 200
    expired = c.get("/erp/invoices")
    assert expired.status_code == 302 and "expired=1" in expired.headers["Location"]
    faults.configure()


def test_helpdesk_api_requires_key_and_updates(client):
    c, vault = client
    assert c.get("/desk/api/tickets/1042").status_code == 401
    key = {"X-API-Key": vault["desk.api_key"]}
    assert c.get("/desk/api/tickets/1042", headers=key).json["subject"].startswith("Charged twice")
    assert c.patch("/desk/api/tickets/1042", json={"status": "bogus"}, headers=key).status_code == 400
    assert c.get("/desk/api/tickets?q=Hooli", headers=key).status_code == 400        # unknown filters are rejected
    assert c.get("/desk/api/tickets?status=all", headers=key).status_code == 400
    assert len(c.get("/desk/api/tickets?customer_id=C-1003", headers=key).json["tickets"]) == 1
    assert c.patch("/desk/api/tickets/1042", json={"status": "solved", "tags": ["billing"]}, headers=key).json["status"] == "solved"


def test_mail_api_validates_attachments(client):
    c, vault = client
    key = {"X-API-Key": vault["mail.api_key"]}
    bad = c.post("/mail/api/send", json={"to": ["a@northwind.example"], "subject": "s", "body": "b",
                                         "attachments": ["../vault.json"]}, headers=key)
    assert bad.status_code == 400
    ok = c.post("/mail/api/send", json={"to": "a@northwind.example", "subject": "s", "body": "b",
                                        "attachments": ["hr/role_access_matrix.xlsx"]}, headers=key)
    assert ok.status_code == 201 and ok.json["attachments"][0]["bytes"] > 0


def test_hr_grants_generate_work_email(client):
    c, vault = client
    login(c, "hr", vault)
    c.post("/hr/employees/E-203/access", data={"entitlements": ["crm:sales-user"]})
    page = c.get("/hr/employees/E-203").data.decode()
    assert "crm:sales-user" in page
