"""Unit tests for ``runner.settings`` profile/env resolution (offline).

The mount-path constants (``/lab_config``, ``/deploy``) are monkeypatched to
point at tmp files so no lab containers are needed.
"""

from __future__ import annotations

import pytest

import runner.settings as settings_mod
from runner.settings import DEFAULT_BASE_URL, DEFAULT_MOCK_URL, load_lab_settings

ENV_VARS = ("LAB_PROFILE", "LAB_BASE_URL", "LAB_MOCK_URL", "LAB_GIT_REV")


@pytest.fixture()
def lab_files(tmp_path, monkeypatch):
    """Point the mount-path constants at tmp files and clear lab env vars."""
    deploy = tmp_path / "deploy"
    lab_config = tmp_path / "lab_config"
    deploy.mkdir()
    lab_config.mkdir()
    (deploy / "pinned-versions.env").write_text(
        "# pinned\nLITELLM_IMAGE=ghcr.io/berriai/litellm@sha256:deadbeef\n",
        encoding="utf-8",
    )
    (lab_config / "router.quick.yaml").write_text(
        "router_settings:\n  num_retries: 2\n  cooldown_time: 8\n",
        encoding="utf-8",
    )
    (lab_config / "router.full.yaml").write_text(
        "router_settings:\n  num_retries: 3\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        settings_mod, "PINNED_VERSIONS_PATH", str(deploy / "pinned-versions.env")
    )
    monkeypatch.setattr(settings_mod, "LAB_CONFIG_DIR", str(lab_config))
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_defaults(lab_files):
    settings = load_lab_settings()
    assert settings.profile == "quick"
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.mock_url == DEFAULT_MOCK_URL
    assert settings.git_rev is None
    assert settings.litellm_image == "ghcr.io/berriai/litellm@sha256:deadbeef"
    assert settings.config_source == "lab_config/router.quick.yaml"
    assert settings.router_settings == {"num_retries": 2, "cooldown_time": 8}


def test_env_overrides_and_trailing_slash_stripping(lab_files, monkeypatch):
    monkeypatch.setenv("LAB_PROFILE", "full")
    monkeypatch.setenv("LAB_BASE_URL", "http://proxy:4000/")
    monkeypatch.setenv("LAB_MOCK_URL", "http://mock:8000/")
    monkeypatch.setenv("LAB_GIT_REV", "abc1234")

    settings = load_lab_settings()
    assert settings.profile == "full"
    assert settings.base_url == "http://proxy:4000"
    assert settings.mock_url == "http://mock:8000"
    assert settings.git_rev == "abc1234"
    assert settings.config_source == "lab_config/router.full.yaml"
    assert settings.router_settings == {"num_retries": 3}


def test_git_rev_unknown_and_blank_env_fall_back(lab_files, monkeypatch):
    monkeypatch.setenv("LAB_GIT_REV", "unknown")
    monkeypatch.setenv("LAB_PROFILE", "   ")
    monkeypatch.setenv("LAB_BASE_URL", "")

    settings = load_lab_settings()
    assert settings.git_rev is None
    assert settings.profile == "quick"
    assert settings.base_url == DEFAULT_BASE_URL


def test_missing_config_file_yields_empty_router_settings(lab_files, monkeypatch):
    monkeypatch.setenv("LAB_PROFILE", "no-such-profile")
    settings = load_lab_settings()
    assert settings.config_source == "lab_config/router.no-such-profile.yaml"
    assert settings.router_settings == {}


def test_malformed_or_non_mapping_yaml_yields_empty_settings(
    lab_files, monkeypatch, tmp_path
):
    (tmp_path / "lab_config" / "router.broken.yaml").write_text(
        "][: not yaml", encoding="utf-8"
    )
    (tmp_path / "lab_config" / "router.scalar.yaml").write_text(
        "router_settings: not-a-mapping\n", encoding="utf-8"
    )

    monkeypatch.setenv("LAB_PROFILE", "broken")
    assert load_lab_settings().router_settings == {}

    monkeypatch.setenv("LAB_PROFILE", "scalar")
    assert load_lab_settings().router_settings == {}


def test_missing_pinned_versions_file_yields_none_image(lab_files, monkeypatch, tmp_path):
    monkeypatch.setattr(
        settings_mod, "PINNED_VERSIONS_PATH", str(tmp_path / "missing.env")
    )
    assert load_lab_settings().litellm_image is None
