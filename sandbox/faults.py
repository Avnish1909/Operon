"""Fault injection: makes the sandbox behave like real, imperfect systems.

Scenarios switch these on to check that the operator recovers instead of
stalling or double-submitting:

* ``transient``      — the first N GET requests under a path prefix return 503.
* ``session_expiry`` — a system's web session dies after N authenticated requests.
* ``latency``        — requests under a path prefix are slowed down.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class FaultState:
    transient: dict[str, int] = field(default_factory=dict)        # path prefix -> remaining 503s
    session_expiry: dict[str, int] = field(default_factory=dict)   # system -> requests until expiry
    latency: dict[str, float] = field(default_factory=dict)        # path prefix -> seconds
    log: list[str] = field(default_factory=list)


_lock = threading.Lock()
_state = FaultState()


def configure(transient: dict[str, int] | None = None,
              session_expiry: dict[str, int] | None = None,
              latency: dict[str, float] | None = None) -> None:
    global _state
    with _lock:
        _state = FaultState(
            transient=dict(transient or {}),
            session_expiry=dict(session_expiry or {}),
            latency=dict(latency or {}),
        )


def snapshot() -> dict:
    with _lock:
        return {
            "transient": dict(_state.transient),
            "session_expiry": dict(_state.session_expiry),
            "latency": dict(_state.latency),
            "log": list(_state.log),
        }


def take_transient(method: str, path: str) -> bool:
    """True if this request should fail with a 503 (consumes one injected failure)."""
    if method != "GET":
        return False
    with _lock:
        for prefix, remaining in _state.transient.items():
            if remaining > 0 and path.startswith(prefix):
                _state.transient[prefix] = remaining - 1
                _state.log.append(f"503 injected on {path}")
                return True
    return False


def tick_session(system: str) -> bool:
    """Count an authenticated request; True means the session must be expired now."""
    with _lock:
        if system not in _state.session_expiry:
            return False
        _state.session_expiry[system] -= 1
        if _state.session_expiry[system] <= 0:
            del _state.session_expiry[system]
            _state.log.append(f"session expired for {system}")
            return True
    return False


def apply_latency(path: str) -> None:
    with _lock:
        delay = max((s for p, s in _state.latency.items() if path.startswith(p)), default=0.0)
    if delay:
        time.sleep(delay)
