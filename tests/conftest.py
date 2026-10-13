"""Test fixtures. Each test session gets its own sandbox state directory and port."""

from __future__ import annotations

import os
import socket
import tempfile

# Must be set before sandbox/operon modules compute their paths.
os.environ["OPERON_VAR_DIR"] = tempfile.mkdtemp(prefix="operon-test-var-")
os.environ["OPERON_RUNS_DIR"] = tempfile.mkdtemp(prefix="operon-test-runs-")
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    os.environ["OPERON_SANDBOX_PORT"] = str(_s.getsockname()[1])

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from operon.config import Settings  # noqa: E402
from sandbox import env, server  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def world():
    """A freshly reset sandbox company (database, shared drive, credentials)."""
    vault = server.reset_world()
    return vault


@pytest.fixture(scope="session")
def live_server():
    """The sandbox served over HTTP in a background thread (for browser/API tests)."""
    server.reset_world()
    srv = server.SandboxServer(env.HOST, env.PORT).start()
    yield srv
    srv.stop()


@pytest.fixture()
def live_world(live_server):
    """Reset the world and make the running server pick up the new credentials."""
    vault = server.reset_world()
    live_server.reload_vault()
    return vault


@pytest.fixture(scope="session")
def browser_host():
    from operon.tools.browser import BrowserHost

    try:
        host = BrowserHost(headless=True)
        host.browser()
    except Exception as exc:  # pragma: no cover - environment without Chromium
        pytest.skip(f"Chromium not available: {exc}")
    yield host
    host.close()


@pytest.fixture()
def company_dir(settings) -> Path:
    return settings.company_dir
