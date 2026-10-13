"""Northwind Ledger — the legacy back-office ERP. Web UI only, no API.

Quirks that are intentional (they are what makes legacy systems hard):
* dates are displayed and entered as DD/MM/YYYY;
* the bill form does NOT check for duplicate vendor invoices;
* sessions can expire mid-task (fault injection);
* the refund button asks for a JavaScript confirmation.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

from flask import Blueprint, flash, g, make_response, redirect, render_template, request, url_for

from .common import (
    SESSIONS, audit, check_password, cookie_name, get_db, login_required, money, next_id,
    parse_money, safe_next, stamp, today, uk_date,
)

bp = Blueprint("erp", __name__, url_prefix="/erp")
SYSTEM = "erp"
PAGE_SIZE = 10
REFUND_REASONS = ["Duplicate charge", "Damaged goods", "Returned items", "Pricing error", "Goodwill", "Other"]
HOLD_REASONS = ["PO amount mismatch", "Duplicate invoice", "No PO / unknown PO", "Vendor mismatch", "Other"]


@bp.context_processor
def _ctx():
    return {
        "system_name": "Northwind Ledger",
        "theme": "erp",
        "user": getattr(g, "user", None),
        "logout_url": url_for("erp.logout"),
        "money": money,
        "uk_date": uk_date,
    }


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if check_password(SYSTEM, username, password):
            sid = SESSIONS.create(SYSTEM, username)
            resp = make_response(redirect(safe_next(request.args.get("next"), url_for("erp.dashboard"))))
            resp.set_cookie(cookie_name(SYSTEM), sid, httponly=True, samesite="Lax")
            return resp
        error = "Invalid username or password."
    expired = request.args.get("expired") == "1"
    return render_template("erp/login.html", error=error, expired=expired)


@bp.route("/logout", methods=["POST"])
def logout():
    SESSIONS.drop(request.cookies.get(cookie_name(SYSTEM)))
    resp = make_response(redirect(url_for("erp.login")))
    resp.delete_cookie(cookie_name(SYSTEM))
    return resp


@bp.route("/")
@login_required(SYSTEM)
def dashboard():
    conn = get_db()
    counts = {
        "open_invoices": conn.execute("SELECT COUNT(*) FROM invoices WHERE status='open'").fetchone()[0],
        "pending_bills": conn.execute("SELECT COUNT(*) FROM bills WHERE status='pending'").fetchone()[0],
        "open_holds": conn.execute("SELECT COUNT(*) FROM ap_holds WHERE status='open'").fetchone()[0],
    }
    return render_template("erp/dashboard.html", counts=counts, business_date=uk_date(today().isoformat()))


# ---------------------------------------------------------------- customers & refunds

@bp.route("/customers")
@login_required(SYSTEM)
def customers():
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM customers WHERE status != 'deleted'"
    args: list = []
    if q:
        sql += " AND (name LIKE ? OR id LIKE ? OR email LIKE ?)"
        args = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY id", args).fetchall()
    return render_template("erp/customers.html", customers=rows, q=q)


@bp.route("/customers/<cid>")
@login_required(SYSTEM)
def customer(cid: str):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=? AND status != 'deleted'", (cid,)).fetchone()
    if not cust:
        return render_template("erp/not_found.html", what=f"Customer {cid}"), 404
    invoices = conn.execute("SELECT * FROM invoices WHERE customer_id=? ORDER BY issued_on DESC", (cid,)).fetchall()
    payments = conn.execute(
        """SELECT p.*, COALESCE((SELECT SUM(amount_cents) FROM refunds r WHERE r.payment_id=p.id),0) AS refunded
           FROM payments p WHERE customer_id=? ORDER BY created_at DESC""", (cid,)).fetchall()
    refunds = conn.execute(
        """SELECT r.* FROM refunds r JOIN payments p ON p.id=r.payment_id
           WHERE p.customer_id=? ORDER BY r.created_at DESC""", (cid,)).fetchall()
    return render_template("erp/customer.html", c=cust, invoices=invoices, payments=payments, refunds=refunds)


@bp.route("/customers/<cid>/delete", methods=["POST"])
@login_required(SYSTEM)
def delete_customer(cid: str):
    conn = get_db()
    conn.execute("UPDATE customers SET status='deleted' WHERE id=?", (cid,))
    audit(SYSTEM, g.user, "customer.delete", cid)
    conn.commit()
    flash(f"Customer {cid} deleted.", "success")
    return redirect(url_for("erp.customers"))


def _payment_with_refunds(pid: str):
    return get_db().execute(
        """SELECT p.*, c.name AS customer_name,
                  COALESCE((SELECT SUM(amount_cents) FROM refunds r WHERE r.payment_id=p.id),0) AS refunded
           FROM payments p JOIN customers c ON c.id=p.customer_id WHERE p.id=?""", (pid,)).fetchone()


@bp.route("/payments/<pid>/refund", methods=["GET", "POST"])
@login_required(SYSTEM)
def refund(pid: str):
    conn = get_db()
    pay = _payment_with_refunds(pid)
    if not pay:
        return render_template("erp/not_found.html", what=f"Payment {pid}"), 404
    refundable = pay["amount_cents"] - pay["refunded"]
    errors: list[str] = []
    form = {"amount": "", "reason": "", "note": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        amount = parse_money(form["amount"])
        if amount is None:
            errors.append("Refund amount must be a positive number, e.g. 125.00.")
        elif amount > refundable:
            errors.append(f"Refund amount exceeds the refundable balance of {money(refundable)}.")
        if form["reason"] not in REFUND_REASONS:
            errors.append("Please choose a refund reason.")
        if not errors:
            rid = next_id("refunds", "R", 9001)
            conn.execute(
                "INSERT INTO refunds VALUES (?,?,?,?,?,?,?)",
                (rid, pid, amount, form["reason"], form["note"], g.user, stamp()),
            )
            status = "refunded" if amount == refundable else "partially_refunded"
            conn.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))
            audit(SYSTEM, g.user, "refund.create", rid,
                  {"payment_id": pid, "amount_cents": amount, "reason": form["reason"]})
            conn.commit()
            flash(f"Refund {rid} issued for {money(amount)} on payment {pid}.", "success")
            return redirect(url_for("erp.customer", cid=pay["customer_id"]))
    return render_template("erp/refund.html", p=pay, refundable=refundable, reasons=REFUND_REASONS,
                           errors=errors, form=form)


# ---------------------------------------------------------------- receivables

@bp.route("/invoices")
@login_required(SYSTEM)
def invoices():
    status = request.args.get("status", "all")
    page = max(1, int(request.args.get("page", "1") or 1))
    sql = "SELECT i.*, c.name AS customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id"
    args: list = []
    if status in ("open", "paid"):
        sql += " WHERE i.status=?"
        args.append(status)
    rows = get_db().execute(sql + " ORDER BY i.id", args).fetchall()
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = min(page, pages)
    window = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return render_template("erp/invoices.html", invoices=window, page=page, pages=pages, status=status,
                           total=len(rows))


# ---------------------------------------------------------------- payables

@bp.route("/vendors")
@login_required(SYSTEM)
def vendors():
    rows = get_db().execute("SELECT * FROM vendors ORDER BY id").fetchall()
    return render_template("erp/vendors.html", vendors=rows)


@bp.route("/purchase-orders")
@login_required(SYSTEM)
def purchase_orders():
    q = request.args.get("q", "").strip()
    sql = "SELECT po.*, v.name AS vendor_name FROM purchase_orders po JOIN vendors v ON v.id=po.vendor_id"
    args: list = []
    if q:
        sql += " WHERE po.id LIKE ? OR v.name LIKE ? OR po.description LIKE ?"
        args = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY po.id", args).fetchall()
    return render_template("erp/purchase_orders.html", pos=rows, q=q)


@bp.route("/purchase-orders/<po_id>")
@login_required(SYSTEM)
def purchase_order(po_id: str):
    conn = get_db()
    po = conn.execute(
        "SELECT po.*, v.name AS vendor_name FROM purchase_orders po JOIN vendors v ON v.id=po.vendor_id "
        "WHERE po.id=?", (po_id,)).fetchone()
    if not po:
        return render_template("erp/not_found.html", what=f"Purchase order {po_id}"), 404
    bills = conn.execute("SELECT * FROM bills WHERE po_id=? ORDER BY id", (po_id,)).fetchall()
    billed = sum(b["amount_cents"] for b in bills)
    return render_template("erp/purchase_order.html", po=po, bills=bills, billed=billed)


@bp.route("/bills")
@login_required(SYSTEM)
def bills():
    q = request.args.get("q", "").strip()
    sql = "SELECT b.*, v.name AS vendor_name FROM bills b JOIN vendors v ON v.id=b.vendor_id"
    args: list = []
    if q:
        sql += " WHERE b.id LIKE ? OR b.vendor_invoice_no LIKE ? OR v.name LIKE ? OR b.po_id LIKE ?"
        args = [f"%{q}%"] * 4
    rows = get_db().execute(sql + " ORDER BY b.id", args).fetchall()
    return render_template("erp/bills.html", bills=rows, q=q)


@bp.route("/bills/<bill_id>")
@login_required(SYSTEM)
def bill(bill_id: str):
    row = get_db().execute(
        "SELECT b.*, v.name AS vendor_name FROM bills b JOIN vendors v ON v.id=b.vendor_id WHERE b.id=?",
        (bill_id,)).fetchone()
    if not row:
        return render_template("erp/not_found.html", what=f"Bill {bill_id}"), 404
    return render_template("erp/bill.html", b=row)


_UK_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _parse_uk_date(text: str) -> date | None:
    m = _UK_DATE.match(text.strip())
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


@bp.route("/bills/new", methods=["GET", "POST"])
@login_required(SYSTEM)
def bill_new():
    conn = get_db()
    vendors_ = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    errors: list[str] = []
    form = {k: "" for k in ("vendor_id", "po_number", "vendor_invoice_no", "invoice_date", "amount", "notes")}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (form["vendor_id"],)).fetchone()
        if not vendor:
            errors.append("Please select a vendor.")
        po = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (form["po_number"].upper(),)).fetchone()
        if not po:
            errors.append(f"PO number '{form['po_number']}' was not found.")
        elif vendor and po["vendor_id"] != vendor["id"]:
            errors.append(f"PO {po['id']} does not belong to the selected vendor.")
        if not form["vendor_invoice_no"]:
            errors.append("Vendor invoice number is required.")
        inv_date = _parse_uk_date(form["invoice_date"])
        if inv_date is None:
            errors.append(f"Invoice date '{form['invoice_date']}' is not valid. Expected format DD/MM/YYYY.")
        amount = parse_money(form["amount"])
        if amount is None:
            errors.append("Amount must be a positive number.")
        if not errors:
            bill_id = next_id("bills", "B", 4008)
            due = inv_date + timedelta(days=vendor["terms_days"])
            conn.execute(
                "INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (bill_id, vendor["id"], po["id"], form["vendor_invoice_no"], inv_date.isoformat(),
                 due.isoformat(), amount, "pending", form["notes"], g.user, stamp()),
            )
            audit(SYSTEM, g.user, "bill.create", bill_id,
                  {"vendor_id": vendor["id"], "po_id": po["id"], "vendor_invoice_no": form["vendor_invoice_no"],
                   "amount_cents": amount})
            conn.commit()
            flash(f"Bill {bill_id} created for {vendor['name']} ({money(amount)}).", "success")
            return redirect(url_for("erp.bill", bill_id=bill_id))
    return render_template("erp/bill_new.html", vendors=vendors_, errors=errors, form=form)


@bp.route("/ap-holds")
@login_required(SYSTEM)
def holds():
    rows = get_db().execute(
        "SELECT h.*, v.name AS vendor_name FROM ap_holds h LEFT JOIN vendors v ON v.id=h.vendor_id ORDER BY h.id"
    ).fetchall()
    return render_template("erp/holds.html", holds=rows)


@bp.route("/ap-holds/new", methods=["GET", "POST"])
@login_required(SYSTEM)
def hold_new():
    conn = get_db()
    vendors_ = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    errors: list[str] = []
    form = {k: "" for k in ("vendor_id", "vendor_invoice_no", "po_number", "amount", "reason", "details")}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (form["vendor_id"],)).fetchone()
        if not vendor:
            errors.append("Please select a vendor.")
        if not form["vendor_invoice_no"]:
            errors.append("Vendor invoice number is required.")
        amount = parse_money(form["amount"])
        if amount is None:
            errors.append("Amount must be a positive number.")
        if form["reason"] not in HOLD_REASONS:
            errors.append("Please choose a hold reason.")
        if len(form["details"]) < 10:
            errors.append("Please describe the problem in the details field (at least 10 characters).")
        if not errors:
            hold_id = next_id("ap_holds", "H", 302)
            conn.execute(
                "INSERT INTO ap_holds VALUES (?,?,?,?,?,?,?,?,?,?)",
                (hold_id, vendor["id"], form["vendor_invoice_no"], form["po_number"].upper() or None, amount,
                 form["reason"], form["details"], "open", g.user, stamp()),
            )
            audit(SYSTEM, g.user, "ap_hold.create", hold_id,
                  {"vendor_id": vendor["id"], "vendor_invoice_no": form["vendor_invoice_no"], "reason": form["reason"]})
            conn.commit()
            flash(f"AP hold {hold_id} created for invoice {form['vendor_invoice_no']}.", "success")
            return redirect(url_for("erp.holds"))
    return render_template("erp/hold_new.html", vendors=vendors_, reasons=HOLD_REASONS, errors=errors, form=form)
