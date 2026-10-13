"""Shared plumbing for the sandbox web systems: DB access, sessions, auth, audit."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime
from functools import wraps
from typing import Callable

from flask import Response, current_app, g, jsonify, redirect, request, url_for

from .. import db as dbmod

HUMAN_USER = ("manager", "northwind")  # demo login for people exploring the sandbox UI


def get_db():
    if "db" not in g:
        g.db = dbmod.connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def vault() -> dict:
    return current_app.config["VAULT"]


def today() -> date:
    return dbmod.get_business_date(get_db())


def stamp() -> str:
    return dbmod.now_stamp(get_db())


def audit(system: str, actor: str, action: str, target: str, details: dict | str = "") -> None:
    if isinstance(details, dict):
        details = json.dumps(details, sort_keys=True)
    get_db().execute(
        "INSERT INTO audit_log(system, actor, action, target, details, at) VALUES (?,?,?,?,?,?)",
        (system, actor, action, target, details, stamp()),
    )


def next_id(table: str, prefix: str, start: int) -> str:
    rows = get_db().execute(f"SELECT id FROM {table} WHERE id LIKE ?", (f"{prefix}-%",)).fetchall()
    nums = [int(r["id"].split("-")[1]) for r in rows if r["id"].split("-")[1].isdigit()]
    return f"{prefix}-{max(nums + [start - 1]) + 1}"


# ---------------------------------------------------------------- formatting

def money(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"${cents / 100:,.2f}"


def uk_date(iso: str | None) -> str:
    """The legacy ERP displays dates as DD/MM/YYYY."""
    if not iso:
        return ""
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def parse_money(text: str) -> int | None:
    cleaned = (text or "").strip().replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(round(value * 100))


# ---------------------------------------------------------------- web sessions

class SessionStore:
    """Server-side sessions so faults can expire them mid-task."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def create(self, system: str, username: str) -> str:
        sid = secrets.token_urlsafe(16)
        with self._lock:
            self._sessions[sid] = {"system": system, "user": username}
        return sid

    def get(self, system: str, sid: str | None) -> str | None:
        if not sid:
            return None
        with self._lock:
            sess = self._sessions.get(sid)
        return sess["user"] if sess and sess["system"] == system else None

    def drop(self, sid: str | None) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


SESSIONS = SessionStore()


def cookie_name(system: str) -> str:
    return f"nw_{system}_sid"


def check_password(system: str, username: str, password: str) -> bool:
    if (username, password) == HUMAN_USER:
        return True
    expected = vault().get(f"{system}.password")
    return username == "ops.agent" and expected is not None and secrets.compare_digest(password, expected)


def login_required(system: str) -> Callable:
    """Decorator for web pages: requires a live session, honours session-expiry faults."""
    from .. import faults

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            sid = request.cookies.get(cookie_name(system))
            user = SESSIONS.get(system, sid)
            if user is None:
                return redirect(url_for(f"{system}.login", next=request.full_path.rstrip("?")))
            if faults.tick_session(system):
                SESSIONS.drop(sid)
                return redirect(url_for(f"{system}.login", next=request.full_path.rstrip("?"), expired=1))
            g.user = user
            return view(*args, **kwargs)

        return wrapper

    return decorator


def api_key_required(system: str) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            key = request.headers.get("X-API-Key", "")
            expected = vault().get(f"{system}.api_key", "")
            if not expected or not secrets.compare_digest(key, expected):
                return jsonify({"error": "unauthorized", "message": "Missing or invalid X-API-Key header."}), 401
            g.user = "ops.agent (api)"
            return view(*args, **kwargs)

        return wrapper

    return decorator


def safe_next(target: str | None, default: str) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return default


def service_unavailable() -> Response:
    html = (
        "<!doctype html><html><head><title>503 Service Unavailable</title></head><body>"
        "<h1>503 Service Temporarily Unavailable</h1>"
        "<p>The server is temporarily unable to handle your request. Please try again in a moment.</p>"
        "</body></html>"
    )
    return Response(html, status=503, mimetype="text/html")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
