---
title: SOP — customer refunds and billing disputes
summary: How to investigate a billing complaint (duplicate charge, damaged goods), issue a refund in the ERP, approval limits, and how to close the loop with the customer.
tags: [refund, billing, duplicate charge, double charged, damaged goods, ticket, finance approval]
owner: Maria Gomez (Finance Manager)
---

# SOP — customer refunds and billing disputes

## 1. Investigate

1. Read the customer's request (ticket in Northwind Desk, or the request you were given).
2. Find the customer in the ERP and open their customer page. Identify the payment(s) involved.
3. **Duplicate charge** — it is a duplicate only if *all* of these hold:
   - two (or more) captured payments reference the **same invoice**,
   - for the **same amount**,
   - within **48 hours** of each other.
   Payments of the same amount against *different* invoices are not duplicates (e.g. monthly bundles).
   Refund the **later** payment in full; keep the earliest one.
4. **Damaged / returned goods** — refund the amount the customer is entitled to (full payment unless
   told otherwise). If the request does not make clear which payment or amount, ask the requester.
5. If the evidence does not support the request, do not refund. Reply to the customer explaining what
   you found and set the ticket to `pending`.

## 2. Issue the refund (ERP)

- Use the "Refund" link next to the payment on the customer page.
- Amount: the refund amount in USD (e.g. `349.00`). Reason: pick the matching reason
  ("Duplicate charge", "Damaged goods", ...). Internal note: reference the ticket number if there is one.
- **Approval limits** (enforced by the system):
  - up to $250.00 — no approval needed;
  - $250.01 to $5,000.00 — Finance Manager approval required before the refund is submitted;
  - over $5,000.00 — not allowed for operations staff; escalate to Finance.
- After submitting, confirm on the customer page that the refund appears with its refund ID (R-xxxx).
  Never submit a second refund for the same payment "to be sure".

## 3. Close the loop

If the request came from a ticket:

1. Post a **public reply** to the customer that states the refund amount, the refund ID and that it
   takes **5–10 business days** to appear on their statement. Sign it "Northwind Support".
2. Post an **internal note** with the refund ID, payment ID and who approved it (if approval was needed).
3. Set the ticket status to **solved**.
