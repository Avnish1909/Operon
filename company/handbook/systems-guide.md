---
title: Systems guide — where things live and how to use them
summary: The company's systems (ERP, helpdesk, HR portal, mail, shared drive), what each is the system of record for, logins and API reference.
tags: [systems, erp, helpdesk, desk, hr, peopleport, mail, api, login, shared drive]
---

# Systems guide

| System | What it is the system of record for | Interfaces |
|---|---|---|
| Northwind Ledger (ERP) | customers, payments, refunds, customer invoices (receivables), vendors, purchase orders, vendor bills, AP holds | web UI only (legacy, no API) |
| Northwind Desk | customer support tickets and conversations | REST API (preferred) and web UI |
| PeoplePort (HR) | employee records, access provisioning | web UI only |
| Northwind Mail | sending email | REST API only |
| Shared drive | vendor invoices (ap_inbox/), HR reference files (hr/), reports (reports/) | file access |

Prefer an API over the web UI when a system offers one: it is faster and less error-prone.

## Northwind Ledger (ERP)

- URL: `{erp}` — sign in as `ops.agent` with password `{{secret:erp.password}}`.
- Sessions expire without warning; if you land on the sign-in page, sign in again and continue.
- Dates are displayed and entered as **DD/MM/YYYY** (e.g. 6 October 2026 → `06/10/2026`).
- Customer page: `/erp/customers/<customer id>` shows payments, refunds and invoices. The "Refund"
  link next to a payment opens the refund form.
- Receivables: `/erp/invoices?status=open&page=N` — 10 invoices per page.
- Purchase orders: `/erp/purchase-orders/<PO id>` shows the PO amount and bills already entered against it.
- Bills: `/erp/bills?q=<vendor invoice number>` to search; `/erp/bills/new` to enter one.
  The bill form does **not** detect duplicates — always search first.
- AP holds: `/erp/ap-holds/new`.

## Northwind Desk (helpdesk)

- Web UI: `{desk}` (sign in as `ops.agent`, password `{{secret:desk.password}}`).
- REST API base: `{desk}api` — authentication is added automatically by the `api_request` tool.

| Method & path | Purpose | Body |
|---|---|---|
| `GET /desk/api/tickets?status=open&customer_id=C-1003` | list tickets (filters optional) | — |
| `GET /desk/api/tickets/<id>` | ticket with full conversation | — |
| `POST /desk/api/tickets/<id>/messages` | add a reply (`internal: false`) or internal note (`internal: true`) | `{"body": "...", "internal": false}` |
| `PATCH /desk/api/tickets/<id>` | change status / priority / tags | `{"status": "solved"}` |
| `POST /desk/api/tickets` | open a new (internal) ticket | `{"subject": "...", "body": "...", "customer_id": "C-1002", "tags": ["privacy"]}` |

Ticket statuses: `open` (needs us), `pending` (waiting on someone else), `solved` (done; customer can
reopen), `closed` (archived — do not use).

## PeoplePort (HR)

- URL: `{hr}` — sign in as `ops.agent`, password `{{secret:hr.password}}`.
- New hire form: `/hr/employees/new` (start date uses the date picker, ISO format).
- Access is granted on the employee page (checkboxes). A work email address is generated when the
  `email` entitlement is granted.

## Northwind Mail

- API only: `POST /mail/api/send` with `{"to": [...], "cc": [...], "subject": "...", "body": "...", "attachments": ["reports/file.csv"]}`.
  Attachments are paths on the shared drive. `GET /mail/api/sent?limit=10` lists sent mail.
- Mail to addresses outside `@northwind.example` needs Communications approval.
