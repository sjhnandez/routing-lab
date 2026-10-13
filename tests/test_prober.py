"""Unit tests for the ``prober`` package - fully offline (no network).

The CLI is exercised through ``prober.__main__.main`` with an injected
``httpx.MockTransport``, a fixed ``now`` and an explicit env mapping, so no
subprocesses, no real HTTP and no dependency on the ambient environment.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from prober import history, rotation
from prober.__main__ import main
from prober.classify import CLASSES, classify
from prober.client import ProberClient
from prober.settings import ConfigError, load_settings

NOW = dt.datetime(2026, 10, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
TODAY = "2026-10-13"
YESTERDAY = "2026-10-12"
TEST_KEY = "sk-prober-test-dummy"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PROBER_HISTORY_DIR": str(tmp_path / "history"),
        "LITELLM_BASE_URL": "http://127.0.0.1:4001",
        "LITELLM_API_KEY": TEST_KEY,
        "PROBER_DAILY_CAP": "48",
        "PROBER_TIMEOUT_S": "5",
    }
    env.update(overrides)
    return env


def _entry(ts: str, cls: str = "ok", model: str = "m1") -> dict:
    return {
        "ts": ts,
        "model": model,
        "ok": cls == "ok",
        "class": cls,
        "http_status": 200 if cls == "ok" else None,
        "latency_ms": 12,
        "error_type": None,
        "budget": {"date": ts[:10], "count": 1, "cap": 48},
    }


def _completion_body(model: str) -> dict:
    return {
        "id": "chatcmpl-prober-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "pong"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _models_transport(
    models=("m1", "m2", "m3"),
    *,
    chat_status: int = 200,
    chat_body: dict | None = None,
    calls: list | None = None,
) -> httpx.MockTransport:
    """Mock transport serving /v1/models + /v1/chat/completions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path))
        assert request.headers.get("authorization") == f"Bearer {TEST_KEY}"
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"object": "list", "data": [{"id": m} for m in models]}
            )
        if request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content)
            expected = {
                "model": payload["model"],
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            }
            assert payload == expected
            if chat_status != 200:
                return httpx.Response(
                    chat_status, json=chat_body if chat_body is not None else {}
                )
            return httpx.Response(200, json=_completion_body(payload["model"]))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _exploding_transport() -> httpx.MockTransport:
    """Fails the test if any request is attempted at all."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected network call: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _read_lines(history_dir: Path, date: str) -> list[dict]:
    path = history.history_path(history_dir, date)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_settings_defaults():
    settings = load_settings(env={})
    assert settings.base_url == "http://127.0.0.1:4001"
    assert settings.api_key is None
    assert settings.history_dir == Path("prober/history")
    assert settings.daily_cap == 48
    assert settings.timeout_s == 30.0


def test_settings_env_overrides_and_slash_stripping():
    settings = load_settings(
        env={
            "LITELLM_BASE_URL": "http://router:4001/",
            "LITELLM_API_KEY": "  sk-abc  ",
            "PROBER_HISTORY_DIR": "  /tmp/prober-hist  ",
            "PROBER_DAILY_CAP": "5",
            "PROBER_TIMEOUT_S": "2.5",
        }
    )
    assert settings.base_url == "http://router:4001"
    assert settings.api_key == "sk-abc"
    assert settings.history_dir == Path("/tmp/prober-hist")
    assert settings.daily_cap == 5
    assert settings.timeout_s == 2.5


def test_settings_invalid_cap_raises_config_error():
    with pytest.raises(ConfigError):
        load_settings(env={"PROBER_DAILY_CAP": "abc"})
    with pytest.raises(ConfigError):
        load_settings(env={"PROBER_DAILY_CAP": "-1"})


def test_settings_invalid_timeout_raises_config_error():
    with pytest.raises(ConfigError):
        load_settings(env={"PROBER_TIMEOUT_S": "fast"})
    with pytest.raises(ConfigError):
        load_settings(env={"PROBER_TIMEOUT_S": "0"})


def test_settings_repr_hides_api_key():
    settings = load_settings(env={"LITELLM_API_KEY": "sk-super-secret"})
    assert settings.api_key == "sk-super-secret"
    assert "sk-super-secret" not in repr(settings)


# ---------------------------------------------------------------------------
# classify: full mapping table incl. precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "body", "exception", "expected"),
    [
        # status-based
        (200, {"choices": [{"message": {"content": "pong"}}]}, None, "ok"),
        (200, None, None, "ok"),
        (429, None, None, "rate_limited"),
        (500, None, None, "unavailable"),
        (501, None, None, "unavailable"),
        (502, None, None, "unavailable"),
        (503, None, None, "unavailable"),
        (504, None, None, "unavailable"),
        (404, None, None, "not_found"),
        (401, None, None, "auth"),
        (403, None, None, "auth"),
        (400, None, None, "other_4xx"),
        (422, None, None, "other_4xx"),
        # body error type beats status
        (500, {"error": {"type": "RateLimitError"}}, None, "rate_limited"),
        (429, {"error": {"type": "Timeout"}}, None, "timeout"),
        (404, {"error": {"type": "model_not_found"}}, None, "not_found"),
        (401, {"error": {"type": "invalid_api_key"}}, None, "auth"),
        (400, {"error": {"type": "context_length_exceeded"}}, None, "other_4xx"),
        (503, {"error": {"type": "InternalServerError"}}, None, "unavailable"),
        (503, {"error": {"type": "no_deployments_available"}}, None, "unavailable"),
        (500, {"error": {"type": "APIConnectionError"}}, None, "timeout"),
        # unknown / absent body type falls back to status
        (429, {"error": {"type": "who_knows"}}, None, "rate_limited"),
        (429, {"error": "plain text error"}, None, "rate_limited"),
        (200, "not-a-dict", None, "ok"),
        # exceptions (no response)
        (None, None, httpx.ReadTimeout("slow"), "timeout"),
        (None, None, httpx.ConnectTimeout("slow"), "timeout"),
        (None, None, httpx.ConnectError("refused"), "timeout"),
        (None, None, httpx.RemoteProtocolError("dropped"), "network"),
        (None, None, httpx.TransportError("generic"), "network"),
    ],
)
def test_classify_mapping(status_code, body, exception, expected):
    assert classify(status_code=status_code, body=body, exception=exception) == expected


def test_classify_timeout_beats_generic_network_precedence():
    # ConnectTimeout is a TransportError (would hit the generic network bucket)
    # but must classify as a timeout; connect errors group with timeouts.
    exc = httpx.ConnectTimeout("slow")
    assert isinstance(exc, httpx.TransportError)
    assert classify(exception=exc) == "timeout"
    assert classify(exception=httpx.ConnectError("refused")) == "timeout"


def test_classify_requires_input():
    with pytest.raises(ValueError):
        classify()


def test_classify_classes_constant_matches_expectations():
    assert set(CLASSES) == {
        "ok",
        "rate_limited",
        "unavailable",
        "timeout",
        "not_found",
        "auth",
        "other_4xx",
        "network",
    }


# ---------------------------------------------------------------------------
# rotation: advance + persistence (tmp dir)
# ---------------------------------------------------------------------------


def test_rotation_missing_and_invalid_state_is_zero(tmp_path):
    d = tmp_path / "history"
    assert rotation.read_rotation(d) == 0  # missing dir/file
    d.mkdir()
    (d / rotation.ROTATION_FILENAME).write_text("garbage\n", encoding="utf-8")
    assert rotation.read_rotation(d) == 0
    (d / rotation.ROTATION_FILENAME).write_text("-3\n", encoding="utf-8")
    assert rotation.read_rotation(d) == 0


def test_rotation_roundtrip_and_advance_wrap(tmp_path):
    d = tmp_path / "history"
    models = ["a", "b", "c"]
    rotation.write_rotation(d, 2)
    assert rotation.read_rotation(d) == 2
    position, model = rotation.pick(models, rotation.read_rotation(d))
    assert (position, model) == (2, "c")
    new_index = rotation.advance(2, len(models))
    rotation.write_rotation(d, new_index)
    assert new_index == 0
    assert rotation.read_rotation(d) == 0
    assert rotation.pick(models, 0) == (0, "a")


def test_rotation_stale_index_is_normalized(tmp_path):
    d = tmp_path / "history"
    rotation.write_rotation(d, 7)
    assert rotation.read_rotation(d) == 7
    assert rotation.pick(["a", "b", "c"], 7) == (1, "b")
    assert rotation.advance(7, 3) == 2


def test_rotation_pick_and_advance_reject_empty():
    with pytest.raises(ValueError):
        rotation.pick([], 0)
    with pytest.raises(ValueError):
        rotation.advance(0, 0)


def test_models_cache_roundtrip(tmp_path):
    d = tmp_path / "history"
    assert rotation.read_models(d) == []  # missing
    rotation.write_models(d, ["m1", "m2"])
    assert rotation.read_models(d) == ["m1", "m2"]
    (d / rotation.MODELS_FILENAME).write_text("not json", encoding="utf-8")
    assert rotation.read_models(d) == []
    (d / rotation.MODELS_FILENAME).write_text('{"nope": 1}', encoding="utf-8")
    assert rotation.read_models(d) == []


# ---------------------------------------------------------------------------
# history: append + aggregation
# ---------------------------------------------------------------------------


def test_history_append_writes_exact_jsonl_schema(tmp_path):
    d = tmp_path / "history"
    entry = _entry("2026-10-13T12:00:00Z", cls="ok", model="m1")
    path = history.append_entry(d, TODAY, entry)
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw.count("\n") == 1
    loaded = json.loads(raw)
    assert loaded == entry
    assert list(loaded.keys()) == [
        "ts",
        "model",
        "ok",
        "class",
        "http_status",
        "latency_ms",
        "error_type",
        "budget",
    ]
    assert list(loaded["budget"].keys()) == ["date", "count", "cap"]


def test_history_iter_entries_skips_malformed(tmp_path):
    d = tmp_path / "history"
    path = history.append_entry(d, TODAY, _entry("2026-10-13T11:00:00Z"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("{not json\n")
        fh.write('["a", "list"]\n')
        fh.write('{"ts": "2026-10-13T10:00:00Z", "class": "ok"}\n')
    entries = list(history.iter_entries(path))
    assert [e["ts"] for e in entries] == [
        "2026-10-13T11:00:00Z",
        "2026-10-13T10:00:00Z",
    ]
    assert history.count_today(d, TODAY) == 2


def test_history_count_today_missing_file_is_zero(tmp_path):
    assert history.count_today(tmp_path / "history", TODAY) == 0


def test_history_window_counts_fixture(tmp_path):
    d = tmp_path / "history"
    history.append_entry(d, TODAY, _entry("2026-10-13T11:00:00Z"))
    history.append_entry(d, TODAY, _entry("2026-10-13T09:00:00Z", cls="rate_limited"))
    history.append_entry(d, TODAY, _entry("2026-10-12T13:00:00Z", cls="timeout"))
    history.append_entry(d, YESTERDAY, _entry("2026-10-12T13:00:00Z", cls="unavailable"))
    history.append_entry(d, YESTERDAY, _entry("2026-10-12T11:00:00Z"))  # 25h old
    counts, total = history.window_counts(d, NOW)
    assert counts == {
        "ok": 1,
        "rate_limited": 1,
        "unavailable": 1,
        "timeout": 1,
        "not_found": 0,
        "auth": 0,
        "other_4xx": 0,
        "network": 0,
    }
    assert total == 4
    assert history.count_today(d, TODAY) == 3


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


def _client_with(handler) -> ProberClient:
    return ProberClient(
        "http://lab.invalid", TEST_KEY, 5.0, transport=httpx.MockTransport(handler)
    )


def test_client_fetch_models_returns_ids_and_sends_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "m1"}, {"id": ""}, {"nope": 1}, "junk"],
            },
        )

    client = _client_with(handler)
    try:
        models = client.fetch_models()
    finally:
        client.close()
    assert models == ["m1"]
    assert seen == {"auth": f"Bearer {TEST_KEY}", "path": "/v1/models"}


def test_client_fetch_models_error_paths():
    client = _client_with(
        lambda request: httpx.Response(503, json={"error": {"message": "down"}})
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_models()
    client.close()

    client = _client_with(lambda request: httpx.Response(200, text="not json"))
    with pytest.raises(ValueError):
        client.fetch_models()
    client.close()

    client = _client_with(lambda request: httpx.Response(200, json={"object": "list"}))
    with pytest.raises(ValueError):
        client.fetch_models()
    client.close()


def test_client_probe_transport_error_is_classified_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with(handler)
    try:
        result = client.probe("m1")
    finally:
        client.close()
    assert result.ok is False
    assert result.cls == "timeout"  # connect errors group with timeouts
    assert result.http_status is None
    assert result.error_type == "ConnectError"
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# CLI: default live run (rotation advance across runs)
# ---------------------------------------------------------------------------


def test_default_run_probes_next_model_records_and_advances(tmp_path):
    d = tmp_path / "history"
    env = _env(tmp_path)
    calls: list = []

    code = main([], transport=_models_transport(calls=calls), now=NOW, env=env)
    assert code == 0
    assert ("GET", "/v1/models") in calls
    assert ("POST", "/v1/chat/completions") in calls

    lines = _read_lines(d, TODAY)
    assert len(lines) == 1
    entry = lines[0]
    assert list(entry.keys()) == [
        "ts",
        "model",
        "ok",
        "class",
        "http_status",
        "latency_ms",
        "error_type",
        "budget",
    ]
    assert entry["ts"] == "2026-10-13T12:00:00Z"
    assert entry["model"] == "m1"
    assert entry["ok"] is True
    assert entry["class"] == "ok"
    assert entry["http_status"] == 200
    assert isinstance(entry["latency_ms"], int)
    assert entry["error_type"] is None
    assert entry["budget"] == {"date": TODAY, "count": 1, "cap": 48}
    assert rotation.read_rotation(d) == 1
    assert rotation.read_models(d) == ["m1", "m2", "m3"]

    # Second run probes the NEXT model, count keeps incrementing.
    code = main([], transport=_models_transport(calls=None), now=NOW, env=env)
    assert code == 0
    lines = _read_lines(d, TODAY)
    assert [e["model"] for e in lines] == ["m1", "m2"]
    assert lines[1]["budget"]["count"] == 2
    assert rotation.read_rotation(d) == 2


def test_probe_specific_model_skips_models_fetch_and_rotation(tmp_path):
    d = tmp_path / "history"
    env = _env(tmp_path)
    calls: list = []

    code = main(["--probe", "special-model"], transport=_models_transport(calls=calls), now=NOW, env=env)
    assert code == 0
    assert calls == [("POST", "/v1/chat/completions")]
    lines = _read_lines(d, TODAY)
    assert [e["model"] for e in lines] == ["special-model"]
    assert lines[0]["budget"]["count"] == 1
    assert not (d / rotation.ROTATION_FILENAME).exists()
    assert not (d / rotation.MODELS_FILENAME).exists()


def test_rate_limited_probe_recorded_and_exits_0(tmp_path):
    env = _env(tmp_path)
    transport = _models_transport(
        ("m1",),
        chat_status=429,
        chat_body={"error": {"message": "slow down", "type": "RateLimitError"}},
    )
    code = main([], transport=transport, now=NOW, env=env)
    assert code == 0
    lines = _read_lines(tmp_path / "history", TODAY)
    entry = lines[0]
    assert entry["class"] == "rate_limited"
    assert entry["ok"] is False
    assert entry["http_status"] == 429
    assert entry["error_type"] == "RateLimitError"


def test_models_fetch_failure_exits_1_and_records_nothing(tmp_path):
    env = _env(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    code = main([], transport=httpx.MockTransport(handler), now=NOW, env=env)
    assert code == 1
    assert not (tmp_path / "history").exists()


# ---------------------------------------------------------------------------
# CLI: budget guard (under / at / over cap via return codes)
# ---------------------------------------------------------------------------


def test_budget_under_cap_probes_and_counts_after(tmp_path):
    d = tmp_path / "history"
    env = _env(tmp_path, PROBER_DAILY_CAP="3")
    history.append_entry(d, TODAY, _entry("2026-10-13T09:00:00Z"))

    code = main([], transport=_models_transport(("m1",)), now=NOW, env=env)
    assert code == 0
    lines = _read_lines(d, TODAY)
    assert len(lines) == 2
    assert lines[-1]["budget"] == {"date": TODAY, "count": 2, "cap": 3}


def test_budget_at_cap_exits_3_without_probe(tmp_path, capsys):
    d = tmp_path / "history"
    env = _env(tmp_path, PROBER_DAILY_CAP="2")
    history.append_entry(d, TODAY, _entry("2026-10-13T09:00:00Z"))
    history.append_entry(d, TODAY, _entry("2026-10-13T10:00:00Z"))

    code = main([], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 3
    assert "daily cap reached (2/2)" in out
    assert history.count_today(d, TODAY) == 2  # nothing added


def test_budget_over_cap_exits_3_without_probe(tmp_path, capsys):
    d = tmp_path / "history"
    env = _env(tmp_path, PROBER_DAILY_CAP="2")
    for hour in ("09", "10", "11"):
        history.append_entry(d, TODAY, _entry(f"2026-10-13T{hour}:00:00Z"))

    code = main([], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 3
    assert "daily cap reached (3/2)" in out
    assert history.count_today(d, TODAY) == 3


def test_budget_guard_applies_to_probe_flag(tmp_path, capsys):
    env = _env(tmp_path, PROBER_DAILY_CAP="1")
    history.append_entry(tmp_path / "history", TODAY, _entry("2026-10-13T09:00:00Z"))

    code = main(["--probe", "m1"], transport=_exploding_transport(), now=NOW, env=env)
    assert code == 3
    assert "daily cap reached (1/1)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI: configuration errors
# ---------------------------------------------------------------------------


def test_live_probe_without_key_exits_2(tmp_path, capsys):
    env = _env(tmp_path, LITELLM_API_KEY="")
    code = main([], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 2
    assert "LITELLM_API_KEY" in out
    assert not (tmp_path / "history").exists()


def test_probe_flag_without_key_exits_2(tmp_path, capsys):
    env = _env(tmp_path, LITELLM_API_KEY="")
    code = main(["--probe", "m1"], transport=_exploding_transport(), now=NOW, env=env)
    assert code == 2
    assert "LITELLM_API_KEY" in capsys.readouterr().out


def test_invalid_cap_env_exits_2(tmp_path, capsys):
    env = _env(tmp_path, PROBER_DAILY_CAP="nope")
    code = main([], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 2
    assert "PROBER_DAILY_CAP" in out


# ---------------------------------------------------------------------------
# CLI: dry-run (zero network, zero writes)
# ---------------------------------------------------------------------------


def test_dry_run_zero_network_zero_writes(tmp_path, capsys):
    env = _env(tmp_path, LITELLM_API_KEY="")
    code = main(["--dry-run"], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 0
    assert "http://127.0.0.1:4001" in out
    assert "position 0 of 0" in out
    assert "0/48" in out
    assert "would probe:" in out
    assert not (tmp_path / "history").exists()


def test_dry_run_uses_cached_models_and_writes_nothing(tmp_path, capsys):
    d = tmp_path / "history"
    rotation.write_models(d, ["a", "b", "c"])
    rotation.write_rotation(d, 2)
    before = sorted(p.name for p in d.iterdir())

    env = _env(tmp_path, LITELLM_API_KEY="")
    code = main(["--dry-run"], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 0
    assert "position 2 of 3" in out
    assert "would probe: c" in out
    assert rotation.read_rotation(d) == 2
    assert sorted(p.name for p in d.iterdir()) == before


# ---------------------------------------------------------------------------
# CLI: status (history only)
# ---------------------------------------------------------------------------


def test_status_empty_history_exits_0_with_zeros(tmp_path, capsys):
    env = _env(tmp_path)
    code = main(["--status"], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 0
    assert "probes:" in out
    assert "0/48" in out
    assert not (tmp_path / "history").exists()


def test_status_aggregates_last_24h_from_fixture(tmp_path, capsys):
    d = tmp_path / "history"
    history.append_entry(d, TODAY, _entry("2026-10-13T11:00:00Z"))
    history.append_entry(d, TODAY, _entry("2026-10-13T09:00:00Z", cls="rate_limited"))
    history.append_entry(d, TODAY, _entry("2026-10-12T13:00:00Z", cls="timeout"))
    path = history.history_path(d, TODAY)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    env = _env(tmp_path)
    code = main(["--status"], transport=_exploding_transport(), now=NOW, env=env)
    out = capsys.readouterr().out
    assert code == 0
    assert set(CLASSES) == {
        "ok",
        "rate_limited",
        "unavailable",
        "timeout",
        "not_found",
        "auth",
        "other_4xx",
        "network",
    }
    assert "3/48" in out  # today's count ignores the malformed line
    assert "probes:" in out
