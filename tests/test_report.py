"""Unit tests for the frozen report schema and the report builder.

The JSON report schema is frozen (the dashboard consumes it); these tests pin
the exact key sets, the nesting, and the ``served_by`` journal aggregation.
The top-level builder lives inline in ``runner.__main__.main()`` and is
exercised offline with fake clients + a canned scenario.
"""

from __future__ import annotations

import json
import re

from runner.report import (
    AssertionResult,
    ScenarioResult,
    Timeline,
    TimelineEvent,
    write_report,
)
from runner.scenarios import ScenarioSpec, _make_result, _served_by
from runner.settings import LabSettings

import runner.__main__ as rmain

TOP_LEVEL_KEYS = {
    "run_id",
    "profile",
    "started_at",
    "finished_at",
    "duration_s",
    "lab",
    "config_snapshot",
    "scenarios",
    "summary",
}
LAB_KEYS = {"base_url", "mock_url", "litellm_image", "git_rev"}
CONFIG_KEYS = {"source", "router_settings"}
SCENARIO_KEYS = {
    "id",
    "name",
    "status",
    "duration_s",
    "expected",
    "observed",
    "assertions",
    "timeline",
    "served_by",
    "mocked_hits",
}
ASSERTION_KEYS = {"desc", "pass", "detail"}
TIMELINE_KEYS = {"t", "event", "detail"}
SERVED_BY_KEYS = {"mode", "hits"}


# ---------------------------------------------------------------------------
# Scenario-level schema + journal aggregation
# ---------------------------------------------------------------------------


def test_served_by_counts_journal_hits_by_mode():
    journal = [
        {"mode": "healthy", "outcome": "completed"},
        {"mode": "err-429", "outcome": "errored"},
        {"mode": "healthy", "outcome": "aborted"},
        {"mode": "healthy", "outcome": "completed"},
        {"mode": "err-429", "outcome": "errored"},
    ]
    assert _served_by(journal) == [
        {"mode": "healthy", "hits": 3},
        {"mode": "err-429", "hits": 2},
    ]
    assert _served_by([]) == []


def test_make_result_wires_served_by_and_assertion_verdict():
    timeline = Timeline()
    timeline.add("journal_reset", "DELETE /__hits ok")
    journal = [{"mode": "healthy", "outcome": "completed"}] * 2

    failed = _make_result(
        2,
        "fake",
        "expected",
        [AssertionResult("a", True, "d"), AssertionResult("b", False, "d2")],
        timeline,
        journal,
        1.5,
        "observed",
    )
    assert failed.status == "fail"  # any failing assertion fails the scenario
    assert failed.served_by == [{"mode": "healthy", "hits": 2}]
    assert failed.mocked_hits == journal

    passed = _make_result(
        1,
        "fake-ok",
        "expected",
        [AssertionResult("a", True, "d")],
        Timeline(),
        journal,
        0.1,
        "observed",
    )
    assert passed.status == "pass"


def test_scenario_result_to_dict_schema_and_nesting():
    result = ScenarioResult(
        id=1,
        name="fake",
        status="pass",
        duration_s=0.12345,
        expected="expected",
        observed="observed",
        assertions=[AssertionResult("a", True, "detail")],
        timeline_events=[TimelineEvent(t=0.25, event="journal_reset", detail="ok")],
        served_by=[{"mode": "healthy", "hits": 2}],
        mocked_hits=[{"mode": "healthy", "outcome": "completed"}],
    )
    data = result.to_dict()
    assert set(data.keys()) == SCENARIO_KEYS
    assert data["duration_s"] == 0.123  # rounded to 3 decimals
    assert set(data["assertions"][0].keys()) == ASSERTION_KEYS
    assert data["assertions"][0] == {"desc": "a", "pass": True, "detail": "detail"}
    assert set(data["timeline"][0].keys()) == TIMELINE_KEYS
    assert data["timeline"][0]["event"] == "journal_reset"
    assert data["timeline"][0]["detail"] == "ok"
    assert set(data["served_by"][0].keys()) == SERVED_BY_KEYS
    assert data["mocked_hits"] == [{"mode": "healthy", "outcome": "completed"}]


