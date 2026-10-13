"""Unit tests for ``GET /api/fleet`` (prober history aggregation in ``lab-api``).

Fully in-process via ``fastapi.testclient.TestClient`` against an app built
with tmp dirs; the prober history directory is a tmp fixture, so nothing here
touches a real prober run or the network. Fixture files mirror the prober's
JSONL format (``probes-YYYY-MM-DD.jsonl``, one probe object per line).

Timestamp offsets are hours, not seconds, so the rolling 24h window math is
stable against test/server clock drift. The parser reads the newest two daily
files; the fixtures below are built so all intended lines live in at most two
date buckets (see ``write_history``), which keeps this deterministic at any
time of day.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import Config, create_app

# Pinned copy of the prober's failure taxonomy (contract shape for counts).
CLASSES = (
    "ok",
    "rate_limited",
    "unavailable",
    "timeout",
    "not_found",
    "auth",
    "other_4xx",
    "network",
)


def _iso(hours_ago: float) -> str:
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def make_client(tmp_path: Path, history_dir: Path) -> TestClient:
    cfg = Config(
        queue_dir=tmp_path / "queue",
        reports_dir=tmp_path / "reports",
        docs_dir=tmp_path / "docs",
        static_dir=tmp_path / "static",
        prober_history_dir=history_dir,
    )
    return TestClient(create_app(cfg))


def probe_json(
    ts: str,
    model: str,
    cls: str,
    *,
    count: int = 1,
    cap: int = 48,
    budget: bool = True,
) -> str:
    entry = {
        "ts": ts,
        "model": model,
        "ok": cls == "ok",
        "class": cls,
        "http_status": 200 if cls == "ok" else 429,
        "latency_ms": 120,
        "error_type": None if cls == "ok" else "RateLimitError",
    }
    if budget:
        entry["budget"] = {"date": ts[:10], "count": count, "cap": cap}
    return json.dumps(entry)


def write_history(history_dir: Path, dated_lines: list[tuple[str, str]]) -> None:
    """Write raw JSONL lines into ``probes-<date>.jsonl``, grouped by date.

    ``dated_lines`` entries are ``(date, raw_line)`` in chronological order;
    lines sharing a date are appended in that order, mirroring the prober.
    """
    history_dir.mkdir(parents=True, exist_ok=True)
    by_date: dict[str, list[str]] = {}
    for date, line in dated_lines:
        by_date.setdefault(date, []).append(line)
    for date, lines in by_date.items():
        (history_dir / f"probes-{date}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def dated(ts: str, model: str, cls: str, **kwargs) -> tuple[str, str]:
    return ts[:10], probe_json(ts, model, cls, **kwargs)


def test_empty_dir_reports_unavailable(tmp_path):
    history = tmp_path / "prober-history"
    history.mkdir()
    client = make_client(tmp_path, history)

    res = client.get("/api/fleet")

    assert res.status_code == 200
    body = res.json()
    assert body["generated_at"].endswith("Z")
    assert body["available"] is False
    assert body["note"] == "prober has not run yet"
    assert body["budget"] == {"date": _today(), "count": 0, "cap": 48}
    assert body["last_24h"] == {
        "total": 0,
        "ok": 0,
        "ok_rate": 0.0,
        "counts": {name: 0 for name in CLASSES},
    }
    assert body["models"] == []
    assert body["recent"] == []


def test_missing_history_dir_still_200(tmp_path):
    client = make_client(tmp_path, tmp_path / "does-not-exist")

    res = client.get("/api/fleet")

    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["note"] == "prober has not run yet"
    assert body["budget"]["cap"] == 48
    assert body["last_24h"]["total"] == 0
    assert body["recent"] == []


def test_malformed_lines_only_reports_unavailable(tmp_path):
    history = tmp_path / "prober-history"
    date = _today()
    write_history(
        history,
        [
            (date, ""),
            (date, "not json at all"),
            (date, "[1, 2, 3]"),
            (date, '{"model": "x", "class": "ok"}'),  # valid JSON, no ts
            (date, '{"ts": "not-a-timestamp", "model": "x", "class": "ok"}'),
        ],
    )
    client = make_client(tmp_path, history)

    body = client.get("/api/fleet").json()

    assert body["available"] is False
    assert body["note"] == "prober has not run yet"
    assert body["models"] == []
    assert body["recent"] == []
    assert body["last_24h"]["total"] == 0


def test_fixture_aggregates_budget_window_models_recent(tmp_path):
    """Valid + malformed lines across files: budget, 24h window, model sort,
    newest-first recent; malformed lines and files older than the newest two
    are excluded."""
    history = tmp_path / "prober-history"
    t0 = _iso(25)         # outside the window, still parsed (newest two files)
    t1 = _iso(23)
    t2 = _iso(5)
    t3 = _iso(3)
    t4 = _iso(1)
    t_old = _iso(72)      # third file: must be ignored entirely
    lines = [
        dated(t_old, "delta", "ok"),
        dated(t0, "gamma", "timeout"),
        dated(t1, "beta", "rate_limited"),
        dated(t2, "alpha", "unavailable"),
        dated(t3, "alpha", "ok"),
        dated(t4, "beta", "ok", count=12, cap=40),
        # Malformed trailing lines must be skipped, not crash the parse.
        (t4[:10], "not json at all"),
        (t4[:10], ""),
        (t4[:10], '{"ts": "garbage", "model": "alpha", "class": "ok"}'),
    ]
    write_history(history, lines)
    client = make_client(tmp_path, history)

    body = client.get("/api/fleet").json()

    assert body["available"] is True
    assert body["note"] is None
    # Budget comes from the latest probe line (count 12, cap 40), not defaults.
    assert body["budget"] == {"date": t4[:10], "count": 12, "cap": 40}

    window = body["last_24h"]
    assert window["total"] == 4
    assert window["ok"] == 2
    assert window["ok_rate"] == pytest.approx(0.5)
    assert set(window["counts"]) == set(CLASSES)
    assert window["counts"]["ok"] == 2
    assert window["counts"]["rate_limited"] == 1
    assert window["counts"]["unavailable"] == 1
    assert window["counts"]["timeout"] == 0  # t0 is outside the window

    # Sorted by probes_24h desc, then model asc; gamma (stale) keeps last_* info.
    assert body["models"] == [
        {"model": "alpha", "probes_24h": 2, "ok_24h": 1, "last_class": "ok", "last_ts": t3},
        {"model": "beta", "probes_24h": 2, "ok_24h": 1, "last_class": "ok", "last_ts": t4},
        {"model": "gamma", "probes_24h": 0, "ok_24h": 0, "last_class": "timeout", "last_ts": t0},
    ]

    recent = body["recent"]
    assert [entry["ts"] for entry in recent] == [t4, t3, t2, t1, t0]
    assert recent[0]["budget"] == {"date": t4[:10], "count": 12, "cap": 40}
    assert all(entry.get("model") != "delta" for entry in recent)


def test_recent_capped_at_50_newest_first(tmp_path):
    history = tmp_path / "prober-history"
    stamps = [_iso((60 - i) / 60) for i in range(60)]  # 1 min apart, ascending
    write_history(
        history,
        [dated(ts, "flux", "ok") for ts in stamps],
    )
    client = make_client(tmp_path, history)

    body = client.get("/api/fleet").json()

    recent = body["recent"]
    assert len(recent) == 50
    expected = list(reversed(stamps))[:50]
    assert [entry["ts"] for entry in recent] == expected
    assert body["last_24h"]["total"] == 60
    assert body["last_24h"]["ok_rate"] == pytest.approx(1.0)
    assert body["models"] == [
        {"model": "flux", "probes_24h": 60, "ok_24h": 60, "last_class": "ok", "last_ts": stamps[-1]}
    ]


def test_history_older_than_24h_still_reports_budget_and_recent(tmp_path):
    """Documented behavior: ``available`` reflects any parsed history, while
    ``last_24h`` is windowed. A stale history still returns its recorded
    budget, per-model last class and the raw recent lines (probes_24h = 0)."""
    history = tmp_path / "prober-history"
    t_a = _iso(30)
    t_b = _iso(26)
    write_history(
        history,
        [
            dated(t_a, "legacy", "unavailable"),
            dated(t_b, "legacy", "timeout", count=6),
        ],
    )
    client = make_client(tmp_path, history)

    body = client.get("/api/fleet").json()

    assert body["available"] is True
    assert body["note"] is None
    window = body["last_24h"]
    assert window["total"] == 0
    assert window["ok"] == 0
    assert window["ok_rate"] == 0.0
    assert window["counts"] == {name: 0 for name in CLASSES}
    assert body["budget"] == {"date": t_b[:10], "count": 6, "cap": 48}
    assert body["models"] == [
        {"model": "legacy", "probes_24h": 0, "ok_24h": 0, "last_class": "timeout", "last_ts": t_b}
    ]
    assert [entry["ts"] for entry in body["recent"]] == [t_b, t_a]


def test_budget_fallback_without_recorded_budget(tmp_path):
    """Lines with no ``budget`` object: date comes from the latest ts and
    count is the number of valid lines on that date; cap defaults to 48."""
    history = tmp_path / "prober-history"
    t1 = _iso(2)
    t2 = _iso(1)
    write_history(
        history,
        [
            dated(t1, "plain", "ok", budget=False),
            dated(t2, "plain", "ok", budget=False),
        ],
    )
    client = make_client(tmp_path, history)

    body = client.get("/api/fleet").json()

    expected_date = t2[:10]
    expected_count = sum(1 for ts in (t1, t2) if ts[:10] == expected_date)
    assert body["budget"] == {"date": expected_date, "count": expected_count, "cap": 48}
    assert body["available"] is True
