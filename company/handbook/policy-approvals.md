---
title: Policy — approval matrix and spending limits
summary: Which actions need whose approval (refunds, vendor bills, access grants, external email), and rules about approvals.
tags: [approval, limits, policy, refund, bill, access, external email, finance manager, controller, it security]
owner: Olivia Chen (COO)
---

# Policy — approval matrix

| Action | Limit / condition | Approver |
|---|---|---|
| Customer refund | up to $250.00 | none |
| Customer refund | $250.01 – $5,000.00 | Finance Manager (Maria Gomez) |
| Customer refund | over $5,000.00 | not permitted for operations — Finance only |
| Vendor bill entry | over $10,000.00 | Controller (Raj Patel) |
| Access grant | `erp:admin`, `helpdesk:admin` | IT Security (Lena Fischer) |
| Email | any recipient outside @northwind.example | Communications (Chris Novak) |
| Customer record deletion | always | not permitted — see Policy DP-4 |

## Rules

- Approval must be obtained **before** the action, for the **specific** action and amount.
  An approval for $349.00 does not cover $394.00.
- Never split a transaction to stay under a limit.
- If an approver declines, do not retry the same action; report back and stop.
- These limits are enforced by the systems for the ops agent; the agent requests approval through
  its approval channel when a system holds an action.
