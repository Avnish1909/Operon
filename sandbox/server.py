"""App factory, world reset and an in-process server runner for the sandbox company."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import urllib.request
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request
from werkzeug.serving import make_server

from . import db as dbmod
from . import documents, env, faults
from .apps import common, desk, erp, hr, mail


def reset_world(today: date | None = None) -> dict:
    """Recreate the database, shared drive and credentials from scratch."""
    today = today or dbmod.business_date()
    dbmod.create(env.DB_PATH, today).close()
    documents.build_workspace(env.WORKSPACE_DIR, today)
    vault = {
        "erp.password": secrets.token_urlsafe(12),
        "hr.password": secrets.token_urlsafe(12),
        "desk.password": secrets.token_urlsafe(12),
        "desk.api_key": "dk_" + secrets.token_hex(16),
        "mail.api_key": "ml_" + secrets.token_hex(16),
    }
    env.VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    env.VAULT_PATH.write_text(json.dumps(vault, indent=2), encoding="utf-8")
    common.SESSIONS.clear()
    faults.configure()
    return vault


def load_vault() -> dict:
    return json.loads(env.VAULT_PATH.read_text(encoding="utf-8")) if env.VAULT_PATH.exists() else {}


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    app.config.update(DB_PATH=env.DB_PATH, WORKSPACE=env.WORKSPACE_DIR, VAULT=load_vault())

    for bp in (erp.bp, desk.bp, hr.bp, mail.bp):
        app.register_blueprint(bp)
    app.teardown_appcontext(common.close_db)

    @app.before_request
    def _faults():
        faults.apply_latency(request.path)
        if not request.path.startswith("/__admin") and faults.take_transient(request.method, request.path):
            return common.service_unavailable()
        return None

    @app.route("/")
    def home():
        return render_template("home.html", system_name="Northwind intranet", theme="home", user=None)

    @app.route("/__admin/faults", methods=["GET", "POST"])
    def admin_faults():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            faults.configure(body.get("transient"), body.get("session_expiry"), body.get("latency"))
        return jsonify(faults.snapshot())

    @app.route("/__admin/reload-vault", methods=["POST"])
    def admin_reload_vault():
        app.config["VAULT"] = load_vault()
        common.SESSIONS.clear()
        return jsonify({"ok": True})

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith(("/desk/api", "/mail/api")):
            return jsonify({"error": "not_found", "message": f"No route for {request.method} {request.path}"}), 404
        return render_template("not_found.html", what=request.path, system_name="Northwind", theme="home",
                               user=None), 404

    @app.errorhandler(405)
    def not_allowed(_e):
        if request.path.startswith(("/desk/api", "/mail/api")):
            return jsonify({"error": "method_not_allowed", "message": f"{request.method} not allowed on {request.path}"}), 405
        return redirect(request.path)

    return app


class SandboxServer:
    """Runs the sandbox in a background thread of the current process."""

    def __init__(self, host: str = env.HOST, port: int = env.PORT) -> None:
        self.host, self.port = host, port
        self.app = create_app()
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "SandboxServer":
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self._server = make_server(self.host, self.port, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, name="sandbox", daemon=True)
        self._thread.start()
        return self

    def reload_vault(self) -> None:
        self.app.config["VAULT"] = load_vault()
        common.SESSIONS.clear()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None


def is_running(url: str = env.BASE_URL) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/__admin/faults", timeout=1.5) as resp:
            return resp.status == 200
    except OSError:
        return False


def ensure_world() -> None:
    """Create the world on first use."""
    if not env.DB_PATH.exists() or not env.VAULT_PATH.exists() or not Path(env.WORKSPACE_DIR).exists():
        reset_world()
