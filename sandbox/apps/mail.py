"""Northwind Mail — internal mail relay. API for sending; a read-only web view of sent mail."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, render_template, request

from .common import api_key_required, audit, get_db, next_id, stamp

bp = Blueprint("mail", __name__, url_prefix="/mail")
SYSTEM = "mail"
SENDER = "ops.agent@northwind.example"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _email_json(row) -> dict:
    return {
        "id": row["id"], "from": row["sender"], "to": json.loads(row["recipients"]), "cc": json.loads(row["cc"]),
        "subject": row["subject"], "body": row["body"], "attachments": json.loads(row["attachments"]),
        "sent_at": row["sent_at"],
    }


@bp.route("/api/send", methods=["POST"])
@api_key_required(SYSTEM)
def api_send():
    data = request.get_json(silent=True) or {}
    to, cc = _as_list(data.get("to")), _as_list(data.get("cc"))
    subject, body = str(data.get("subject", "")).strip(), str(data.get("body", "")).strip()
    errors = []
    if not to:
        errors.append("'to' must contain at least one recipient")
    bad = [a for a in to + cc if not _EMAIL.match(a)]
    if bad:
        errors.append(f"invalid addresses: {bad}")
    if not subject:
        errors.append("'subject' is required")
    if not body:
        errors.append("'body' is required")

    attachments = []
    workspace: Path = current_app.config["WORKSPACE"]
    for rel in _as_list(data.get("attachments")):
        path = (workspace / rel).resolve()
        if not path.is_relative_to(workspace.resolve()) or not path.is_file():
            errors.append(f"attachment not found on the shared drive: {rel}")
            continue
        content = path.read_bytes()
        attachments.append({"path": rel, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()[:16]})
    if errors:
        return jsonify({"error": "invalid_request", "message": "; ".join(errors)}), 400

    conn = get_db()
    mid = next_id("emails", "M", 2)
    conn.execute(
        "INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)",
        (mid, SENDER, json.dumps(to), json.dumps(cc), subject, body, json.dumps(attachments), stamp()),
    )
    audit(SYSTEM, g.user, "mail.send", mid, {"to": to, "cc": cc, "subject": subject})
    conn.commit()
    return jsonify(_email_json(conn.execute("SELECT * FROM emails WHERE id=?", (mid,)).fetchone())), 201


@bp.route("/api/sent", methods=["GET"])
@api_key_required(SYSTEM)
def api_sent():
    limit = min(int(request.args.get("limit", "20") or 20), 100)
    rows = get_db().execute("SELECT * FROM emails ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return jsonify({"emails": [_email_json(r) for r in rows]})


@bp.route("/")
def outbox():
    rows = get_db().execute("SELECT * FROM emails ORDER BY id DESC").fetchall()
    return render_template("mail/outbox.html", emails=[_email_json(r) for r in rows],
                           system_name="Northwind Mail", theme="mail", user=None)
