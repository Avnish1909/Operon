"""Run report: a self-contained HTML page (and a Markdown summary) built from the run's state and ledger."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from ..agent.state import STATUS_MARK, RunState

STATUS_COLOURS = {
    "completed": "#15803d", "escalated": "#b45309", "unverified": "#b91c1c", "budget_exhausted": "#b91c1c",
    "error": "#b91c1c", "refused": "#b91c1c", "stalled": "#b91c1c", "running": "#1d4ed8",
}
VERDICT = {"pass": ("✓", "#15803d"), "fail": ("✗", "#b91c1c"), "unclear": ("?", "#b45309")}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def write_reports(state: RunState, events: list[dict], model: str, cost: float | None) -> Path:
    path = state.run_dir / "report.html"
    path.write_text(render_html(state, events, model, cost), encoding="utf-8")
    (state.run_dir / "summary.md").write_text(render_markdown(state, model, cost), encoding="utf-8")
    return path


def _final_results(state: RunState) -> dict[str, dict]:
    if not state.verifications:
        return {}
    return {r["criterion_id"]: r for r in state.verifications[-1]["results"]}


def render_markdown(state: RunState, model: str, cost: float | None) -> str:
    lines = [f"# Operon run {state.run_id}", "", f"**Request:** {state.goal}", "",
             f"**Status:** {state.status} · {state.steps} steps · {state.model_calls} model calls · "
             f"{state.duration_s}s" + (f" · ~${cost:.2f}" if cost is not None else ""), "",
             "## Outcome", "", state.summary or "(no summary)", ""]
    if state.brief:
        results = _final_results(state)
        lines += ["## Success criteria", ""]
        for c in state.brief.get("success_criteria", []):
            verdict = results.get(c["id"], {}).get("status", "not verified")
            lines.append(f"- **{c['id']}** [{verdict}] {c['criterion']}")
        extra = [r for cid, r in results.items() if cid not in {c['id'] for c in state.brief['success_criteria']}]
        for r in extra:
            lines.append(f"- **{r['criterion_id']}** (added by verifier) [{r['status']}] {r.get('criterion', '')}")
        lines += ["", "## Plan", "", state.plan_text(), ""]
    if state.approvals:
        lines += ["## Approvals", ""]
        lines += [f"- {a['pending_id']}: {a['summary']} — {'approved' if a['approved'] else 'declined'} by "
                  f"{a['approver']}" for a in state.approvals]
        lines.append("")
    if state.questions:
        lines += ["## Questions asked", ""]
        lines += [f"- Q: {q['question']}\n  A: {q['answer']}" for q in state.questions]
        lines.append("")
    if state.lessons:
        lines += ["## Lessons recorded", ""] + [f"- [{l['system']}] {l['lesson']}" for l in state.lessons] + [""]
    lines.append("Full report: report.html · event log: events.jsonl")
    return "\n".join(lines)


def render_html(state: RunState, events: list[dict], model: str, cost: float | None) -> str:
    colour = STATUS_COLOURS.get(state.status, "#334155")
    usage = state.usage
    started = datetime.fromtimestamp(state.started_at).strftime("%Y-%m-%d %H:%M:%S")
    parts: list[str] = []
    add = parts.append

    add(f"""<header><div class="title"><span class="badge" style="background:{colour}">{e(state.status.upper())}</span>
