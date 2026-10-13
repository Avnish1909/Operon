"""Filesystem locations and network settings for the sandbox company."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = Path(os.environ.get("OPERON_VAR_DIR", ROOT / "var"))
DB_PATH = VAR_DIR / "sandbox.db"
WORKSPACE_DIR = VAR_DIR / "workspace"
VAULT_PATH = VAR_DIR / "vault.json"

HOST = os.environ.get("OPERON_SANDBOX_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPERON_SANDBOX_PORT", "8765"))
BASE_URL = f"http://{HOST}:{PORT}"

COMPANY_DOMAIN = "northwind.example"
