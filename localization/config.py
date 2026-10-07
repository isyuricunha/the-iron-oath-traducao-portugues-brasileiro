"""Configuration: environment, CLI defaults, FreeInference profile.

Precedence (highest first):
    explicit CLI option > supported environment override > repository
    configuration > internal default.

The FreeInference profile is strict: exactly one route
(freeinference.org with deepseek-v4-flash). Any other host or model is
rejected at startup. The API key never leaves the Authorization header.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

GAME_TITLE = "The Iron Oath"
USER_AGENT = "Translation Pipeline The Iron Oath FI Transparency"

MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://freeinference.org/v1"
REASONING_VALUES = ("none", "low", "high", "max")
MAX_TOKENS_CEILING = 393216

_ENV_FILE = Path(".env")


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    model: str = MODEL
    glossary_model: str = MODEL
    reasoning_effort: str = "max"
    max_response_size: int = 131072
    timeout: float = 900.0
    idle_timeout: float = 180.0
    retries: int = 3
    batch_size: int = 40
    max_batch_chars: int = 24000
    translation_concurrency: int = 3
    provider_max_concurrency: int = 3
    glossary_worker_concurrency: int = 1
    min_translation_slots: int = 2
    stream: bool = True
    resume: bool = True
    glossary_enabled: bool = True
    glossary_worker_enabled: bool = True
    api_key: str = ""
    source_lang: str = "en"
    target_lang: str = "ptbr"
    input_root: Path = field(default_factory=lambda: Path("."))
    output_root: Path = field(default_factory=lambda: Path("PTBR"))
    format: str = "auto"
    project_context: str = ""
    mode: str = "translate"
    verbose: bool = False

    def validate_profile(self) -> None:
        host = urlparse(self.base_url).hostname or ""
        if host != "freeinference.org":
            raise ValueError(
                f"TRANSLATION_BASE_URL host {host!r} is not allowed; "
                "the profile accepts only host freeinference.org"
            )
        if self.model != MODEL:
            raise ValueError(
                f"TRANSLATION_MODEL {self.model!r} is not allowed; "
                "the profile accepts only model deepseek-v4-flash"
            )
        if self.glossary_model != MODEL:
            raise ValueError(
                f"GLOSSARY_MODEL {self.glossary_model!r} is not allowed; "
                "the profile accepts only model deepseek-v4-flash"
            )
        if self.reasoning_effort not in REASONING_VALUES:
            raise ValueError(
                f"Reasoning effort {self.reasoning_effort!r} is not accepted; "
                f"accepted values are {', '.join(REASONING_VALUES)}"
            )
        if not (0 < self.max_response_size <= MAX_TOKENS_CEILING):
            raise ValueError(
                f"max_response_size {self.max_response_size} is outside "
                f"(0, {MAX_TOKENS_CEILING}]"
            )
        if self.max_batch_chars < 0:
            raise ValueError("max_batch_chars must be >= 0")
        if not (1 <= self.translation_concurrency <= self.provider_max_concurrency):
            raise ValueError(
                "translation_concurrency must be >= 1 and <= provider_max_concurrency"
            )
        if self.provider_max_concurrency > 3:
            raise ValueError("provider_max_concurrency must not exceed 3")
        if self.input_root.resolve() == self.output_root.resolve():
            raise ValueError("input and output must not resolve to the same path")


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read a dotenv file: plain, quoted and `export NAME=value` forms.

    Later keys win; malformed lines are skipped. The process environment
    already takes precedence at resolution time.
    """
    path = path or _ENV_FILE
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, val = match.group(1), match.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key] = val
    return values


def resolve_key(env: dict[str, str]) -> str:
    """The key comes from the process environment first, then .env."""
    return os.environ.get("FREEINFERENCE_API_KEY") or env.get("FREEINFERENCE_API_KEY", "")


def settings_from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(env) if env is not None else load_dotenv()
    s = Settings()
    s.api_key = resolve_key(env)
    s.base_url = env.get("TRANSLATION_BASE_URL", s.base_url)
    s.model = env.get("TRANSLATION_MODEL", s.model)
    s.glossary_model = env.get("GLOSSARY_MODEL") or s.model
    s.reasoning_effort = env.get("TRANSLATION_REASONING_EFFORT", s.reasoning_effort)
    s.validate_profile()
    return s


def startup_report(s: Settings) -> str:
    """Print the effective configuration; never the key."""
    lines = [
        "provider profile: freeinference/deepseek-v4-flash",
        f"gateway host: {urlparse(s.base_url).hostname}",
        f"user-agent: {USER_AGENT}",
        f"translation model: {s.model}",
        f"glossary model: {s.glossary_model}",
        f"reasoning effort: {s.reasoning_effort}",
        "thinking: enabled" if s.reasoning_effort != "none" else "thinking: disabled",
        f"max response size: {s.max_response_size}",
        f"timeout: {s.timeout}s, idle timeout: {s.idle_timeout}s, retries: {s.retries}",
        f"streaming: {'on' if s.stream else 'off'}",
        f"batch size ceiling: {s.batch_size}",
        (
            f"translation concurrency: {s.translation_concurrency}, "
            f"provider concurrency: {s.provider_max_concurrency}"
        ),
    ]
    return "\n".join(lines)