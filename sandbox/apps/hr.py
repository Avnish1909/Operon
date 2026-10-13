"""PeoplePort — the HR portal: employee records and access provisioning. Web UI only."""

from __future__ import annotations

import re
from datetime import date

from flask import Blueprint, flash, g, make_response, redirect, render_template, request, url_for

from .. import env
from .common import (
    SESSIONS, audit, check_password, cookie_name, get_db, login_required, next_id, safe_next,
    stamp, today,
)

bp = Blueprint("hr", __name__, url_prefix="/hr")
SYSTEM = "hr"
TITLES = ["Sales Associate", "Sales Manager", "Support Agent", "Support Lead", "AP Clerk", "Controller",
          "Finance Manager", "Data Protection Officer", "IT Security Lead", "Communications Lead", "COO"]
DEPARTMENTS = ["Sales", "Customer Support", "Finance", "IT", "Legal", "Marketing", "Operations"]
EMPLOYMENT_TYPES = ["Full-time", "Part-time", "Contractor"]
ENTITLEMENTS = [
    ("email", "Email account"),
    ("crm:sales-user", "CRM — sales user"),
    ("crm:sales-manager", "CRM — sales manager"),
    ("helpdesk:viewer", "Helpdesk — viewer"),
    ("helpdesk:agent", "Helpdesk — agent"),
    ("helpdesk:admin", "Helpdesk — admin"),
    ("erp:readonly", "ERP — read only"),
    ("erp:ap-clerk", "ERP — AP clerk"),
    ("erp:admin", "ERP — administrator"),
]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.context_processor
def _ctx():
    return {"system_name": "PeoplePort", "theme": "hr", "user": getattr(g, "user", None),
            "logout_url": url_for("hr.logout")}


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if check_password(SYSTEM, username, request.form.get("password", "")):
            sid = SESSIONS.create(SYSTEM, username)
            resp = make_response(redirect(safe_next(request.args.get("next"), url_for("hr.directory"))))
            resp.set_cookie(cookie_name(SYSTEM), sid, httponly=True, samesite="Lax")
            return resp
        error = "Invalid username or password."
    return render_template("hr/login.html", error=error, expired=request.args.get("expired") == "1")


@bp.route("/logout", methods=["POST"])
def logout():
    SESSIONS.drop(request.cookies.get(cookie_name(SYSTEM)))
    resp = make_response(redirect(url_for("hr.login")))
    resp.delete_cookie(cookie_name(SYSTEM))
    return resp


@bp.route("/")
@login_required(SYSTEM)
def directory():
    rows = get_db().execute(
        "SELECT e.*, m.full_name AS manager_name FROM employees e LEFT JOIN employees m ON m.id=e.manager_id "
        "ORDER BY e.id").fetchall()
    return render_template("hr/directory.html", employees=rows)


@bp.route("/employees/<eid>")
@login_required(SYSTEM)
def employee(eid: str):
    conn = get_db()
    e = conn.execute(
        "SELECT e.*, m.full_name AS manager_name FROM employees e LEFT JOIN employees m ON m.id=e.manager_id "
        "WHERE e.id=?", (eid,)).fetchone()
    if not e:
        return render_template("hr/not_found.html", what=f"Employee {eid}"), 404
    granted = [r["entitlement"] for r in conn.execute(
        "SELECT entitlement FROM access_grants WHERE employee_id=? ORDER BY id", (eid,))]
    available = [(code, label) for code, label in ENTITLEMENTS if code not in granted]
    return render_template("hr/employee.html", e=e, granted=granted, available=available,
                           labels=dict(ENTITLEMENTS))


@bp.route("/employees/new", methods=["GET", "POST"])
@login_required(SYSTEM)
def employee_new():
    conn = get_db()
    managers = conn.execute("SELECT id, full_name, title FROM employees WHERE status='active' ORDER BY full_name").fetchall()
    errors: list[str] = []
    fields = ("full_name", "personal_email", "title", "department", "manager_id", "start_date", "employment_type")
    form = {k: "" for k in fields}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in fields}
        if len(form["full_name"].split()) < 2:
            errors.append("Full name must include first and last name.")
        if not _EMAIL.match(form["personal_email"]):
            errors.append("A valid personal email address is required.")
        if form["title"] not in TITLES:
            errors.append("Please choose a job title.")
        if form["department"] not in DEPARTMENTS:
            errors.append("Please choose a department.")
        if not conn.execute("SELECT 1 FROM employees WHERE id=?", (form["manager_id"],)).fetchone():
            errors.append("Please choose a manager.")
        try:
            start = date.fromisoformat(form["start_date"])
            if start < today():
                errors.append("Start date cannot be in the past.")
        except ValueError:
            errors.append("Start date is required.")
        if form["employment_type"] not in EMPLOYMENT_TYPES:
            errors.append("Please choose an employment type.")
        dup = conn.execute("SELECT id FROM employees WHERE lower(full_name)=lower(?)", (form["full_name"],)).fetchone()
        if dup:
            errors.append(f"An employee named {form['full_name']} already exists ({dup['id']}).")
        if not errors:
            eid = next_id("employees", "E", 205)
            conn.execute(
                "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, form["full_name"], form["personal_email"], None, form["title"], form["department"],
                 form["manager_id"], form["start_date"], form["employment_type"], "pre-start", g.user, stamp()),
            )
            audit(SYSTEM, g.user, "employee.create", eid, {k: form[k] for k in ("full_name", "title", "start_date")})
            conn.commit()
            flash(f"Employee record {eid} created for {form['full_name']}.", "success")
            return redirect(url_for("hr.employee", eid=eid))
    return render_template("hr/employee_new.html", form=form, errors=errors, titles=TITLES,
                           departments=DEPARTMENTS, managers=managers, types=EMPLOYMENT_TYPES)


@bp.route("/employees/<eid>/access", methods=["POST"])
@login_required(SYSTEM)
def grant_access(eid: str):
    conn = get_db()
    e = conn.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone()
    if not e:
        return render_template("hr/not_found.html", what=f"Employee {eid}"), 404
    requested = [x for x in request.form.getlist("entitlements") if x in dict(ENTITLEMENTS)]
    if not requested:
        flash("Select at least one entitlement to grant.", "error")
        return redirect(url_for("hr.employee", eid=eid))
    existing = {r["entitlement"] for r in conn.execute("SELECT entitlement FROM access_grants WHERE employee_id=?", (eid,))}
    added = [x for x in requested if x not in existing]
    for ent in added:
        conn.execute(
            "INSERT INTO access_grants(employee_id, entitlement, granted_by, granted_at) VALUES (?,?,?,?)",
            (eid, ent, g.user, stamp()),
        )
    if "email" in added and not e["work_email"]:
        work = e["full_name"].lower().replace(" ", ".") + f"@{env.COMPANY_DOMAIN}"
        conn.execute("UPDATE employees SET work_email=? WHERE id=?", (work, eid))
    audit(SYSTEM, g.user, "access.grant", eid, {"entitlements": added})
    conn.commit()
    flash(f"Granted: {', '.join(added) or 'nothing new'}.", "success")
    return redirect(url_for("hr.employee", eid=eid))
