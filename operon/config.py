"""Runtime settings. Everything is overridable from the CLI or environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free",
}


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ[name]) if os.environ.get(name) else default


def _default_provider() -> str:
    if os.environ.get("OPERON_PROVIDER"):
        return os.environ["OPERON_PROVIDER"]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "anthropic"


@dataclass(frozen=True)
class Settings:
    # Where the company's knowledge, policies and system registry live.
    company_dir: Path = ROOT / "company"
    # Mutable state: shared drive, credential vault, run artifacts, cross-run memory.
    var_dir: Path = field(default_factory=lambda: _env_path("OPERON_VAR_DIR", ROOT / "var"))
    runs_dir: Path = field(default_factory=lambda: _env_path("OPERON_RUNS_DIR", ROOT / "runs"))
    sandbox_url: str = field(default_factory=lambda: os.environ.get(
        "SANDBOX_URL",
        f"http://{os.environ.get('OPERON_SANDBOX_HOST', '127.0.0.1')}:{os.environ.get('OPERON_SANDBOX_PORT', '8765')}"))

    # Model: "anthropic" (Claude, the default) or "openrouter" (any OpenAI-compatible model on OpenRouter).
    provider: str = field(default_factory=_default_provider)
    model: str = field(default_factory=lambda: os.environ.get("OPERON_MODEL", ""))
    effort: str = field(default_factory=lambda: os.environ.get("OPERON_EFFORT", "medium"))
    verifier_effort: str = field(default_factory=lambda: os.environ.get("OPERON_VERIFIER_EFFORT", "medium"))
    refusal_fallbacks: bool = True

    # Budgets — the operator stops and escalates instead of looping forever.
    max_steps: int = 90             # tool calls by the operator
    max_model_calls: int = 80
    max_wall_seconds: int = 1800
    max_verification_rounds: int = 3
    verifier_max_steps: int = 30

    # Browser
    headless: bool = True
    slow_mo_ms: int = 0

    def __post_init__(self) -> None:
        if self.provider not in DEFAULT_MODELS:
            raise ValueError(f"unknown provider '{self.provider}' (use one of {sorted(DEFAULT_MODELS)})")
        if not self.model:
            object.__setattr__(self, "model", DEFAULT_MODELS[self.provider])

    @property
    def workspace_dir(self) -> Path:
        return self.var_dir / "workspace"

    @property
    def vault_path(self) -> Path:
        return self.var_dir / "vault.json"

    @property
    def memory_dir(self) -> Path:
        return self.var_dir / "memory"

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})