<h1>{e(state.goal)}</h1></div>
<div class="meta">Run {e(state.run_id)} · requested by {e(state.requester)} · started {e(started)} ·
{state.duration_s}s · {state.steps} tool calls · {state.model_calls} operator model calls ·
model {e(model)} · tokens in {usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens:,}
(cached {usage.cache_read_input_tokens:,}) / out {usage.output_tokens:,}{f' · est. cost ${cost:.2f}' if cost is not None else ''}</div>
</header>""")

    add(f"<section><h2>Outcome</h2><pre class='summary'>{e(state.summary or '(no summary)')}</pre>")
    if state.escalation:
        add("<dl class='facts'>" + "".join(f"<dt>{e(k.replace('_', ' '))}</dt><dd>{e(v)}</dd>"
                                          for k, v in state.escalation.items()) + "</dl>")
    add("</section>")

    if state.brief:
        b = state.brief
        results = _final_results(state)
        add("<section><h2>Understanding &amp; plan</h2><dl class='facts'>"
            f"<dt>Intended outcome</dt><dd>{e(b.get('intended_outcome'))}</dd>"
            f"<dt>Request type</dt><dd>{e(b.get('request_type', '—'))}</dd>"
            f"<dt>Procedures</dt><dd>{e(', '.join(b.get('procedures', [])))}</dd>"
            f"<dt>Assumptions</dt><dd>{e('; '.join(b.get('assumptions', [])) or '—')}</dd>"
            f"<dt>Approvals expected</dt><dd>{e('; '.join(b.get('approvals_expected', [])) or '—')}</dd>"
            f"<dt>Brief revisions</dt><dd>{b.get('revision', 1)}</dd></dl>")
        add("<h3>Success criteria (final verification)</h3><table><tr><th>Id</th><th>Criterion</th>"
            "<th>How to verify</th><th>Verdict</th><th>Verifier evidence</th></tr>")
        known = set()
        for c in b.get("success_criteria", []):
            known.add(c["id"])
            add(_criterion_row(c["id"], c["criterion"], c.get("how_to_verify", ""), results.get(c["id"])))
        for cid, r in results.items():
            if cid not in known:
                add(_criterion_row(cid, r.get("criterion", "") + " (added by verifier)", "", r))
        add("</table><h3>Plan</h3><ol class='plan'>")
        for p in state.plan:
            add(f"<li class='{e(p.status)}'><span>{STATUS_MARK.get(p.status, '?')}</span> {e(p.step)}"
                + (f" <em>— {e(p.note)}</em>" if p.note else "") + "</li>")
        add("</ol></section>")

    if state.questions or state.approvals:
        add("<section><h2>Human in the loop</h2>")
        for q in state.questions:
            add(f"<div class='hitl'><b>Question:</b> {e(q['question'])}<br><b>Answer:</b> {e(q['answer'])}</div>")
        for a in state.approvals:
            verdict = "approved" if a["approved"] else "declined"
            add(f"<div class='hitl {verdict}'><b>{e(a['pending_id'])} — {e(a['summary'])}</b><br>Policy rule "
                f"{e(a['rule'])} · {verdict} by {e(a['approver'])} {('— ' + e(a['comment'])) if a['comment'] else ''}"
                f"<br><span class='muted'>Justification: {e(a['justification'])}</span></div>")
        add("</section>")

    policy = [ev for ev in events if ev["kind"] == "policy" and not str(ev.get("action", "")).endswith(("sign_in", "sign_out"))]
    if policy:
        add("<section><h2>Policy decisions (enforced at the network boundary)</h2><table><tr><th>Time</th><th>Who</th>"
            "<th>Decision</th><th>Action</th><th>Detail</th></tr>")
        for ev in policy:
            add(f"<tr class='{e(ev['outcome'])}'><td>{_ts(ev['t'])}</td><td>{e(ev['actor'])}</td>"
                f"<td>{e(ev['outcome'])}</td><td>{e(ev.get('action'))}</td><td>{e(ev.get('summary'))}"
                f"{(' — ' + e(ev.get('message'))) if ev.get('message') else ''}</td></tr>")
        add("</table></section>")

    for v in state.verifications:
        add(f"<section><h2>Verification round {v['round']} — {'passed' if v['passed'] else 'failed'}</h2>"
            f"<p>{e(v['assessment'])}</p><table><tr><th>Criterion</th><th>Verdict</th><th>Evidence observed</th></tr>")
        for r in v["results"]:
            mark, col = VERDICT.get(r["status"], ("?", "#334155"))
            add(f"<tr><td><b>{e(r['criterion_id'])}</b> {e(r.get('criterion', ''))}</td>"
                f"<td style='color:{col}'>{mark} {e(r['status'])}</td><td>{e(r.get('evidence', ''))}</td></tr>")
        add("</table></section>")

    shots = [ev for ev in events if ev["kind"] == "evidence"]
    if shots:
        add("<section><h2>Evidence</h2><div class='gallery'>")
        for ev in shots:
            rel = Path(ev["path"]).name
            add(f"<figure><a href='evidence/{e(rel)}'><img src='evidence/{e(rel)}' loading='lazy'></a>"
                f"<figcaption>{_ts(ev['t'])} · {e(ev['actor'])} · {e(ev['label'])}</figcaption></figure>")
        add("</div></section>")

    if state.lessons:
        add("<section><h2>Lessons recorded for future runs</h2><ul>"
            + "".join(f"<li>[{e(l['system'])}] {e(l['lesson'])}</li>" for l in state.lessons) + "</ul></section>")

    add("<section><h2>Timeline</h2><table class='timeline'><tr><th>Time</th><th>Who</th><th>Event</th></tr>")
    for ev in events:
        row = _timeline_row(ev)
        if row:
            add(f"<tr class='k-{e(ev['kind'])}'><td>{_ts(ev['t'])}</td><td>{e(ev['actor'])}</td><td>{row}</td></tr>")
    add("</table></section>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operon run {e(state.run_id)}</title><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"""


