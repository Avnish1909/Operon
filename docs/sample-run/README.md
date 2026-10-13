# Sample run — `refund` scenario (live)

A real, unedited run of the operator, kept so reviewers can see its output without installing anything.

- **Request:** "Ticket 1042 — the customer says they were charged twice. Can you sort it out?" (from Jordan Blake, Support Lead)
- **Model:** `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter, effort `medium`
- **Faults injected:** a 503 on the customer page (recovered automatically); ERP session expiry armed after 7 requests (not reached in this run)
- **Outcome:** `completed` — the independent verifier confirmed all 6 success criteria; ground truth **7/7** (`checks.md`)
- **Effort:** 17 operator model calls + 4 verifier calls, 265 s, $0.00

| File | What it is |
|---|---|
| [`report.html`](report.html) | The run report: outcome, brief and success criteria with the verifier's verdicts, plan, approval, policy decisions, evidence, full timeline (download and open in a browser) |
| [`summary.md`](summary.md) | Short Markdown summary |
| [`checks.md`](checks.md) | Ground-truth checks, read directly from the sandbox database |
| [`events.jsonl`](events.jsonl) | The append-only event ledger the report is built from |
| [`transcript.json`](transcript.json) | The operator's raw model transcript |
| [`state.json`](state.json) | Final structured run state |
| [`evidence/`](evidence/) | Screenshot captured automatically after the refund was written |

Credentials never appear: the agent only ever handled `{{secret:...}}` placeholders, and everything logged is redacted.
