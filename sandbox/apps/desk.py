"""Northwind Desk — customer support helpdesk with both a web UI and a REST API."""

from __future__ import annotations

from flask import Blueprint, flash, g, jsonify, make_response, redirect, render_template, request, url_for

from .common import (
    SESSIONS, api_key_required, audit, check_password, cookie_name, get_db, login_required,
    safe_next, stamp,
)

bp = Blueprint("desk", __name__, url_prefix="/desk")
SYSTEM = "desk"
STATUSES = ["open", "pending", "solved", "closed"]


@bp.context_processor
def _ctx():
    return {"system_name": "Northwind Desk", "theme": "desk", "user": getattr(g, "user", None),
            "logout_url": url_for("desk.logout")}


def _ticket(tid: int):
    return get_db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()


def _messages(tid: int):
    return get_db().execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id", (tid,)).fetchall()


def _ticket_json(t, messages=None) -> dict:
    data = {
        "id": t["id"], "customer_id": t["customer_id"], "requester_name": t["requester_name"],
        "requester_email": t["requester_email"], "subject": t["subject"], "status": t["status"],
        "priority": t["priority"], "tags": [x for x in (t["tags"] or "").split(",") if x],
        "created_at": t["created_at"], "updated_at": t["updated_at"],
    }
    if messages is not None:
        data["messages"] = [
            {"id": m["id"], "author": m["author"], "body": m["body"], "internal": bool(m["internal"]),
             "created_at": m["created_at"]}
            for m in messages
        ]
    return data


def _add_message(tid: int, author: str, body: str, internal: bool) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO ticket_messages(ticket_id, author, body, internal, created_at) VALUES (?,?,?,?,?)",
        (tid, author, body, int(internal), stamp()),
    )
    conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (stamp(), tid))
    audit(SYSTEM, author, "ticket.message", str(tid), {"internal": internal, "chars": len(body)})
    return cur.lastrowid


def _set_status(tid: int, status: str, actor: str) -> None:
    get_db().execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, stamp(), tid))
    audit(SYSTEM, actor, "ticket.status", str(tid), {"status": status})


# ---------------------------------------------------------------- web UI

@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if check_password(SYSTEM, username, request.form.get("password", "")):
            sid = SESSIONS.create(SYSTEM, username)
            resp = make_response(redirect(safe_next(request.args.get("next"), url_for("desk.tickets"))))
            resp.set_cookie(cookie_name(SYSTEM), sid, httponly=True, samesite="Lax")
            return resp
        error = "Invalid username or password."
    return render_template("desk/login.html", error=error, expired=request.args.get("expired") == "1")


@bp.route("/logout", methods=["POST"])
def logout():
    SESSIONS.drop(request.cookies.get(cookie_name(SYSTEM)))
    resp = make_response(redirect(url_for("desk.login")))
    resp.delete_cookie(cookie_name(SYSTEM))
    return resp


@bp.route("/")
@login_required(SYSTEM)
def tickets():
    status = request.args.get("status", "")
    sql, args = "SELECT * FROM tickets", []
    if status in STATUSES:
        sql += " WHERE status=?"
        args.append(status)
    rows = get_db().execute(sql + " ORDER BY id DESC", args).fetchall()
    return render_template("desk/tickets.html", tickets=rows, status=status, statuses=STATUSES)


@bp.route("/tickets/<int:tid>")
@login_required(SYSTEM)
def ticket(tid: int):
    t = _ticket(tid)
    if not t:
        return render_template("desk/not_found.html", what=f"Ticket #{tid}"), 404
    return render_template("desk/ticket.html", t=t, messages=_messages(tid), statuses=STATUSES)


@bp.route("/tickets/<int:tid>/reply", methods=["POST"])
@login_required(SYSTEM)
def reply(tid: int):
    if not _ticket(tid):
        return render_template("desk/not_found.html", what=f"Ticket #{tid}"), 404
    body = request.form.get("body", "").strip()
    if not body:
        flash("Reply cannot be empty.", "error")
    else:
        _add_message(tid, g.user, body, request.form.get("internal") == "on")
        get_db().commit()
        flash("Reply added.", "success")
    return redirect(url_for("desk.ticket", tid=tid))


@bp.route("/tickets/<int:tid>/status", methods=["POST"])
@login_required(SYSTEM)
def status(tid: int):
    new = request.form.get("status", "")
    if new not in STATUSES:
        flash("Unknown status.", "error")
    else:
        _set_status(tid, new, g.user)
        get_db().commit()
        flash(f"Ticket #{tid} set to {new}.", "success")
    return redirect(url_for("desk.ticket", tid=tid))


