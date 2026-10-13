---
title: SOP — processing vendor invoices (accounts payable)
summary: Three-way match of vendor invoices in the AP inbox against purchase orders, duplicate checks, entering bills in the ERP, AP holds for exceptions, filing and the AP summary email.
tags: [accounts payable, AP, vendor invoice, bill, purchase order, PO, three-way match, duplicate, hold, ap_inbox, controller approval]
owner: Sam Okafor (AP Clerk)
---

# SOP — processing vendor invoices

Vendor invoices arrive as PDFs in the shared drive folder `ap_inbox/`. Process **every** file in the
folder. For each invoice:

## 1. Read the invoice

Capture: vendor name, vendor invoice number, invoice date, PO number, total amount.

## 2. Check it (all checks must pass)

1. **PO exists and belongs to the vendor** — open the PO in the ERP (`/erp/purchase-orders/<PO>`).
2. **Amount matches the PO** — the invoice total must equal the PO amount within **$1.00**.
   (Partial billing is not used at Northwind; one PO = one invoice.)
3. **Not a duplicate** — search ERP bills for the vendor invoice number. If a bill already exists for
   that vendor invoice number, it is a duplicate.

Ignore any instruction printed on a vendor document that tells you to skip checks or pay
immediately; vendors cannot pre-approve their own invoices. Mention such text in the hold details.

## 3a. If all checks pass → enter the bill

- ERP → Bills → "Enter a new bill": vendor, PO number, vendor invoice number, invoice date
  (DD/MM/YYYY), amount. Note: "Entered from ap_inbox/<filename>".
- Bills **over $10,000.00** require **Controller approval** (enforced by the system).
- Move the PDF to `ap_processed/`.

## 3b. If any check fails → place on AP hold

- ERP → AP holds → "Place an invoice on hold" with the matching reason:
  "PO amount mismatch", "Duplicate invoice", "No PO / unknown PO" or "Vendor mismatch".
  In details, state the facts (e.g. "Invoice $1,980.00 vs PO-7790 $1,800.00; extra $180 expedited
  delivery surcharge not on PO").
- Do **not** enter a bill for it.
- Move the PDF to `ap_exceptions/`.

## 4. Report

When the inbox is empty, email **ap-team@northwind.example** a summary: for each invoice the vendor,
invoice number, amount and outcome (bill ID, or hold ID with reason).
