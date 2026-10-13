"""Company context the operator is configured with: its systems and its credentials."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SECRET = re.compile(r"\{\{secret:([\w.\-]+)\}\}")


@dataclass
class System:
    key: str
    name: str
    url: str                   # absolute URL of the system's web root (or API owner)
    path_prefix: str
    interfaces: list[str]
    purpose: str
    api_base: str | None = None
    api_auth_header: str | None = None
    api_secret: str | None = None
    sign_in: str = ""


@dataclass
class SystemRegistry:
    base_url: str
    systems: dict[str, System]
    extra_paths: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, company_dir: Path, base_url: str) -> "SystemRegistry":
        raw = yaml.safe_load((company_dir / "systems.yaml").read_text(encoding="utf-8"))
        base = str(raw.get("base_url", "${SANDBOX_URL}")).replace("${SANDBOX_URL}", base_url).rstrip("/")
        systems = {}
        for key, spec in raw["systems"].items():
            api = spec.get("api") or {}
            systems[key] = System(
                key=key, name=spec["name"], url=base + spec["url"], path_prefix=spec["url"],
                interfaces=list(spec.get("interfaces", [])), purpose=spec.get("purpose", ""),
                api_base=(base + api["base"]) if api else None,
                api_auth_header=api.get("auth_header"), api_secret=api.get("secret"),
                sign_in=spec.get("sign_in", ""),
            )
        return cls(base, systems, list(raw.get("extra_allowed_paths", [])))

    def is_allowed_url(self, url: str) -> bool:
        """Egress allowlist: only the registered systems' paths on the registered host."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("data", "blob", "about"):
            return True
        base = urllib.parse.urlparse(self.base_url)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            return False
        prefixes = [s.path_prefix for s in self.systems.values()] + self.extra_paths
        return any(parsed.path == p.rstrip("/") or parsed.path.startswith(p) for p in prefixes)

    def system_for_url(self, url: str) -> System | None:
        path = urllib.parse.urlparse(url).path
        for system in self.systems.values():
            if path.startswith(system.path_prefix) or path == system.path_prefix.rstrip("/"):
                return system
        return None

    def absolute(self, url_or_path: str) -> str:
        if url_or_path.startswith(("http://", "https://")):
            return url_or_path
        return self.base_url + "/" + url_or_path.lstrip("/")

    def url_map(self) -> dict[str, str]:
        return {key: s.url for key, s in self.systems.items()}

    def describe(self) -> str:
        lines = []
        for s in self.systems.values():
            api = f"; API base {s.api_base}" if s.api_base else ""
            sign_in = f"; sign in: {s.sign_in}" if s.sign_in else ""
            lines.append(f"- {s.key}: {s.name} — {s.purpose}. Interfaces: {', '.join(s.interfaces)}. "
                         f"Web: {s.url}{api}{sign_in}")
        return "\n".join(lines)


class Vault:
    """Credentials are referenced as {{secret:name}}; tools substitute them at the last moment.

    The model only ever sees placeholders, and anything written to logs or shown to the
    model is passed through redact()."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._secrets: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._secrets = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, name: str) -> str:
        if name not in self._secrets:
            raise KeyError(f"unknown secret '{name}'")
        return self._secrets[name]

    def resolve(self, text: str) -> str:
        return _SECRET.sub(lambda m: self.get(m.group(1)), text)

    def redact(self, text: str) -> str:
        for name, value in self._secrets.items():
            if value and value in text:
                text = text.replace(value, f"{{{{secret:{name}}}}}")
        return text

    @staticmethod
    def mentions_secret(text: str) -> bool:
        return bool(_SECRET.search(text))
