"""API capability: call a company system's REST API (preferred over the UI when available).

Authentication is injected from the vault; the model never sees keys. Writes go
through the same policy engine as browser submissions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..company import SystemRegistry, Vault
from ..policy import ActionRequest, PolicyEngine
from .base import Tool, ToolError, ToolResult, TransientError, schema

MAX_CHARS = 12000


class ApiClient:
    def __init__(self, registry: SystemRegistry, policy: PolicyEngine, vault: Vault, read_only: bool = False) -> None:
        self.registry, self.policy, self.vault, self.read_only = registry, policy, vault, read_only

    def call(self, system: str, method: str, path: str, body: dict | None = None,
             query: dict | None = None) -> ToolResult:
        method = method.upper()
        sys_ = self.registry.systems.get(system)
        if sys_ is None or not sys_.api_base:
            with_api = [s.key for s in self.registry.systems.values() if s.api_base]
            raise ToolError(f"System '{system}' has no API. Systems with an API: {with_api}. "
                            "Use the browser for the others.")
        api_path = urllib.parse.urlparse(sys_.api_base).path
        if not path.startswith("/"):
            path = api_path + path
        if not path.startswith(api_path):
            raise ToolError(f"Path must be under {api_path} for system '{system}'.")
        url = self.registry.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})

        decision = None
        if method != "GET":
            if self.read_only:
                raise ToolError("This session is read-only: only GET requests are allowed.")
            decision = self.policy.evaluate(ActionRequest(method, path, body or {}, {}, via="api"))
            if not decision.allowed:
                hint = "\n→ Nothing was sent."
                known = self.policy.known_actions(api_path) if decision.action_id == "unknown" else []
                if known:
                    hint += ("\n→ This endpoint is not a documented action of this API — it is probably the wrong "
                             "path. Documented write endpoints:\n" + "\n".join(f"   {k}" for k in known)
                             + "\n  If one of these is what you meant, call it instead.")
                if decision.outcome == "require_approval":
                    hint += (f"\n→ {'Only if you really need this exact request: ' if known else ''}call "
                             f"request_approval(pending_id='{decision.pending_id}', justification=...), then repeat "
                             "this exact request.")
                elif decision.outcome == "deny":
                    hint += "\n→ This action is not permitted; do not attempt it another way."
                return ToolResult(decision.describe() + hint, is_error=True,
                                  data={"decisions": [{"outcome": decision.outcome, "action": decision.action_id,
                                                       "summary": decision.summary,
                                                       "pending_id": decision.pending_id}]})

        data = json.dumps(body).encode() if body is not None and method != "GET" else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if sys_.api_auth_header and sys_.api_secret:
            headers[sys_.api_auth_header] = self.vault.get(sys_.api_secret)
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                status, raw = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            if method == "GET":
                raise TransientError(f"Could not reach {system} API: {exc}") from exc
            raise ToolError(f"Could not reach {system} API: {exc}. The request may not have been sent — "
                            "check the current state before retrying.") from exc

        if decision is not None and decision.grant_id and not 200 <= status < 300:
            self.policy.restore_grant(decision.grant_id)
        if status >= 500 and method == "GET":
            raise TransientError(f"{system} API returned HTTP {status}")
        try:
            pretty = json.dumps(json.loads(raw), indent=1, ensure_ascii=False)
        except json.JSONDecodeError:
            pretty = raw
        pretty = self.vault.redact(pretty)
        if len(pretty) > MAX_CHARS:
            pretty = pretty[:MAX_CHARS] + f"\n… (truncated {len(pretty) - MAX_CHARS} characters)"
        prefix = f"HTTP {status} {method} {path}"
        if decision is not None:
            prefix = decision.describe() + "\n" + prefix
        out = {"decisions": [{"outcome": decision.outcome, "action": decision.action_id, "summary": decision.summary,
                              "pending_id": None}]} if decision else {}
        return ToolResult(f"{prefix}\n{pretty}", is_error=status >= 400, data=out)


def api_tools(client: ApiClient) -> list[Tool]:
    systems = [s for s in client.registry.systems.values() if s.api_base]
    listing = "; ".join(f"{s.key} ({s.name}) at {urllib.parse.urlparse(s.api_base).path}" for s in systems)
    return [
        Tool(
            "api_request",
            "Call a company system's REST API. Prefer this over the browser for systems that have an API. "
            f"Systems with APIs: {listing}. Authentication is added automatically. Endpoints are documented "
            "in the systems guide (read_document 'systems-guide'). Write requests are checked against company policy.",
            schema({
                "system": {"type": "string", "enum": [s.key for s in systems]},
                "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"]},
                "path": {"type": "string", "description": "Full path, e.g. /desk/api/tickets/1042"},
                "body": {"type": "object", "description": "JSON body for POST/PATCH/PUT"},
                "query": {"type": "object", "description": "Query-string parameters for GET"},
            }, ["system", "method", "path"]),
            lambda a: client.call(a["system"], a["method"], a["path"], a.get("body"), a.get("query")),
            # GETs are retried by the dispatcher on transient errors; writes never are.
            idempotent=lambda a: str(a.get("method", "")).upper() == "GET",
        ),
    ]
