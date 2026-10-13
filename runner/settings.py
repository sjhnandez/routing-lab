"""Settings loading for the routing-lab scenario battery.

The runner is a short-lived container: configuration arrives via environment
variables (see deploy/docker-compose.dev.yml) plus two read-only mounts:

  /lab_config  - the LiteLLM config under test (config snapshot + source name)
  /deploy      - pinned-versions.env (litellm image digest for the report)

Reports are written to /app/reports, which the compose file mounts from the
repo's reports/ directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import yaml

DEFAULT_BASE_URL = "http://litellm-lab:4000"
DEFAULT_MOCK_URL = "http://mock-upstream:8000"

# Dummy lab master key, identical to general_settings.master_key in
# lab_config/router.*.yaml. No real secrets anywhere in this repo.
LAB_MASTER_KEY = "sk-lab-master-dummy"

PINNED_VERSIONS_PATH = "/deploy/pinned-versions.env"
LAB_CONFIG_DIR = "/lab_config"
REPORTS_DIR = "/app/reports"


@dataclass(frozen=True)
class LabSettings:
    profile: str
    base_url: str
    mock_url: str
    git_rev: Optional[str]
    litellm_image: Optional[str]
    config_source: str
    router_settings: dict[str, Any]


def _read_litellm_image() -> Optional[str]:
    """Read the digest-pinned litellm image string from pinned-versions.env."""
    try:
        with open(PINNED_VERSIONS_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("LITELLM_IMAGE="):
                    return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def _read_router_settings(profile: str) -> tuple[str, dict[str, Any]]:
    """Read router_settings from the mounted config for the active profile."""
    source = f"lab_config/router.{profile}.yaml"
    path = os.path.join(LAB_CONFIG_DIR, f"router.{profile}.yaml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        print(f"[warn] could not read {path}: {type(exc).__name__}: {exc}")
        return source, {}
    settings = data.get("router_settings") if isinstance(data, dict) else None
    if not isinstance(settings, dict):
        print(f"[warn] {path} has no router_settings mapping")
        return source, {}
    return source, settings


def load_lab_settings() -> LabSettings:
    profile = os.environ.get("LAB_PROFILE", "").strip() or "quick"
    base_url = (os.environ.get("LAB_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
    mock_url = (os.environ.get("LAB_MOCK_URL", "").strip() or DEFAULT_MOCK_URL).rstrip("/")
    git_rev_raw = os.environ.get("LAB_GIT_REV", "").strip()
    git_rev = None if git_rev_raw in ("", "unknown") else git_rev_raw
    config_source, router_settings = _read_router_settings(profile)
    return LabSettings(
        profile=profile,
        base_url=base_url,
        mock_url=mock_url,
        git_rev=git_rev,
        litellm_image=_read_litellm_image(),
        config_source=config_source,
        router_settings=router_settings,
    )
