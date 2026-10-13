"""Command line interface.

    operon run "Ticket 1042 — the customer says they were charged twice. Can you sort it out?"
    operon scenario refund            # reset the sandbox, inject faults, run, score against ground truth
    operon eval                       # every scenario, summary table
    operon sandbox serve              # browse the sandbox company yourself
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from rich.console import Console

from .config import ROOT, Settings


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = _parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = Console(highlight=False)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args, console)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="operon", description="Operon — an autonomous company operator (prototype).")
    sub = p.add_subparsers(dest="command", required=True)

    def agent_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--provider", choices=["anthropic", "openrouter"],
                        help="model provider (default: anthropic if ANTHROPIC_API_KEY is set, else openrouter)")
        sp.add_argument("--model", help="model id (defaults: claude-opus-5-5 / nvidia/nemotron-3-ultra-550b-a55b:free)")
        sp.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="reasoning effort")
        sp.add_argument("--max-steps", type=int, help="tool-call budget for the operator")
        sp.add_argument("--max-model-calls", type=int, help="model-call budget for the operator")
        sp.add_argument("--verifier-steps", type=int, help="model-call budget per verification round")
        sp.add_argument("--headed", action="store_true", help="show the browser window")
        sp.add_argument("--slow-mo", type=int, default=0, help="slow browser actions down (ms), for demos")
        sp.add_argument("--quiet", action="store_true", help="no live trace (report is still written)")

    run = sub.add_parser("run", help="give the operator a request")
    run.add_argument("goal", help="the request, in plain language")
    run.add_argument("--requester", default="Olivia Chen (COO)", help="who is asking")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--auto-approve", action="store_true", help="approve every held action (no prompts)")
    mode.add_argument("--auto-deny", action="store_true", help="decline every held action (no prompts)")
    agent_opts(run)
    run.set_defaults(func=cmd_run)

    sc = sub.add_parser("scenario", help="run one benchmark scenario against a fresh sandbox and score it")
    sc.add_argument("id", help="scenario id (see `operon scenarios`)")
    sc.add_argument("--interactive", action="store_true", help="you answer questions/approvals instead of the script")
    sc.add_argument("--fresh-memory", action="store_true", help="forget lessons from earlier runs first")
    agent_opts(sc)
    sc.set_defaults(func=cmd_scenario)

    ev = sub.add_parser("eval", help="run all (or some) scenarios and print a scorecard")
    ev.add_argument("--only", help="comma-separated scenario ids")
    ev.add_argument("--trials", type=int, default=1)
    ev.add_argument("--fresh-memory", action="store_true", help="forget lessons before each scenario")
    agent_opts(ev)
    ev.set_defaults(func=cmd_eval)

    ls = sub.add_parser("scenarios", help="list benchmark scenarios")
    ls.set_defaults(func=cmd_list)

    sb = sub.add_parser("sandbox", help="manage the simulated company")
    sb_sub = sb.add_subparsers(dest="action", required=True)
    reset = sb_sub.add_parser("reset", help="recreate data, files and credentials")
    reset.set_defaults(func=cmd_sandbox_reset)
    serve = sb_sub.add_parser("serve", help="run the sandbox web systems in the foreground")
    serve.set_defaults(func=cmd_sandbox_serve)

    lessons = sub.add_parser("lessons", help="show or clear the operator's learned lessons")
    lessons.add_argument("--clear", action="store_true")
    lessons.set_defaults(func=cmd_lessons)
    return p


# ---------------------------------------------------------------------- commands

def _settings(args) -> Settings:
    base = Settings(provider=args.provider) if getattr(args, "provider", None) else Settings()
    return base.with_overrides(
        model=getattr(args, "model", None), effort=getattr(args, "effort", None),
        max_steps=getattr(args, "max_steps", None), max_model_calls=getattr(args, "max_model_calls", None),
        verifier_max_steps=getattr(args, "verifier_steps", None),
        headless=not getattr(args, "headed", False) if hasattr(args, "headed") else None,
        slow_mo_ms=getattr(args, "slow_mo", None),
    )


def _require_credentials(console: Console, settings: Settings) -> bool:
    if settings.provider == "openrouter":
        if os.environ.get("OPENROUTER_API_KEY"):
            return True
        console.print(f"[red]OPENROUTER_API_KEY is not set.[/red] Add it to {ROOT / '.env'} (see .env.example).")
        return False
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if (Path.home() / ".config" / "anthropic").exists():
        return True  # an `ant auth login` profile; the SDK picks it up
    console.print("[red]No model credentials found.[/red] Set ANTHROPIC_API_KEY (or OPENROUTER_API_KEY) in your "
                  f"environment or in {ROOT / '.env'} (see .env.example).")
    return False


def cmd_run(args, console: Console) -> int:
    from .human import AutoHuman, ConsoleHuman
    from .runtime import Runtime

    settings = _settings(args)
    if not _require_credentials(console, settings):
        return 2
    human = AutoHuman(True) if args.auto_approve else AutoHuman(False) if args.auto_deny else ConsoleHuman(console)
    with Runtime(settings, human, console=console, quiet=args.quiet) as rt:
        state = rt.run(args.goal, args.requester)
    return 0 if state.status == "completed" else 1


def cmd_scenario(args, console: Console) -> int:
    from scenarios import get

    from .evals import run_scenario
    from .human import ScriptedHuman
    from .runtime import Runtime

    settings = _settings(args)
    if not _require_credentials(console, settings):
        return 2
    scenario = get(args.id)
    with Runtime(settings, ScriptedHuman(), console=console, quiet=args.quiet) as rt:
        result = run_scenario(rt, scenario, interactive=args.interactive, fresh_memory=args.fresh_memory,
                              console=console)
    console.print(f"[dim]Report: {Path(result.run_dir) / 'report.html'}[/dim]")
    return 0 if result.success else 1


def cmd_eval(args, console: Console) -> int:
    from scenarios import SCENARIOS, get

    from .evals import run_eval
    from .human import ScriptedHuman
    from .runtime import Runtime

    settings = _settings(args)
    if not _require_credentials(console, settings):
        return 2
    chosen = [get(i.strip()) for i in args.only.split(",")] if args.only else SCENARIOS
    with Runtime(settings, ScriptedHuman(), console=console, quiet=args.quiet) as rt:
        run_eval(rt, chosen, trials=args.trials, fresh_memory=args.fresh_memory, console=console)
    return 0


def cmd_list(args, console: Console) -> int:
    from datetime import date

    from rich.table import Table

    from scenarios import SCENARIOS

    table = Table(title="Benchmark scenarios")
    for col in ("id", "request", "requester", "demonstrates", "faults"):
        table.add_column(col, overflow="fold")
    for s in SCENARIOS:
        table.add_row(s.id, s.goal(date.today()), s.requester, ", ".join(s.demonstrates),
                      ", ".join(f"{k}: {v}" for k, v in s.faults.items()) or "—")
    console.print(table)
    return 0


def cmd_sandbox_reset(args, console: Console) -> int:
    import urllib.request

    from sandbox import env, server

    server.reset_world()
    try:
        urllib.request.urlopen(urllib.request.Request(f"{env.BASE_URL}/__admin/reload-vault", method="POST"), timeout=2)
    except OSError:
        pass
    console.print(f"Sandbox reset: database, shared drive ({env.WORKSPACE_DIR}) and credentials recreated.")
    return 0


def cmd_sandbox_serve(args, console: Console) -> int:
    from sandbox import env, server

    server.ensure_world()
    console.print(f"Northwind sandbox at [bold]{env.BASE_URL}[/bold]  (people log in with manager / northwind)\n"
                  f"  ERP {env.BASE_URL}/erp/ · Helpdesk {env.BASE_URL}/desk/ · HR {env.BASE_URL}/hr/ · "
                  f"Mail {env.BASE_URL}/mail/\nCtrl+C to stop.")
    srv = server.SandboxServer(env.HOST, env.PORT)
    srv.start()
    try:
        srv._thread.join()
    except KeyboardInterrupt:
        srv.stop()
    return 0


def cmd_lessons(args, console: Console) -> int:
    from .knowledge import LessonStore

    store = LessonStore(Settings().memory_dir / "lessons.jsonl")
    if args.clear:
        if store.path.exists():
            store.path.unlink()
        console.print("Lessons cleared.")
        return 0
    lessons = store.all()
    if not lessons:
        console.print("No lessons recorded yet.")
    for item in lessons:
        console.print(f"- [{item['system']}] {item['lesson']}  [dim]({item['run_id']})[/dim]")
    return 0


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


if __name__ == "__main__":
    raise SystemExit(main())
