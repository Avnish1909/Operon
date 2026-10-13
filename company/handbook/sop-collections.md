---
title: SOP — overdue receivables report for collections
summary: How to build the overdue receivables (aged debt) report — which invoices count, days overdue, the CSV format and who receives it.
tags: [collections, overdue, receivables, aged debt, invoices, report, csv, finance]
owner: Maria Gomez (Finance Manager)
---

# SOP — overdue receivables report

## Which invoices count

- Customer invoices in the ERP with status **open** only (ignore paid).
- **Days overdue** = business date − due date (in days). The ERP shows due dates as DD/MM/YYYY.
- "Over N days overdue" means strictly more than N days (an invoice exactly N days overdue is not included).
- Look at **every page** of the receivables list.

## Report

1. Save a CSV to the shared drive at `reports/overdue_<N>d_<YYYY-MM-DD>.csv` (business date) with columns:
   `customer_id,customer_name,invoice_id,due_date,days_overdue,amount` — one row per invoice,
   `due_date` in ISO format, `amount` as a plain number (e.g. `2340.00`).
2. Email **collections@northwind.example** with the CSV attached. In the body list each customer with
   their total overdue amount, and the grand total.
