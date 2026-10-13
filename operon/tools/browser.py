"""Browser capability: operate company web apps like a person would, with guardrails.

Perception: snapshot.js renders the page as structured text with element refs.
Action: click / fill / press by ref.
Guardrails (enforced here, not by the model):
* egress allowlist — requests to anything that is not a registered company system are aborted;
* every state-changing request is classified by the policy engine at the network layer.
  Held or denied requests are never sent: form navigations get a 204 (the page and the
  typed values stay put), XHR/fetch get a 403 JSON error;
* approved requests consume their approval only if the system accepts them;
* a screenshot is captured after every write that went through, for the audit trail.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from ..company import SystemRegistry, Vault
from ..policy import ActionRequest, Decision, PolicyEngine
from ..policy.engine import WRITE_METHODS
from .base import Tool, ToolError, ToolResult, TransientError, schema

SNAPSHOT_JS = (Path(__file__).parent / "snapshot.js").read_text(encoding="utf-8")
_REF = re.compile(r"^e\d+$")


class BrowserHost:
    """One Playwright driver + Chromium per process; sessions get isolated contexts."""

    def __init__(self, headless: bool = True, slow_mo_ms: int = 0) -> None:
        self.headless, self.slow_mo_ms = headless, slow_mo_ms
        self._pw = None
        self._browser = None

    def browser(self):
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
            self._browser = self._pw = None


class BrowserSession:
    def __init__(self, host: BrowserHost, registry: SystemRegistry, policy: PolicyEngine, vault: Vault,
                 evidence_dir: Path, label: str = "operator",
                 on_evidence: Callable[[str, Path], None] | None = None) -> None:
        self.host, self.registry, self.policy, self.vault = host, registry, policy, vault
        self.evidence_dir, self.label, self.on_evidence = evidence_dir, label, on_evidence
        self._context = None
        self.page = None
        self._events: list[str] = []
        self._decisions: list[Decision] = []
        self._nav: list[tuple[int, str]] = []
        self._shots = 0

    # ------------------------------------------------------------------ lifecycle

    def _ensure(self):
        if self.page is None:
            self._context = self.host.browser().new_context(viewport={"width": 1280, "height": 900})
            self._context.route("**/*", self._on_route)
            self._context.on("page", self._adopt)
            self._adopt(self._context.new_page(), announce=False)
        return self.page

    def _adopt(self, page, announce: bool = True) -> None:
        page.on("dialog", self._on_dialog)
        page.on("response", self._on_response)
        if announce and self.page is not None:
            self._events.append(f"a new tab opened ({page.url}); now operating in it")
        self.page = page

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = self.page = None

    # ------------------------------------------------------------------ network guardrails

    def _on_route(self, route, request) -> None:
        url = request.url
        if not self.registry.is_allowed_url(url):
            self._events.append(f"blocked a request to {url}: not a registered company system (egress allowlist)")
            route.abort("blockedbyclient")
            return
        method = request.method.upper()
        if method not in WRITE_METHODS:
            route.continue_()
            return
        areq = ActionRequest.from_raw(method, url, request.post_data, request.headers.get("content-type"), "browser")
        decision = self.policy.evaluate(areq)
        self._decisions.append(decision)
        if decision.allowed and not decision.grant_id:
            route.continue_()
            return
        if decision.allowed:
            # Approved action: send it ourselves so we can tell whether the system accepted it.
            try:
                response = route.fetch(max_redirects=0)
            except PlaywrightError as exc:
                self.policy.restore_grant(decision.grant_id)
                self._events.append(f"the approved request failed to send ({exc}); approval kept for a retry")
                route.abort()
                return
            accepted = 300 <= response.status < 400 or (
                200 <= response.status < 300 and "json" in response.headers.get("content-type", ""))
            if not accepted:
                self.policy.restore_grant(decision.grant_id)
                self._events.append("the system did not accept the approved submission (no change was made); "
                                    "the approval remains valid for a corrected retry of the same action")
            route.fulfill(response=response)
            return
        if request.is_navigation_request():
            route.fulfill(status=204, body="")
        else:
            route.fulfill(status=403, content_type="application/json",
                          body=json.dumps({"error": "blocked_by_policy", "detail": decision.describe()}))

    def _on_dialog(self, dialog) -> None:
        self._events.append(f'{dialog.type} dialog "{dialog.message}" — accepted')
        try:
            dialog.accept()
        except PlaywrightError:
            pass

    def _on_response(self, response) -> None:
        try:
            if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                self._nav.append((response.status, response.url))
        except PlaywrightError:
            pass

    # ------------------------------------------------------------------ perception

    def snapshot_text(self, filter_text: str | None = None) -> str:
        page = self._ensure()
        try:
            data = page.evaluate(SNAPSHOT_JS, {"maxChars": 80000 if filter_text else 14000})
        except PlaywrightError as exc:
            raise ToolError(f"Could not read the page: {_first_line(exc)}") from exc
        text = self.vault.redact(data["text"])
        header = f"Page: {data['title']}\nURL: {_short_url(data['url'])}"
        if filter_text:
            needle = filter_text.lower()
            all_lines = text.splitlines()
            keep = [i for i, line in enumerate(all_lines) if needle in line.lower()]
            if not keep:
                return f"{header}\n(no lines on this page contain '{filter_text}')"
            selected = sorted({j for i in keep for j in range(max(0, i - 1), min(len(all_lines), i + 2))})
            text = "\n".join(all_lines[j] for j in selected)
            header += f"\n(showing lines matching '{filter_text}' with context)"
        return f"{header}\n{text}"

    def capture(self, label: str) -> Path:
        page = self._ensure()
        self._shots += 1
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:50] or "page"
        path = self.evidence_dir / f"{self.label}-{self._shots:02d}-{slug}.png"
        page.screenshot(path=str(path), full_page=True)
        if self.on_evidence:
            self.on_evidence(label, path)
        return path

    # ------------------------------------------------------------------ actions

    def navigate(self, url: str) -> ToolResult:
        target = self.registry.absolute(url.strip())
        if not self.registry.is_allowed_url(target):
            raise ToolError(f"{target} is not a registered company system. Reachable systems:\n{self.registry.describe()}")
        page = self._ensure()
        self._reset_observation()
        try:
            response = page.goto(target, wait_until="domcontentloaded", timeout=20000)
        except PlaywrightTimeout as exc:
            raise TransientError(f"Timed out loading {target}") from exc
        except PlaywrightError as exc:
            raise ToolError(f"Could not open {target}: {_first_line(exc)}") from exc
        if response is not None and response.status >= 500:
            raise TransientError(f"{target} returned HTTP {response.status}")
        return self._observe(f"opened {_short_url(target)}")

    def click(self, ref: str) -> ToolResult:
        loc = self._locate(ref)
        desc = self._element_label(loc)
        self._reset_observation()
        try:
            loc.click(timeout=6000)
        except PlaywrightTimeout as exc:
            raise ToolError(f"Could not click {ref} ({desc}): it is not clickable right now "
                            f"(hidden, covered or disabled). Take a new snapshot.") from exc
        except PlaywrightError as exc:
            raise ToolError(f"Click on {ref} failed: {_first_line(exc)}") from exc
        return self._observe(f"clicked {ref} ({desc})")

    def fill(self, fields: list[dict], submit_ref: str | None = None) -> ToolResult:
        done = []
        for field in fields:
            ref, value = field["ref"], field["value"]
            loc = self._locate(ref)
            info = loc.evaluate("el => ({tag: el.tagName, type: (el.getAttribute('type') || '').toLowerCase()})")
            try:
                if info["tag"] == "SELECT":
                    self._select(loc, ref, str(value))
                    shown = value
                elif info["type"] in ("checkbox", "radio"):
                    loc.set_checked(_truthy(value), timeout=4000)
                    shown = "checked" if _truthy(value) else "unchecked"
                else:
                    loc.fill(self.vault.resolve(str(value)), timeout=4000)
                    shown = "(secret)" if info["type"] == "password" or Vault.mentions_secret(str(value)) else value
            except KeyError as exc:
                raise ToolError(f"{exc}. Use one of the documented {{{{secret:...}}}} names.") from exc
            except PlaywrightError as exc:
                raise ToolError(f"Could not set {ref}: {_first_line(exc)}") from exc
            done.append(f"{ref}={shown!r}")
        if submit_ref:
            result = self.click(submit_ref)
            result.text = f"Filled {', '.join(done)}; then {result.text[0].lower()}{result.text[1:]}"
            return result
        self._reset_observation()
        return self._observe(f"filled {', '.join(done)}")

    def press(self, key: str) -> ToolResult:
        page = self._ensure()
        self._reset_observation()
        try:
            page.keyboard.press(key)
        except PlaywrightError as exc:
            raise ToolError(f"Could not press {key}: {_first_line(exc)}") from exc
        return self._observe(f"pressed {key}")

    def screenshot(self, label: str, show: bool = True) -> ToolResult:
        path = self.capture(label)
        images = [("image/png", self.page.screenshot(full_page=False))] if show else []
        return ToolResult(f"Saved evidence screenshot '{label}' ({path.name}).", images=images,
                          data={"evidence": str(path)})

    # ------------------------------------------------------------------ internals

    def _locate(self, ref: str):
        ref = ref.strip().strip("[]")
        if not _REF.match(ref):
            raise ToolError(f"'{ref}' is not an element ref. Use a ref like e12 from the latest snapshot.")
        page = self._ensure()
        loc = page.locator(f'[data-op-ref="{ref}"]')
        if loc.count() == 0:
            raise ToolError(f"No element {ref} on the current page — refs change whenever the page changes. "
                            f"Call browser_snapshot and use a current ref.")
        return loc.first

    def _element_label(self, loc) -> str:
        # Never read input values here: a password field's value is a secret.
        script = """el => {
            const t = (el.getAttribute('type') || '').toLowerCase();
            const isButton = el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(t);
            return el.getAttribute('aria-label') || (el.tagName === 'INPUT' && !isButton ? '' : el.innerText)
                || (isButton ? el.value : '') || ((el.tagName.toLowerCase()) + (el.name ? ' ' + el.name : ''));
        }"""
        try:
            return _clip(" ".join(str(loc.evaluate(script)).split()), 60)
        except PlaywrightError:
            return "element"

    def _select(self, loc, ref: str, value: str) -> None:
        try:
            loc.select_option(label=value, timeout=3000)
            return
        except PlaywrightError:
            pass
        try:
            loc.select_option(value=value, timeout=3000)
            return
        except PlaywrightError:
            options = loc.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
            # Accept an unambiguous partial match, e.g. "Dana Lee" for "Dana Lee (Sales Manager)".
            matches = [o for o in options if value.lower() in o.lower()]
            if len(matches) == 1:
                loc.select_option(label=matches[0], timeout=3000)
                return
            raise ToolError(f"'{value}' is not an option of {ref}. Options: {options}")

    def _reset_observation(self) -> None:
        self._events.clear()
        self._decisions.clear()
        self._nav.clear()

    def _settle(self) -> None:
        page = self._ensure()
        for state, timeout in (("domcontentloaded", 15000), ("networkidle", 2500)):
            try:
                page.wait_for_load_state(state, timeout=timeout)
            except PlaywrightError:
                pass

    def _observe(self, action: str) -> ToolResult:
        self._settle()
        time.sleep(0.05)
        notes, data = [], {"decisions": []}
        for decision in self._decisions:
            data["decisions"].append({"outcome": decision.outcome, "action": decision.action_id,
                                      "summary": decision.summary, "pending_id": decision.pending_id})
            if decision.allowed and decision.action_id.endswith(("sign_in", "sign_out")):
                continue  # routine; not worth the model's attention
            notes.append(decision.describe() + _guidance(decision))
        notes.extend(self._events)
        for status, url in self._nav:
            if status >= 500:
                notes.append(f"the server answered HTTP {status} for {_short_url(url)}. If this followed a "
                             "submission, check whether the change happened before trying again.")
        wrote = [d for d in self._decisions if d.allowed and not d.action_id.endswith(("sign_in", "sign_out"))]
        for decision in wrote:
            data.setdefault("evidence", []).append(str(self.capture(f"after {decision.action_id}")))
        snapshot = self.snapshot_text()
        parts = [f"Action: {action}"]
        parts += [f"Event: {n}" for n in notes]
        parts.append(snapshot)
        blocked = any(d.outcome != "allow" for d in self._decisions)
        return ToolResult("\n".join(parts), is_error=blocked, data=data)


def _guidance(decision: Decision) -> str:
    if decision.outcome == "require_approval":
        return (f"\n  → Nothing was sent. Call request_approval(pending_id='{decision.pending_id}', justification=...). "
                "Once approved, submit the same form again with the same values.")
    if decision.outcome == "deny":
        return ("\n  → Nothing was sent. This action is not permitted; do not attempt it another way. "
                "Follow the policy referenced above or escalate.")
    return ""


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on", "checked", "x", "selected")


def _short_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------- tool definitions

def browser_tools(session: BrowserSession) -> list[Tool]:
    return [
        Tool(
            "browser_navigate",
            "Open a page of a company web system in the browser and return a text snapshot of it. Accepts an "
            "absolute URL or a path such as /erp/customers/C-1003. Only registered company systems are reachable. "
            "Use this to go straight to a known page instead of clicking through menus.",
            schema({"url": {"type": "string", "description": "URL or path, e.g. /erp/bills?q=88213"}}, ["url"]),
            lambda a: session.navigate(a["url"]),
            idempotent=True,
        ),
        Tool(
            "browser_snapshot",
            "Re-read the current page as text. Interactive elements are shown as [eN] refs; use the newest refs "
            "with the other browser tools. Pass 'filter' to show only lines containing that text (useful on long pages).",
            schema({"filter": {"type": "string", "description": "Optional text to search for on the page"}}),
            lambda a: ToolResult(session.snapshot_text(a.get("filter"))),
        ),
        Tool(
            "browser_click",
            "Click an element (link, button, checkbox, disclosure) by its ref from the latest snapshot. Returns what "
            "happened (dialogs, policy decisions, errors) and a snapshot of the resulting page. Clicking a submit "
            "button sends the form; state-changing submissions are checked against company policy first.",
            schema({"ref": {"type": "string", "description": "Element ref, e.g. e12"}}, ["ref"]),
            lambda a: session.click(a["ref"]),
            idempotent=False,
        ),
        Tool(
            "browser_fill",
            "Fill form fields by ref, optionally clicking a submit button afterwards. Text fields: the value replaces "
            "the current content. Select boxes: the option's visible label. Checkboxes/radios: 'true' or 'false'. "
            "Date pickers: YYYY-MM-DD. For passwords use the {{secret:name}} placeholder from the systems guide — "
            "never ask for or invent credentials.",
            schema({
                "fields": {"type": "array", "items": schema(
                    {"ref": {"type": "string"}, "value": {"type": "string"}}, ["ref", "value"])},
                "submit_ref": {"type": "string", "description": "Optional ref of a button to click after filling"},
            }, ["fields"]),
            lambda a: session.fill(a["fields"], a.get("submit_ref")),
            idempotent=False,
        ),
        Tool(
            "browser_press",
            "Press a keyboard key in the browser (e.g. Enter, Tab, Escape).",
            schema({"key": {"type": "string"}}, ["key"]),
            lambda a: session.press(a["key"]),
            idempotent=False,
        ),
        Tool(
            "browser_screenshot",
            "Capture the current page as an evidence screenshot for the run report, and look at it yourself. Use "
            "it to document an important state (e.g. the refund confirmation) or when the text snapshot is unclear.",
            schema({"label": {"type": "string", "description": "Short description of what the screenshot shows"}},
                   ["label"]),
            lambda a: session.screenshot(a["label"]),
        ),
    ]