def _criterion_row(cid: str, text: str, how: str, result: dict | None) -> str:
    status = result["status"] if result else "not verified"
    mark, col = VERDICT.get(status, ("·", "#64748b"))
    evidence = result.get("evidence", "") if result else ""
    return (f"<tr><td><b>{e(cid)}</b></td><td>{e(text)}</td><td class='muted'>{e(how)}</td>"
            f"<td style='color:{col}'>{mark} {e(status)}</td><td>{e(evidence)}</td></tr>")


def _timeline_row(ev: dict) -> str:
    k = ev["kind"]
    if k == "tool_call":
        return f"<code>{e(ev['name'])}</code> <span class='muted'>{e(_clip(json.dumps(ev['input'], ensure_ascii=False), 300))}</span>"
    if k == "tool_result":
        status = "ok" if ev["ok"] else "error"
        retry = f" (recovered after {ev['retries']} retries)" if ev.get("retries") else ""
        return (f"<details><summary class='{status}'>{e(_clip(ev['summary'], 220))}{e(retry)}</summary>"
                f"<pre>{e(ev.get('detail', ''))}</pre></details>")
    if k == "thinking":
        return f"<details><summary class='muted'>thinking: {e(_clip(ev['text'], 140))}</summary><pre>{e(ev['text'])}</pre></details>"
    if k == "say":
        return f"<i>{e(ev['text'])}</i>"
    if k == "policy":
        return f"<b class='{e(ev['outcome'])}'>policy {e(ev['outcome'])}</b> {e(ev.get('summary'))}"
    if k == "phase":
        return f"<b>── {e(ev['phase'].upper())}</b> {e(ev.get('detail', ''))}"
    if k == "brief":
        return f"<b>brief</b> {e(ev['brief'].get('intended_outcome'))}"
    if k in ("question", "answer"):
        return f"<b>{k}</b> {e(ev.get('question') or ev.get('answer'))}"
    if k == "approval_requested":
        return f"<b>approval requested</b> {e(ev['pending_id'])}: {e(ev['summary'])}"
    if k == "approval":
        return f"<b>approval</b> {e(ev['pending_id'])} {'approved' if ev['approved'] else 'declined'} by {e(ev['approver'])}"
    if k == "verification":
        return f"<b>verification round {ev['round']}</b>: {'passed' if ev['passed'] else 'failed'}"
    if k in ("guard", "error", "lesson", "note"):
        return f"<b>{k}</b> {e(ev.get('message') or ev.get('lesson') or (ev.get('key', '') + ' = ' + str(ev.get('value', ''))))}"
    if k == "evidence":
        return f"evidence: {e(ev['label'])}"
    if k == "run_started":
        return f"<b>run started</b> — {e(ev['goal'])}"
    if k == "run_finished":
        return f"<b>run finished: {e(ev['status'])}</b>"
    return ""


def _ts(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--bg:#f8fafc}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;color:var(--ink);background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:20px 28px}
.title{display:flex;gap:14px;align-items:center}.title h1{font-size:20px;margin:0}
.badge{color:#fff;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.04em}
.meta{color:var(--muted);margin-top:8px;font-size:13px}
section{background:#fff;margin:16px 28px;padding:16px 20px;border:1px solid var(--line);border-radius:8px}
h2{font-size:16px;margin:0 0 10px}h3{font-size:14px;margin:16px 0 8px}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}
.facts{display:grid;grid-template-columns:170px 1fr;gap:4px 12px;margin:0}.facts dt{color:var(--muted)}.facts dd{margin:0}
pre{white-space:pre-wrap;word-break:break-word;background:#f1f5f9;padding:10px;border-radius:6px;font-size:12px;margin:6px 0}
pre.summary{font:inherit;background:none;padding:0}
.plan{padding-left:22px}.plan li.done{color:#15803d}.plan li.blocked{color:#b91c1c}.plan li.skipped{color:var(--muted)}
.hitl{border-left:3px solid #1d4ed8;padding:6px 12px;margin:8px 0;background:#f8fafc}.hitl.approved{border-color:#15803d}.hitl.declined{border-color:#b91c1c}
.muted{color:var(--muted)}tr.deny td,b.deny{color:#b91c1c}tr.require_approval td,b.require_approval{color:#b45309}b.allow{color:#15803d}
summary.error{color:#b91c1c}summary{cursor:pointer}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
figure{margin:0;border:1px solid var(--line);border-radius:6px;overflow:hidden}figure img{width:100%;height:180px;object-fit:cover;object-position:top;display:block}
figcaption{font-size:12px;padding:6px 8px;color:var(--muted)}
.timeline td:first-child{white-space:nowrap;color:var(--muted)}code{background:#eef2ff;padding:1px 5px;border-radius:4px}
@media (max-width:700px){section{margin:12px}header{padding:16px}.facts{grid-template-columns:1fr}}
"""