def test_write_report_writes_json_file(tmp_path):
    report = {"run_id": "20261013-000000-quick", "nested": {"a": [1, 2]}}
    path = write_report("20261013-000000-quick", report, reports_dir=str(tmp_path))
    assert path == tmp_path / "20261013-000000-quick.json"
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == report
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# Top-level builder (runner.__main__.main) exercised offline
# ---------------------------------------------------------------------------


def _fake_settings() -> LabSettings:
    return LabSettings(
        profile="quick",
        base_url="http://litellm-lab:4000",
        mock_url="http://mock-upstream:8000",
        git_rev="deadbeef",
        litellm_image="ghcr.io/berriai/litellm@sha256:abc",
        config_source="lab_config/router.quick.yaml",
        router_settings={"num_retries": 2, "cooldown_time": 8},
    )


class _FakeLabClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def wait_ready(self, timeout_s=90.0, poll_s=2.0, emit=lambda *a, **k: None):
        return True

    def close(self):
        pass


class _FakeMockClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def close(self):
        pass


def _canned_result(status: str) -> ScenarioResult:
    return ScenarioResult(
        id=1,
        name="fake-scenario",
        status=status,
        duration_s=0.5,
        expected="expected",
        observed="observed",
        assertions=[AssertionResult("assertion", status == "pass", "detail")],
        timeline_events=[TimelineEvent(t=0.1, event="journal_reset", detail="ok")],
        served_by=[{"mode": "healthy", "hits": 1}],
        mocked_hits=[{"mode": "healthy", "outcome": "completed"}],
    )


def _run_main(monkeypatch, tmp_path, result: ScenarioResult):
    settings = _fake_settings()
    monkeypatch.setattr(rmain, "load_lab_settings", lambda: settings)
    monkeypatch.setattr(rmain, "LabClient", _FakeLabClient)
    monkeypatch.setattr(rmain, "MockClient", _FakeMockClient)
    monkeypatch.setattr(
        rmain,
        "SCENARIOS",
        [ScenarioSpec(1, "fake-scenario", "expected", lambda ctx: result)],
    )
    captured = {}

    def fake_write_report(run_id, report, reports_dir=None):
        captured["run_id"] = run_id
        captured["report"] = report
        return tmp_path / f"{run_id}.json"

    monkeypatch.setattr(rmain, "write_report", fake_write_report)
    rc = rmain.main()
    return rc, settings, captured


def test_report_builder_emits_frozen_schema(monkeypatch, tmp_path):
    rc, settings, captured = _run_main(monkeypatch, tmp_path, _canned_result("pass"))
    assert rc == 0
    report = captured["report"]

    assert set(report.keys()) == TOP_LEVEL_KEYS
    assert re.fullmatch(r"\d{8}-\d{6}-quick", report["run_id"])
    assert report["profile"] == "quick"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", report["started_at"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", report["finished_at"])
    assert isinstance(report["duration_s"], float) and report["duration_s"] >= 0

    assert set(report["lab"].keys()) == LAB_KEYS
    assert report["lab"] == {
        "base_url": settings.base_url,
        "mock_url": settings.mock_url,
        "litellm_image": settings.litellm_image,
        "git_rev": settings.git_rev,
    }

    assert set(report["config_snapshot"].keys()) == CONFIG_KEYS
    assert report["config_snapshot"]["source"] == settings.config_source
    assert report["config_snapshot"]["router_settings"] == settings.router_settings

    assert report["scenarios"] == [_canned_result("pass").to_dict()]
    scenario = report["scenarios"][0]
    assert set(scenario.keys()) == SCENARIO_KEYS
    assert set(scenario["assertions"][0].keys()) == ASSERTION_KEYS
    assert set(scenario["timeline"][0].keys()) == TIMELINE_KEYS
    assert set(scenario["served_by"][0].keys()) == SERVED_BY_KEYS

    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0, "status": "pass"}


def test_report_builder_failed_scenario_sets_fail_status(monkeypatch, tmp_path):
    rc, _, captured = _run_main(monkeypatch, tmp_path, _canned_result("fail"))
    assert rc == 1
    report = captured["report"]
    assert report["summary"] == {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "status": "fail",
    }
