"""Scenario runner: resets the world, injects faults, runs the operator, scores against ground truth.

Metrics that matter for an autonomous operator, reported per scenario:
* task success — every ground-truth check passed (read straight from the systems);
* agent status — what the operator concluded (completed / escalated / ...);
* false completion — the operator (and its verifier) said "done" but ground truth disagrees.
  This is the most damaging failure mode for an AI employee, so it is tracked explicitly;
* effort — tool calls, model calls, cost, wall time, questions and approvals.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from scenarios import Scenario
from scenarios.catalog import Check, Ctx

from .human import ConsoleHuman
from .runtime import Runtime


@dataclass
class ScenarioResult:
    scenario: str
    title: str
    trial: int
    agent_status: str
    success: bool
    false_completion: bool
    checks: list[dict]
    steps: int
    model_calls: int
    verification_rounds: int
    questions: int
    approvals: int
    duration_s: float
    cost_usd: float | None
    run_dir: str


def run_scenario(runtime: Runtime, scenario: Scenario, trial: int = 1, interactive: bool = False,
                 fresh_memory: bool = False, console: Console | None = None) -> ScenarioResult:
    from sandbox import db as sandbox_db

    console = console or Console()
    console.rule(f"[bold]Scenario {scenario.id}[/bold] — {scenario.title} (trial {trial})")
    if fresh_memory and runtime.lessons.path.exists():
        runtime.lessons.path.unlink()
    runtime.reset_world()
    runtime.set_faults(scenario.faults)
    today = runtime.business_date()
    runtime.human = ConsoleHuman(console) if interactive else scenario.human_factory(today)
    state = runtime.run(scenario.goal(today), scenario.requester)

    events = [json.loads(line) for line in (state.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    conn = sandbox_db.connect(runtime.settings.var_dir / "sandbox.db")
    try:
        checks = scenario.checker(Ctx(conn, runtime.settings.workspace_dir, state, today, events))
    finally:
        conn.close()
    graded = [c for c in checks if not c.info]
    success = all(c.passed for c in graded)
    state_data = json.loads((state.run_dir / "state.json").read_text(encoding="utf-8"))
    result = ScenarioResult(
        scenario=scenario.id, title=scenario.title, trial=trial, agent_status=state.status, success=success,
        false_completion=state.status == "completed" and not success,
        checks=[asdict(c) for c in checks], steps=state.steps, model_calls=state.model_calls,
        verification_rounds=len(state.verifications), questions=len(state.questions),
        approvals=len(state.approvals), duration_s=state.duration_s, cost_usd=state_data.get("cost_usd"),
        run_dir=str(state.run_dir),
    )
    _write_checks(state.run_dir, scenario, checks, success)
    _print_checks(console, checks, success)
    return result


def run_eval(runtime: Runtime, scenarios: list[Scenario], trials: int = 1, fresh_memory: bool = False,
             console: Console | None = None) -> Path:
    console = console or Console()
    results = [run_scenario(runtime, s, t + 1, fresh_memory=fresh_memory, console=console)
               for s in scenarios for t in range(trials)]
    out_dir = runtime.settings.runs_dir / f"eval-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8")
    (out_dir / "results.md").write_text(_markdown(results, runtime.settings.model), encoding="utf-8")
    _print_summary(console, results)
    console.print(f"[dim]Saved {out_dir / 'results.md'}[/dim]")
    return out_dir


def _write_checks(run_dir: Path, scenario: Scenario, checks: list[Check], success: bool) -> None:
    lines = [f"# Ground-truth checks — {scenario.id}: {scenario.title}", "",
             f"Result: **{'PASS' if success else 'FAIL'}**", ""]
    for c in checks:
        mark = "ℹ" if c.info else ("✓" if c.passed else "✗")
        lines.append(f"- {mark} {c.name}" + (f"  \n  `{c.detail[:400]}`" if c.detail and not c.passed else ""))
    (run_dir / "checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_checks(console: Console, checks: list[Check], success: bool) -> None:
    console.print(f"\n[bold]Ground truth: {'[green]PASS[/green]' if success else '[red]FAIL[/red]'}[/bold]")
    for c in checks:
        mark = "[blue]ℹ[/blue]" if c.info else ("[green]✓[/green]" if c.passed else "[red]✗[/red]")
        console.print(f"  {mark} {c.name}")
        if not c.passed and c.detail:
            console.print(f"      [dim]{c.detail[:300]}[/dim]")


def _print_summary(console: Console, results: list[ScenarioResult]) -> None:
    table = Table(title="Operon — scenario results")
    for col in ("scenario", "trial", "ground truth", "agent status", "checks", "steps", "verif. rounds", "asked",
                "approvals", "time", "cost"):
        table.add_column(col)
    for r in results:
        graded = [c for c in r.checks if not c["info"]]
        table.add_row(r.scenario, str(r.trial), "[green]PASS[/green]" if r.success else "[red]FAIL[/red]",
                      r.agent_status + (" [red](false completion)[/red]" if r.false_completion else ""),
                      f"{sum(c['passed'] for c in graded)}/{len(graded)}", str(r.steps), str(r.verification_rounds),
                      str(r.questions), str(r.approvals), f"{r.duration_s:.0f}s",
                      f"${r.cost_usd:.2f}" if r.cost_usd is not None else "—")
    console.print(table)
    n = len(results)
    console.print(f"Task success: {sum(r.success for r in results)}/{n} · "
                  f"false completions: {sum(r.false_completion for r in results)}/{n}")


def _markdown(results: list[ScenarioResult], model: str) -> str:
    n = len(results)
    lines = [f"# Operon eval — {datetime.now():%Y-%m-%d %H:%M}", "", f"Model: `{model}`", "",
             f"Task success **{sum(r.success for r in results)}/{n}** · false completions "
             f"**{sum(r.false_completion for r in results)}/{n}**", "",
             "| scenario | trial | ground truth | agent status | checks | steps | verification rounds | questions | approvals | time | cost |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        graded = [c for c in r.checks if not c["info"]]
        lines.append(f"| {r.scenario} | {r.trial} | {'PASS' if r.success else 'FAIL'} | {r.agent_status}"
                     f"{' (false completion)' if r.false_completion else ''} | "
                     f"{sum(c['passed'] for c in graded)}/{len(graded)} | {r.steps} | {r.verification_rounds} | "
                     f"{r.questions} | {r.approvals} | {r.duration_s:.0f}s | "
                     f"{'$%.2f' % r.cost_usd if r.cost_usd is not None else '—'} |")
    lines.append("")
    for r in results:
        failed = [c for c in r.checks if not c["passed"] and not c["info"]]
        if failed:
            lines.append(f"**{r.scenario} (trial {r.trial}) failed checks:** " + "; ".join(c["name"] for c in failed))
    return "\n".join(lines) + "\n"
