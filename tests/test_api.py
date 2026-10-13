"""Unit tests for ``api.main`` (the ``lab-api`` service).

Fully in-process via ``fastapi.testclient.TestClient`` against an app built
with tmp dirs (``create_app(Config(...))``) - no server, no docker, no queue
executor. Covers the POST /api/run gates (trigger kill switch, single
concurrency, cooldown math, per-IP window), id sanitization/traversal
safety, response shapes, SSE replay/tail of queue events files, and
last-run/history over fixture reports.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import Config, _event_stream, create_app

RUN_ID_A = "0" * 32
RUN_ID_B = "a" * 32


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago_iso(seconds: float) -> str:
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_config(tmp_path: Path, **overrides) -> Config:
    fields = {
        "queue_dir": tmp_path / "queue",
        "reports_dir": tmp_path / "reports",
        "docs_dir": tmp_path / "docs",
        "static_dir": tmp_path / "static",
    }
    fields.update(overrides)
    cfg = Config(**fields)
    for directory in (cfg.queue_dir, cfg.reports_dir, cfg.docs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return cfg


def make_client(tmp_path: Path, **overrides) -> TestClient:
    return TestClient(create_app(make_config(tmp_path, **overrides)))


def write_queue_record(
    tmp_path: Path,
    run_id: str,
    *,
    status: str = "done",
    age_s: float = 300.0,
    source_ip: str = "203.0.113.7",
    requested_at: str | None = None,
) -> None:
    record = {
        "id": run_id,
        "requested_at": requested_at or _ago_iso(age_s),
        "profile": "quick",
        "source_ip": source_ip,
        "status": status,
    }
    path = tmp_path / "queue" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def append_events(tmp_path: Path, run_id: str, events: list[dict]) -> None:
    lines = "".join(json.dumps(event) + "\n" for event in events)
    path = tmp_path / "queue" / f"{run_id}.events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(lines)


def make_events(run_id: str) -> list[dict]:
    ts = _now_iso()
    return [
        {"ts": ts, "stage": "queued"},
        {"ts": ts, "stage": "boot"},
        {"ts": ts, "stage": "scenario", "scenario": 1, "name": "healthy-baseline", "status": "pass"},
        {"ts": ts, "stage": "scenario", "scenario": 2, "name": "retry-on-429", "status": "pass"},
        {"ts": ts, "stage": "teardown"},
        {"ts": ts, "stage": "done", "report_id": run_id},
    ]


def make_report(run_id: str, started_at: str, duration_s: float = 58.4) -> dict:
    return {
        "run_id": run_id,
        "profile": "quick",
        "started_at": started_at,
        "finished_at": started_at,
        "duration_s": duration_s,
        "lab": {"base_url": "http://litellm-lab:4000", "git_rev": "abc1234"},
        "config_snapshot": {"source": "lab_config/router.quick.yaml"},
        "scenarios": [],
        "summary": {"total": 8, "passed": 8, "failed": 0, "status": "pass"},
    }


def write_report(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "reports" / f"{report['run_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def sse_stages(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# POST /api/run: accepted path + queue file contract
# ---------------------------------------------------------------------------


def test_post_run_accepted_creates_queue_files(tmp_path):
    client = make_client(tmp_path)
    resp = client.post("/api/run")
    assert resp.status_code == 202
    run_id = resp.json()["id"]
    assert len(run_id) == 32 and run_id == run_id.lower()

    record = json.loads((tmp_path / "queue" / f"{run_id}.json").read_text())
    assert set(record) == {"id", "requested_at", "profile", "source_ip", "status"}
    assert record["id"] == run_id
    assert record["profile"] == "quick"
    assert record["source_ip"]
    assert record["status"] == "pending"
    assert len(record["requested_at"]) == 20 and record["requested_at"].endswith("Z")

    lines = (tmp_path / "queue" / f"{run_id}.events.jsonl").read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert set(event) == {"ts", "stage"}
    assert event["stage"] == "queued"


def test_post_run_records_cf_connecting_ip(tmp_path):
    client = make_client(tmp_path)
    resp = client.post("/api/run", headers={"CF-Connecting-IP": "198.51.100.9"})
    assert resp.status_code == 202
    record = json.loads(
        (tmp_path / "queue" / f"{resp.json()['id']}.json").read_text()
    )
    assert record["source_ip"] == "198.51.100.9"


def test_post_run_ignores_malformed_queue_entries(tmp_path):
    client = make_client(tmp_path)
    queue = tmp_path / "queue"
    (queue / "junk.txt").write_text("not json", encoding="utf-8")
    (queue / "nothex.json").write_text('{"status": "pending"}', encoding="utf-8")
    (queue / f"{RUN_ID_A}.json").write_text("{not valid", encoding="utf-8")
    (queue / f"{RUN_ID_B}.json").write_text(
        '{"requested_at": "yesterday", "status": "pending"}', encoding="utf-8"
    )
    assert client.post("/api/run").status_code == 202


# ---------------------------------------------------------------------------
# Gate: trigger kill switch
# ---------------------------------------------------------------------------


def test_trigger_off_returns_503_and_writes_nothing(tmp_path):
    client = make_client(tmp_path, trigger_enabled=False)
    resp = client.post("/api/run")
    assert resp.status_code == 503
    assert resp.json() == {"reason": "trigger_disabled"}
    assert list((tmp_path / "queue").iterdir()) == []


def test_trigger_off_beats_other_gates(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="pending", age_s=0.1)
    client = make_client(tmp_path, trigger_enabled=False)
    resp = client.post("/api/run")
    assert resp.status_code == 503
    assert resp.json() == {"reason": "trigger_disabled"}


def test_health_reports_trigger_state(tmp_path):
    client = make_client(tmp_path, trigger_enabled=False)
    assert client.get("/api/health").json()["trigger_enabled"] is False


# ---------------------------------------------------------------------------
# Gate: single concurrency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "running"])
def test_run_in_progress_blocks(tmp_path, status):
    write_queue_record(tmp_path, RUN_ID_A, status=status, age_s=3600)
    client = make_client(tmp_path)
    resp = client.post("/api/run")
    assert resp.status_code == 429
    assert resp.json() == {"reason": "run_in_progress"}


def test_health_counts_pending_and_running(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="pending", age_s=3600)
    write_queue_record(tmp_path, RUN_ID_B, status="running", age_s=3500)
    client = make_client(tmp_path)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["queue"] == {"pending": 1, "running": 1}


# ---------------------------------------------------------------------------
# Gate: 120 s cooldown since the newest run
# ---------------------------------------------------------------------------


def test_cooldown_blocks_with_retry_after(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=60)
    client = make_client(tmp_path)
    resp = client.post("/api/run")
    assert resp.status_code == 429
    body = resp.json()
    assert body["reason"] == "cooldown"
    assert 58 <= body["retry_after_s"] <= 60


def test_cooldown_expired_allows(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=200)
    client = make_client(tmp_path)
    resp = client.post("/api/run", headers={"CF-Connecting-IP": "198.51.100.9"})
    assert resp.status_code == 202


def test_cooldown_uses_newest_entry(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=500)
    write_queue_record(
        tmp_path, RUN_ID_B, status="failed", age_s=30, source_ip="198.51.100.9"
    )
    client = make_client(tmp_path)
    resp = client.post("/api/run")
    assert resp.status_code == 429
    body = resp.json()
    assert body["reason"] == "cooldown"
    assert 88 <= body["retry_after_s"] <= 90


# ---------------------------------------------------------------------------
# Gate: per-IP window (1 / 10 min)
# ---------------------------------------------------------------------------


def test_rate_limited_same_ip_within_window(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=300, source_ip="203.0.113.7")
    client = make_client(tmp_path)
    resp = client.post("/api/run", headers={"CF-Connecting-IP": "203.0.113.7"})
    assert resp.status_code == 429
    body = resp.json()
    assert body["reason"] == "rate_limited"
    assert 295 <= body["retry_after_s"] <= 300


def test_rate_limit_expired_allows(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=700, source_ip="203.0.113.7")
    client = make_client(tmp_path)
    resp = client.post("/api/run", headers={"CF-Connecting-IP": "203.0.113.7"})
    assert resp.status_code == 202


def test_rate_limit_isolated_per_ip(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=300, source_ip="203.0.113.7")
    client = make_client(tmp_path)
    resp = client.post("/api/run", headers={"CF-Connecting-IP": "198.51.100.9"})
    assert resp.status_code == 202


def test_rate_limit_falls_back_to_client_host(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=300, source_ip="testclient")
    client = make_client(tmp_path)
    resp = client.post("/api/run")
    assert resp.status_code == 429
    assert resp.json()["reason"] == "rate_limited"


# ---------------------------------------------------------------------------
# Id sanitization + traversal safety
# ---------------------------------------------------------------------------

BAD_RUN_IDS = [
    "..",
    ".",
    "not-a-hex-id",
    "0" * 31,
    "0" * 33,
    "Z" * 32,
    "0" * 31 + "-",
    "..%2F..%2Fsecret",
]


@pytest.mark.parametrize("bad_id", BAD_RUN_IDS)
def test_events_rejects_bad_ids(tmp_path, bad_id):
    client = make_client(tmp_path)
    resp = client.get(f"/api/run/{bad_id}/events")
    assert resp.status_code in (400, 404)


@pytest.mark.parametrize("bad_id", BAD_RUN_IDS)
def test_reports_rejects_bad_ids(tmp_path, bad_id):
    client = make_client(tmp_path)
    resp = client.get(f"/api/reports/{bad_id}")
    assert resp.status_code in (400, 404)


def test_events_unknown_run_is_404(tmp_path):
    client = make_client(tmp_path)
    assert client.get(f"/api/run/{RUN_ID_A}/events").status_code == 404


def test_reports_traversal_cannot_escape(tmp_path):
    (tmp_path / "secret.json").write_text("TOP-SECRET", encoding="utf-8")
    client = make_client(tmp_path)
    for bad_id in ["..%2F..%2Fsecret", "%2e%2e%2f%2e%2e%2fsecret", "....//secret"]:
        resp = client.get(f"/api/reports/{bad_id}")
        assert resp.status_code in (400, 404)
        assert "TOP-SECRET" not in resp.text


def test_report_route_serves_fixture_and_404s_unknown(tmp_path):
    report = make_report("20261013-083105-quick", "2026-10-13T08:31:05Z")
    write_report(tmp_path, report)
    client = make_client(tmp_path)
    resp = client.get("/api/reports/20261013-083105-quick")
    assert resp.status_code == 200
    assert resp.json() == report
    assert client.get("/api/reports/20261013-999999-quick").status_code == 404
    assert client.get("/api/reports/evil.json").status_code == 404


# ---------------------------------------------------------------------------
# SSE: replay, tail, close
# ---------------------------------------------------------------------------


def test_events_replays_finished_run(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="done", age_s=300)
    append_events(tmp_path, RUN_ID_A, make_events(RUN_ID_A))
    client = make_client(tmp_path)
    resp = client.get(f"/api/run/{RUN_ID_A}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = sse_stages(resp.text)
    assert [e["stage"] for e in events] == [
        "queued",
        "boot",
        "scenario",
        "scenario",
        "teardown",
        "done",
    ]
    assert events[2]["scenario"] == 1
    assert events[2]["name"] == "healthy-baseline"
    assert events[2]["status"] == "pass"
    assert events[5]["report_id"] == RUN_ID_A
    assert all("ts" in e for e in events)


def test_events_closes_when_terminal_without_terminal_event(tmp_path):
    write_queue_record(tmp_path, RUN_ID_A, status="failed", age_s=10)
    append_events(tmp_path, RUN_ID_A, [{"ts": _now_iso(), "stage": "queued"}])
    client = make_client(tmp_path)
    resp = client.get(f"/api/run/{RUN_ID_A}/events")
    assert resp.status_code == 200
    assert [e["stage"] for e in sse_stages(resp.text)] == ["queued"]


def test_events_tail_streams_new_events_until_done(tmp_path):
    """Drive the async generator directly: it must replay what exists, keep
    polling, then emit newly appended events and stop after ``done``.

    (The TestClient buffers streamed responses in this starlette version, so
    mid-stream observation goes through ``_event_stream`` itself.)"""
    write_queue_record(tmp_path, RUN_ID_A, status="running", age_s=0.1)
    append_events(
        tmp_path,
        RUN_ID_A,
        [{"ts": _now_iso(), "stage": "queued"}, {"ts": _now_iso(), "stage": "boot"}],
    )
    cfg = make_config(tmp_path, sse_poll_s=0.01)

    async def scenario() -> list[str]:
        generator = _event_stream(cfg, RUN_ID_A)
        chunks: list[str] = []

        async def pull() -> None:
            chunks.append(await asyncio.wait_for(generator.__anext__(), 2.0))

        await pull()  # replayed queued
        await pull()  # replayed boot (no terminal yet -> keeps polling)

        append_events(tmp_path, RUN_ID_A, make_events(RUN_ID_A)[2:])
        try:
            while True:
                await pull()
        except StopAsyncIteration:
            pass
        return chunks

    chunks = asyncio.run(scenario())
    events = sse_stages("\n".join(chunks))
    assert [e["stage"] for e in events] == [
        "queued",
        "boot",
        "scenario",
        "scenario",
        "teardown",
        "done",
    ]
    assert events[-1]["report_id"] == RUN_ID_A


# ---------------------------------------------------------------------------
# last-run / history
# ---------------------------------------------------------------------------


def test_last_run_null_without_reports(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/last-run").json() == {"report": None}
    assert client.get("/api/history").json() == []


def test_last_run_newest_by_mtime(tmp_path):
    older = make_report("20261013-080000-quick", "2026-10-13T09:00:00Z")
    newer = make_report("20261013-070000-quick", "2026-10-13T07:00:00Z")
    path_older = write_report(tmp_path, older)
    path_newer = write_report(tmp_path, newer)
    import os

    os.utime(path_older, (1_700_000_000, 1_700_000_000))
    os.utime(path_newer, (1_700_000_100, 1_700_000_100))
    client = make_client(tmp_path)
    assert client.get("/api/last-run").json() == {"report": newer}


def test_history_newest_first_and_shape(tmp_path):
    older = make_report("20261013-070000-quick", "2026-10-13T07:00:00Z", 61.5)
    newer = make_report("20261013-090000-quick", "2026-10-13T09:00:00Z", 58.4)
    write_report(tmp_path, newer)
    write_report(tmp_path, older)
    client = make_client(tmp_path)
    history = client.get("/api/history").json()
    assert [item["run_id"] for item in history] == [
        "20261013-090000-quick",
        "20261013-070000-quick",
    ]
    first = history[0]
    assert set(first) == {"run_id", "profile", "started_at", "duration_s", "summary"}
    assert first["profile"] == "quick"
    assert first["started_at"] == "2026-10-13T09:00:00Z"
    assert first["duration_s"] == 58.4
    assert first["summary"] == {"total": 8, "passed": 8, "failed": 0, "status": "pass"}


# ---------------------------------------------------------------------------
# docs + static serving
# ---------------------------------------------------------------------------


def test_docs_serves_markdown_strings(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "methodology.md").write_text("# Method", encoding="utf-8")
    (docs_dir / "scenarios.md").write_text("# Scenarios", encoding="utf-8")
    client = make_client(tmp_path)
    assert client.get("/api/docs").json() == {
        "methodology": "# Method",
        "scenarios": "# Scenarios",
    }


def test_docs_missing_files_are_empty_strings(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/api/docs")
    assert resp.status_code == 200
    assert resp.json() == {"methodology": "", "scenarios": ""}


def test_root_is_503_placeholder_without_static_build(tmp_path):
    client = make_client(tmp_path)
    for path in ("/", "/deep/link"):
        resp = client.get(path)
        assert resp.status_code == 503
        assert "not built yet" in resp.text


def test_spa_serves_assets_and_falls_back_to_index(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>SPA-INDEX-MARKER</html>", encoding="utf-8")
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    client = make_client(tmp_path)

    assert "SPA-INDEX-MARKER" in client.get("/").text
    assert "SPA-INDEX-MARKER" in client.get("/deep/spa/route").text
    assert "console.log('app')" in client.get("/assets/app.js").text

    # Unknown /api/* paths are JSON 404s, never the SPA index.
    unknown = client.get("/api/definitely-not-a-route")
    assert unknown.status_code == 404

    # Path traversal must not escape the static root.
    for bad in ("/..%2Fsecret.txt", "/%2e%2e%2fsecret.txt"):
        resp = client.get(bad)
        assert "TOP-SECRET" not in resp.text


# ---------------------------------------------------------------------------
# deploy invariants
# ---------------------------------------------------------------------------


def test_dockerfile_pins_single_uvicorn_worker():
    """The POST /api/run gate check-then-write is race-free only with one
    worker (see post_run); the Dockerfile must keep the explicit pin."""
    dockerfile = Path(__file__).resolve().parents[1] / "api" / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    compact = "".join(content.split())
    assert '"--workers","1"' in compact
