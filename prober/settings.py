"""Environment-driven settings for the prober.

All configuration arrives via environment variables; no secret is ever read
from a file, written to disk or printed:

  LITELLM_BASE_URL    router base URL (default http://127.0.0.1:4001)
  LITELLM_API_KEY     bearer key for live probes (required for any live
                      probe; --dry-run and --status never need it)
  PROBER_HISTORY_DIR  history/state directory (default prober/history;
                      relative paths resolve against the CWD)
  PROBER_DAILY_CAP    maximum live probes per UTC day (default 48)
  PROBER_TIMEOUT_S    per-request timeout in seconds (default 30)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:4001"
DEFAULT_HISTORY_DIR = "prober/history"
DEFAULT_DAILY_CAP = 48
DEFAULT_TIMEOUT_S = 30.0


class ConfigError(Exception):
    """Invalid environment configuration (maps to exit code 2)."""


@dataclass(frozen=True)
class ProberSettings:
    base_url: str
    api_key: Optional[str] = field(repr=False)  # never render the key
    history_dir: Path
    daily_cap: int
    timeout_s: float


def _parse_int(name: str, raw: Optional[str], default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw.strip()!r}") from None
    if value < 0:
        raise ConfigError(f"{name} must be >= 0, got {value}")
    return value


def _parse_float(name: str, raw: Optional[str], default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw.strip()!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be > 0, got {value}")
    return value


def load_settings(env: Optional[Mapping[str, str]] = None) -> ProberSettings:
    source: Mapping[str, str] = os.environ if env is None else env

    base_url = (source.get("LITELLM_BASE_URL") or "").strip() or DEFAULT_BASE_URL
    api_key = (source.get("LITELLM_API_KEY") or "").strip() or None
    history_raw = (source.get("PROBER_HISTORY_DIR") or "").strip()
    return ProberSettings(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        history_dir=Path(history_raw or DEFAULT_HISTORY_DIR),
        daily_cap=_parse_int(
            "PROBER_DAILY_CAP", source.get("PROBER_DAILY_CAP"), DEFAULT_DAILY_CAP
        ),
        timeout_s=_parse_float(
            "PROBER_TIMEOUT_S", source.get("PROBER_TIMEOUT_S"), DEFAULT_TIMEOUT_S
        ),
    )