# ---------------------------------------------------------------- REST API

@bp.route("/api/tickets", methods=["GET"])
@api_key_required(SYSTEM)
def api_list():
    unknown = sorted(set(request.args) - {"status", "customer_id"})
    if unknown or (request.args.get("status") and request.args["status"] not in STATUSES):
        return jsonify({"error": "invalid_filter",
                        "message": f"Unsupported filter(s) {unknown or {'status': request.args.get('status')}}. "
                                   f"Supported: status ({' | '.join(STATUSES)}), customer_id (e.g. C-1003). "
                                   "Omit filters to list all tickets."}), 400
    sql, clauses, args = "SELECT * FROM tickets", [], []
    for field in ("status", "customer_id"):
        if request.args.get(field):
            clauses.append(f"{field}=?")
            args.append(request.args[field])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = get_db().execute(sql + " ORDER BY id DESC", args).fetchall()
    return jsonify({"tickets": [_ticket_json(t) for t in rows]})


@bp.route("/api/tickets/<int:tid>", methods=["GET"])
@api_key_required(SYSTEM)
def api_get(tid: int):
    t = _ticket(tid)
    if not t:
        return jsonify({"error": "not_found", "message": f"Ticket {tid} does not exist."}), 404
    return jsonify(_ticket_json(t, _messages(tid)))


@bp.route("/api/tickets/<int:tid>", methods=["PATCH"])
@api_key_required(SYSTEM)
def api_update(tid: int):
    t = _ticket(tid)
    if not t:
        return jsonify({"error": "not_found", "message": f"Ticket {tid} does not exist."}), 404
    data = request.get_json(silent=True) or {}
    unknown = set(data) - {"status", "priority", "tags"}
    if unknown:
        return jsonify({"error": "invalid_field", "message": f"Unsupported fields: {sorted(unknown)}"}), 400
    if "status" in data:
        if data["status"] not in STATUSES:
            return jsonify({"error": "invalid_status", "message": f"status must be one of {STATUSES}"}), 400
        _set_status(tid, data["status"], g.user)
    if "priority" in data:
        get_db().execute("UPDATE tickets SET priority=? WHERE id=?", (str(data["priority"]), tid))
    if "tags" in data:
        tags = data["tags"] if isinstance(data["tags"], list) else [data["tags"]]
        get_db().execute("UPDATE tickets SET tags=? WHERE id=?", (",".join(map(str, tags)), tid))
    get_db().commit()
    return jsonify(_ticket_json(_ticket(tid)))


@bp.route("/api/tickets/<int:tid>/messages", methods=["POST"])
@api_key_required(SYSTEM)
def api_message(tid: int):
    if not _ticket(tid):
        return jsonify({"error": "not_found", "message": f"Ticket {tid} does not exist."}), 404
    data = request.get_json(silent=True) or {}
    body = str(data.get("body", "")).strip()
    if not body:
        return jsonify({"error": "invalid_body", "message": "body is required"}), 400
    mid = _add_message(tid, "Northwind Support (ops.agent)", body, bool(data.get("internal", False)))
    get_db().commit()
    return jsonify({"id": mid, "ticket_id": tid, "internal": bool(data.get("internal", False))}), 201


@bp.route("/api/tickets", methods=["POST"])
@api_key_required(SYSTEM)
def api_create():
    data = request.get_json(silent=True) or {}
    missing = [f for f in ("subject", "body") if not str(data.get(f, "")).strip()]
    if missing:
        return jsonify({"error": "missing_fields", "message": f"Required: {missing}"}), 400
    conn = get_db()
    tid = (conn.execute("SELECT MAX(id) FROM tickets").fetchone()[0] or 1000) + 1
    tags = data.get("tags", [])
    tags = tags if isinstance(tags, list) else [tags]
    conn.execute(
        "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, data.get("customer_id"), data.get("requester_name", "Northwind (internal)"),
         data.get("requester_email", "ops.agent@northwind.example"), data["subject"].strip(), "open",
         data.get("priority", "normal"), ",".join(map(str, tags)), stamp(), stamp()),
    )
    _add_message(tid, "Northwind Support (ops.agent)", data["body"].strip(), bool(data.get("internal", False)))
    audit(SYSTEM, g.user, "ticket.create", str(tid), {"subject": data["subject"]})
    conn.commit()
    return jsonify(_ticket_json(_ticket(tid), _messages(tid))), 201
