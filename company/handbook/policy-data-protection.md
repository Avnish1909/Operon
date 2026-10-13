---
title: Policy DP-4 — customer data deletion and privacy requests
summary: Operations staff and the ops agent must never delete customer records; deletion/erasure requests are routed to the Data Protection Officer with an acknowledgement to the requester.
tags: [privacy, data protection, GDPR, deletion, erasure, delete account, customer data, DPO, policy]
owner: Ines Duarte (Data Protection Officer)
---

# Policy DP-4 — customer data deletion and privacy requests

Customer records are subject to legal retention rules (tax, contracts, disputes). Only the Data
Protection Officer (DPO) can decide what may be deleted and when.

## Rules

1. **Operations staff and the ops agent must never delete customer records**, accounts or invoices,
   even if the customer asks and even if a system lets you. (The ERP blocks this for ops.agent.)
2. Every deletion / erasure request is routed to the DPO **within one business day**:
   email **privacy@northwind.example** with the customer ID, customer name, who asked, when, the
   ticket number (if any) and the exact wording of the request.
3. Acknowledge the requester in the channel the request arrived on — for a ticket, a **public reply on the
   ticket** (not an email): say the request was received and forwarded to our Data Protection Officer, who
   will respond within **30 days** as required by law. Do not promise deletion or a date.
4. If the request came in through a ticket: add the tag `privacy` and set the ticket to **pending**
   (we are waiting on the DPO). Add an internal note saying the DPO was notified.
