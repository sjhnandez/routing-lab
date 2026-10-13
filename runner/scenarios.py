"""The eight hard-assertion scenarios.

Every scenario is deterministic and self-contained:

1. reset the mock journal (``DELETE /__hits``) so the evaluation window is clean,
2. issue the requests against the lab proxy,
3. wait a short settle period (the mock records cancellation outcomes
   asynchronously once litellm kills an upstream stream), fetch the journal,
4. evaluate hard assertions and return a result.

The mock journal is the single attribution source: litellm stdout never names
which deployment stalled or failed (see docs/methodology.md).

Integrity: assertions encode the plan's claims with empirically calibrated
bounds. They are never weakened to force a pass - if a claim fails against the
real stack, the scenario fails and the report carries the raw evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .client import CompletionOutcome, LabClient, MockClient, StreamOutcome, excerpt
from .report import AssertionResult, ScenarioResult, Timeline
from .settings import LabSettings

# Settle delay after the client steps: gives the mock a moment to flush
# cancellation (aborted) journal entries before we read the journal.
SETTLE_S = 0.75
# S5 reads the journal twice mid-scenario; keep those delays tight so request B
# still falls inside the quick profile's 8s cooldown window.
SETTLE_S5_S = 0.5
# S6 reads the journal after each request (per-request hit windows); same tight
# settle so the deliberate cooldown wait can start as early as possible.
SETTLE_S6_S = 0.5
# S5 request C (cooldown-expired re-hit) is an optional extra claim. It is kept
# only while it passes reliably; flip to False to drop it entirely.
S5_EXPIRY_CHECK = True

USER_MESSAGE = [{"role": "user", "content": "Hello from the routing-lab battery."}]


@dataclass(frozen=True)
class ScenarioSpec:
    id: int
    name: str
    expected: str
    run: Callable[["Context"], ScenarioResult]


class Context:
    """Shared clients, settings and timeline state for all scenarios."""

    def __init__(self, settings: LabSettings, lab: LabClient, mock: MockClient):
        self.settings = settings
        self.lab = lab
        self.mock = mock
        self.timeline = Timeline()

    def new_timeline(self) -> Timeline:
        self.timeline = Timeline()
        return self.timeline

    def reset_journal(self, tl: Timeline) -> None:
        self.mock.reset_journal()
        tl.add("journal_reset", "DELETE /__hits ok")

    def fetch_journal(self, tl: Timeline, settle_s: float = SETTLE_S) -> list[dict[str, Any]]:
        time.sleep(settle_s)
        hits = self.mock.fetch_journal()
        tl.add("journal_fetched", f"{len(hits)} entries after {settle_s:.2f}s settle")
        return hits


# ---------------------------------------------------------------------------
# Journal helpers
# ---------------------------------------------------------------------------


def _entries(journal: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    return [e for e in journal if e.get("mode") == mode]


def _completed(journal: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    return [e for e in journal if e.get("mode") == mode and e.get("outcome") == "completed"]


def _mode_counts(journal: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in journal:
        mode = str(entry.get("mode"))
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _counts_str(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{mode} x{n}" for mode, n in counts.items())


def _chunks_sent(journal: list[dict[str, Any]], mode: str) -> list[Any]:
    return [e.get("chunks_sent") for e in _entries(journal, mode)]


def _outcomes(journal: list[dict[str, Any]], mode: str) -> list[Any]:
    return [e.get("outcome") for e in _entries(journal, mode)]


def _served_by(journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"mode": mode, "hits": n} for mode, n in _mode_counts(journal).items()]


def _router_note(outcome: Any) -> str:
    """One-line note for litellm's router-side retry/fallback headers (if any)."""
    retries = getattr(outcome, "attempted_retries", None)
    fallbacks = getattr(outcome, "attempted_fallbacks", None)
    if retries is None and fallbacks is None:
        return "router headers: absent"
    return (
        f"router headers: attempted_retries="
        f"{retries if retries is not None else 'absent'} "
        f"attempted_fallbacks={fallbacks if fallbacks is not None else 'absent'}"
    )


def _make_result(
    sid: int,
    name: str,
    expected: str,
    assertions: list[AssertionResult],
    tl: Timeline,
    journal: list[dict[str, Any]],
    duration_s: float,
    observed: str,
) -> ScenarioResult:
    passed = bool(assertions) and all(a.passed for a in assertions)
    return ScenarioResult(
        id=sid,
        name=name,
        status="pass" if passed else "fail",
        duration_s=duration_s,
        expected=expected,
        observed=observed,
        assertions=assertions,
        timeline_events=list(tl.events),
        served_by=_served_by(journal),
        mocked_hits=journal,
    )


# ---------------------------------------------------------------------------
# S1 healthy-baseline
# ---------------------------------------------------------------------------

S1_EXPECTED = (
    "free-chat (complexity router) streams a clean completion; mode-healthy "
    "non-stream returns HTTP 200; every journaled hit is completed"
)


def scenario_healthy_baseline(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    stream = ctx.lab.chat_stream("free-chat", USER_MESSAGE, emit=tl.add)
    nonstream = ctx.lab.chat_once("mode-healthy", USER_MESSAGE, emit=tl.add)
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    healthy = _entries(journal, "healthy")
    bad = [e for e in journal if e.get("outcome") != "completed"]
    assertions = [
        AssertionResult(
            "free-chat stream completes with [DONE] and non-empty content",
            stream.success,
            f"status={stream.status_code} done={stream.done} "
            f"chunks={stream.content_chunks} content_len={len(stream.content)}",
        ),
        AssertionResult(
            "mode-healthy non-stream returns HTTP 200 with content",
            nonstream.success,
            f"status={nonstream.status_code} content={excerpt(nonstream.content)}",
        ),
        AssertionResult(
            "journal: >= 2 healthy hits, every entry completed (no errored/aborted)",
            len(healthy) >= 2 and not bad,
            f"entries={len(journal)} ({_counts_str(_mode_counts(journal))}) "
            f"non_completed={len(bad)}",
        ),
        AssertionResult(
            "scenario duration < 10s",
            duration < 10.0,
            f"duration={duration:.2f}s",
        ),
    ]
    observed = (
        f"free-chat: HTTP {stream.status_code}, {stream.content_chunks} chunks, "
        f"done={stream.done}; mode-healthy: HTTP {nonstream.status_code}; "
        f"journal: {_counts_str(_mode_counts(journal))}; total {duration:.2f}s"
    )
    return _make_result(1, "healthy-baseline", S1_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# S2 stall-pre-reroute
# ---------------------------------------------------------------------------

S2_EXPECTED = (
    "stall before first chunk -> stream silence kill -> reroute via fallbacks; "
    "client sees a complete success and the journal attributes the abort to "
    "stall-pre and the completion to a healthy deployment"
)


def scenario_stall_pre_reroute(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    stream = ctx.lab.chat_stream("mode-stall-pre", USER_MESSAGE, emit=tl.add)
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    stalled = _entries(journal, "stall-pre")
    healthy = _completed(journal, "healthy")
    stalled_ok = len(stalled) >= 1 and all(
        e.get("outcome") == "aborted" and e.get("chunks_sent") == 0 for e in stalled
    )
    assertions = [
        AssertionResult(
            "client gets a complete success via fallback (no error surfaced)",
            stream.success,
            f"status={stream.status_code} done={stream.done} "
            f"chunks={stream.content_chunks} error={stream.error_event or 'none'}",
        ),
        AssertionResult(
            "journal: stall-pre hit(s) aborted with chunks_sent == 0",
            stalled_ok,
            f"stall-pre hits={len(stalled)} "
            f"outcomes={_outcomes(journal, 'stall-pre')} "
            f"chunks_sent={_chunks_sent(journal, 'stall-pre')}",
        ),
        AssertionResult(
            "journal: healthy hit(s) completed (fallback attribution)",
            len(healthy) >= 1,
            f"healthy completed={len(healthy)}",
        ),
        AssertionResult(
            "scenario duration < 30s",
            duration < 30.0,
            f"duration={duration:.2f}s",
        ),
    ]
    observed = (
        f"client success via fallback: done={stream.done}, "
        f"{stream.content_chunks} chunks; stall-pre aborted x{len(stalled)} "
        f"(chunks_sent=0); healthy completed x{len(healthy)}; total {duration:.2f}s"
    )
    return _make_result(2, "stall-pre-reroute", S2_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# S3 stall-mid-surface-error
# ---------------------------------------------------------------------------

S3_EXPECTED = (
    "stall after content started -> litellm cannot un-send chunks: the turn "
    "fails for the client and there is no healthy fallback hit"
)


def scenario_stall_mid_surface_error(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    stream = ctx.lab.chat_stream("mode-stall-mid", USER_MESSAGE, emit=tl.add)
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    stalled = _entries(journal, "stall-mid")
    healthy = _entries(journal, "healthy")
    mid_ok = len(stalled) >= 1 and any(
        e.get("outcome") == "aborted" and (e.get("chunks_sent") or 0) >= 1
        for e in stalled
    )
    assertion_2 = AssertionResult(
        "turn fails: no successful [DONE] completion; in-band SSE error event surfaced",
        not stream.success and stream.error_event is not None,
        f"manifestation={stream.manifestation()}; "
        f"error_event={stream.error_event or 'none'}; done={stream.done}; "
        f"status={stream.status_code}",
    )
    assertions = [
        AssertionResult(
            "client receives >= 1 content chunk before the failure",
            stream.content_chunks >= 1,
            f"content_chunks={stream.content_chunks} content_len={len(stream.content)}",
        ),
        assertion_2,
        AssertionResult(
            "journal: stall-mid hit(s) aborted with chunks_sent >= 1",
            mid_ok,
            f"stall-mid hits={len(stalled)} "
            f"outcomes={_outcomes(journal, 'stall-mid')} "
            f"chunks_sent={_chunks_sent(journal, 'stall-mid')}",
        ),
        AssertionResult(
            "journal: no healthy hits (fallback impossible after content)",
            len(healthy) == 0,
            f"healthy hits={len(healthy)}",
        ),
    ]
    observed = (
        f"client: {stream.content_chunks} chunks then {stream.manifestation()}; "
        f"stall-mid aborted x{len(stalled)} "
        f"(chunks_sent={_chunks_sent(journal, 'stall-mid')}); healthy x{len(healthy)}; "
        f"total {duration:.2f}s"
    )
    return _make_result(3, "stall-mid-surface-error", S3_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# S4 slow-steady-survives
# ---------------------------------------------------------------------------

S4_EXPECTED = (
    "steady chunks with gaps under the silence threshold: the stream is never "
    "killed despite running far longer than stream_timeout"
)


def scenario_slow_steady_survives(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    turn_start = time.monotonic()
    stream = ctx.lab.chat_stream("mode-slow-steady", USER_MESSAGE, emit=tl.add)
    turn_duration = time.monotonic() - turn_start
    tl.add("turn_duration", f"stream turn lasted {turn_duration:.2f}s")
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    slow = _entries(journal, "slow-steady")
    healthy = _entries(journal, "healthy")
    exactly_one = len(slow) == 1 and slow[0].get("outcome") == "completed"
    assertions = [
        AssertionResult(
            "client completes with [DONE] and non-empty content",
            stream.success,
            f"status={stream.status_code} done={stream.done} "
            f"chunks={stream.content_chunks} content_len={len(stream.content)}",
        ),
        AssertionResult(
            ">= 12 streamed content chunks (steady small gaps)",
            stream.content_chunks >= 12,
            f"content_chunks={stream.content_chunks}",
        ),
        AssertionResult(
            "stream turn duration >= 9s (survived despite total > stream_timeout)",
            turn_duration >= 9.0,
            f"turn_duration={turn_duration:.2f}s",
        ),
        AssertionResult(
            "journal: exactly 1 slow-steady completed hit, no healthy hits (no reroute)",
            exactly_one and len(healthy) == 0,
            f"slow-steady hits={len(slow)} outcomes={_outcomes(journal, 'slow-steady')} "
            f"healthy hits={len(healthy)} total_entries={len(journal)}",
        ),
    ]
    observed = (
        f"stream success: {stream.content_chunks} chunks, done={stream.done}, "
        f"turn {turn_duration:.2f}s; journal: {_counts_str(_mode_counts(journal))}; "
        f"total {duration:.2f}s"
    )
    return _make_result(4, "slow-steady-survives", S4_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# S5 sat-429-cooldown-spillover
# ---------------------------------------------------------------------------

S5_EXPECTED = (
    "first 429 failure cools the deployment down; the immediate next request "
    "spills over to healthy-pool without hitting the cooling deployment, and "
    "after cooldown expiry the deployment is eligible again"
)


def scenario_sat_429_cooldown_spillover(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    # Request A: stream to the single-deployment 429 group -> retries, cooldown,
    # spillover to healthy-pool.
    tl.add("request_A", "stream mode-err-429")
    request_a = ctx.lab.chat_stream("mode-err-429", USER_MESSAGE, emit=tl.add)
    journal_a = ctx.fetch_journal(tl, settle_s=SETTLE_S5_S)
    a_hits = len(_entries(journal_a, "err-429"))
    a_healthy = len(_completed(journal_a, "healthy"))
    last_429_ts = max(
        (e["ts"] for e in _entries(journal_a, "err-429") if "ts" in e),
        default=None,
    )

    # Request B: immediately after - the err-429 deployment is in cooldown.
    tl.add("request_B", "non-stream mode-err-429 (during cooldown)")
    request_b = ctx.lab.chat_once("mode-err-429", USER_MESSAGE, emit=tl.add)
    journal_b = ctx.fetch_journal(tl, settle_s=SETTLE_S5_S)
    b_hits = len(_entries(journal_b, "err-429"))
    b_healthy = len(_completed(journal_b, "healthy"))
    delta_b = b_hits - a_hits
    tl.add("cooldown_check", f"err-429 hit delta after B = {delta_b} (expected 0)")

    # Request C (optional): after cooldown expiry the deployment is eligible again.
    # The wait is derived from the active profile's cooldown_time (+ 1.5s margin),
    # like S6: ~9.5s in quick, ~121.5s in full.
    cooldown_s = float(ctx.settings.router_settings.get("cooldown_time") or 8.0)
    request_c: CompletionOutcome | None = None
    delta_c: int | None = None
    journal_final = journal_b
    if S5_EXPIRY_CHECK and last_429_ts is not None:
        wait_s = (last_429_ts + cooldown_s + 1.5) - time.time()
        if wait_s > 0:
            tl.add("cooldown_wait", f"sleeping {wait_s:.2f}s until cooldown expiry")
            time.sleep(wait_s)
        tl.add("request_C", "non-stream mode-err-429 (after cooldown expiry)")
        request_c = ctx.lab.chat_once("mode-err-429", USER_MESSAGE, emit=tl.add)
        journal_final = ctx.fetch_journal(tl, settle_s=SETTLE_S5_S)
        delta_c = len(_entries(journal_final, "err-429")) - b_hits
        tl.add("cooldown_expired_check", f"err-429 hit delta after C = {delta_c}")

    duration = time.monotonic() - started

    assertions = [
        AssertionResult(
            "A: stream success via healthy fallback (429 -> cooldown -> spillover)",
            request_a.success,
            f"status={request_a.status_code} done={request_a.done} "
            f"chunks={request_a.content_chunks} error={request_a.error_event or 'none'}",
        ),
        AssertionResult(
            "A: journal err-429 hits >= 1 and healthy completed >= 1",
            a_hits >= 1 and a_healthy >= 1,
            f"err-429 hits={a_hits} healthy completed={a_healthy}",
        ),
        AssertionResult(
            "B (immediately after): client success",
            request_b.success,
            f"status={request_b.status_code} content={excerpt(request_b.content)}",
        ),
        AssertionResult(
            "B: err-429 hit delta == 0 while deployment is in cooldown",
            delta_b == 0,
            f"delta={delta_b} (A={a_hits} -> B={b_hits}); healthy completed {a_healthy} -> {b_healthy}",
        ),
    ]
    if S5_EXPIRY_CHECK:
        if request_c is not None and delta_c is not None:
            assertions.append(
                AssertionResult(
                    "C (after cooldown expiry): err-429 hit again, client success",
                    delta_c >= 1 and request_c.success,
                    f"delta={delta_c} status={request_c.status_code}",
                )
            )
        else:
            assertions.append(
                AssertionResult(
                    "C (after cooldown expiry): err-429 hit again, client success",
                    False,
                    "request C could not run: no err-429 hit timestamp from request A",
                )
            )

    observed = (
        f"A: err-429 x{a_hits}, healthy x{a_healthy}, client ok={request_a.success}; "
        f"B: delta={delta_b}, healthy x{b_healthy}, client ok={request_b.success}; "
        f"C: delta={delta_c}, client ok={request_c.success if request_c else 'n/a'}; "
        f"total {duration:.2f}s"
    )
    return _make_result(5, "sat-429-cooldown-spillover", S5_EXPECTED, assertions, tl, journal_final, duration, observed)


# ---------------------------------------------------------------------------
# S6 err-5xx-retry-reroute
# ---------------------------------------------------------------------------

S6_EXPECTED = (
    "5xx failures in the 2-deployment pools are retried against the healthy "
    "sibling (>= 2 upstream hits) before the fallback reroutes to healthy-pool; "
    "checked for stream (err-503, err-500) and non-stream (err-500) requests"
)


def scenario_err_5xx_retry_reroute(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    # Request 1: err-503 stream, exercised against its pool.
    tl.add("request_503", "stream mode-err-503")
    stream_503 = ctx.lab.chat_stream("mode-err-503", USER_MESSAGE, emit=tl.add)
    journal_1 = ctx.fetch_journal(tl, settle_s=SETTLE_S6_S)
    hits_503 = len(_entries(journal_1, "err-503"))
    healthy_503 = len(_completed(journal_1, "healthy"))

    # Request 2: err-500 stream (independent pool; not touched by request 1).
    tl.add("request_500_stream", "stream mode-err-500")
    stream_500 = ctx.lab.chat_stream("mode-err-500", USER_MESSAGE, emit=tl.add)
    journal_2 = ctx.fetch_journal(tl, settle_s=SETTLE_S6_S)
    hits_500_stream = len(_entries(journal_2, "err-500"))
    healthy_500_stream = len(_completed(journal_2, "healthy")) - healthy_503

    # Request 3: err-500 non-stream. Request 2 cooled both err-500 deployments
    # (allowed_fails: 0), so wait for the pool to leave cooldown first -
    # otherwise this request would be served by the fallback without touching
    # the pool and carry no retry evidence.
    cooldown_s = float(ctx.settings.router_settings.get("cooldown_time") or 8.0)
    last_500_ts = max(
        (e["ts"] for e in _entries(journal_2, "err-500") if "ts" in e),
        default=None,
    )
    if last_500_ts is not None:
        wait_s = (last_500_ts + cooldown_s + 1.5) - time.time()
        if wait_s > 0:
            tl.add(
                "cooldown_wait",
                f"sleeping {wait_s:.2f}s until the err-500 pool leaves cooldown",
            )
            time.sleep(wait_s)
    tl.add("request_500_nonstream", "non-stream mode-err-500 (after pool cooldown expiry)")
    nonstream_500 = ctx.lab.chat_once("mode-err-500", USER_MESSAGE, emit=tl.add)
    journal_final = ctx.fetch_journal(tl, settle_s=SETTLE_S6_S)
    hits_500_nonstream = len(_entries(journal_final, "err-500")) - hits_500_stream
    healthy_500_nonstream = (
        len(_completed(journal_final, "healthy")) - healthy_503 - healthy_500_stream
    )
    duration = time.monotonic() - started

    assertions = [
        AssertionResult(
            "err-503 stream: client success after pool retry + reroute",
            stream_503.success,
            f"status={stream_503.status_code} done={stream_503.done} "
            f"chunks={stream_503.content_chunks} error={stream_503.error_event or 'none'}; "
            f"{_router_note(stream_503)}",
        ),
        AssertionResult(
            "journal: err-503 hits >= 2 (retried against the sibling deployment)",
            hits_503 >= 2,
            f"err-503 hits={hits_503}",
        ),
        AssertionResult(
            "err-500 stream: client success after pool retry + reroute",
            stream_500.success,
            f"status={stream_500.status_code} done={stream_500.done} "
            f"chunks={stream_500.content_chunks} error={stream_500.error_event or 'none'}; "
            f"{_router_note(stream_500)}",
        ),
        AssertionResult(
            "journal: err-500 hits >= 2 for the stream request "
            "(retry reached the sibling deployment)",
            hits_500_stream >= 2,
            f"err-500 hits={hits_500_stream}; {_router_note(stream_500)}",
        ),
        AssertionResult(
            "err-500 non-stream: client success after pool retry + reroute",
            nonstream_500.success,
            f"status={nonstream_500.status_code} content={excerpt(nonstream_500.content)}; "
            f"{_router_note(nonstream_500)}",
        ),
        AssertionResult(
            "journal: err-500 hits >= 2 for the non-stream request "
            "(retry reached the sibling deployment)",
            hits_500_nonstream >= 2,
            f"err-500 hits={hits_500_nonstream} "
            f"(after {cooldown_s:.0f}s cooldown-expiry wait); "
            f"{_router_note(nonstream_500)}",
        ),
        AssertionResult(
            "journal: one healthy completion per rerouted request "
            "(fallback reached each time)",
            healthy_503 >= 1 and healthy_500_stream >= 1 and healthy_500_nonstream >= 1,
            f"healthy completed per step: {healthy_503}, {healthy_500_stream}, "
            f"{healthy_500_nonstream}",
        ),
    ]
    observed = (
        f"err-503 stream ok={stream_503.success} (hits={hits_503}); "
        f"err-500 stream ok={stream_500.success} (hits={hits_500_stream}); "
        f"err-500 non-stream ok={nonstream_500.success} (hits={hits_500_nonstream}); "
        f"healthy per step={healthy_503}/{healthy_500_stream}/{healthy_500_nonstream}; "
        f"total {duration:.2f}s"
    )
    return _make_result(
        6,
        "err-5xx-retry-reroute",
        S6_EXPECTED,
        assertions,
        tl,
        journal_final,
        duration,
        observed,
    )


# ---------------------------------------------------------------------------
# S7 err-404-no-retry
# ---------------------------------------------------------------------------

S7_EXPECTED = (
    "a 404 (NotFoundError) is never retried: exactly one upstream hit, "
    "regardless of num_retries/retry_policy"
)


def scenario_err_404_no_retry(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    request = ctx.lab.chat_once("mode-err-404", USER_MESSAGE, emit=tl.add)
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    err404 = _entries(journal, "err-404")
    healthy = _completed(journal, "healthy")
    if request.success:
        outcome_text = "fallback success via healthy-pool (HTTP 200)"
    elif request.exception:
        outcome_text = f"transport error ({request.exception})"
    else:
        outcome_text = (
            f"error surfaced to client (HTTP {request.status_code}: {request.error})"
        )
    assertions = [
        AssertionResult(
            "client succeeds via fallback (HTTP 200 with content)",
            request.success,
            f"status={request.status_code} content={excerpt(request.content)}",
        ),
        AssertionResult(
            "journal: exactly 1 err-404 hit (404 never retried)",
            len(err404) == 1,
            f"err-404 hits={len(err404)} "
            f"outcomes={_outcomes(journal, 'err-404')} "
            f"chunks_sent={_chunks_sent(journal, 'err-404')}",
        ),
        AssertionResult(
            "journal: >= 1 completed healthy hit (fallback rerouted)",
            len(healthy) >= 1,
            f"healthy hits={len(healthy)}",
        ),
    ]
    observed = (
        f"client outcome: {outcome_text}; err-404 hits={len(err404)}; "
        f"healthy hits={len(healthy)}; total {duration:.2f}s"
    )
    return _make_result(7, "err-404-no-retry", S7_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# S8 sat-all-failing-bounded
# ---------------------------------------------------------------------------

S8_EXPECTED = (
    "a group whose every deployment fails (and chains to another all-failing "
    "group) fails the client turn in bounded time: retried against the pool "
    "siblings, no healthy hit, no hang"
)


def scenario_sat_all_failing_bounded(ctx: Context) -> ScenarioResult:
    tl = ctx.new_timeline()
    started = time.monotonic()
    ctx.reset_journal(tl)

    turn_start = time.monotonic()
    stream = ctx.lab.chat_stream("sat-all-failing", USER_MESSAGE, emit=tl.add)
    turn_duration = time.monotonic() - turn_start
    tl.add("turn_duration", f"failing turn lasted {turn_duration:.2f}s")
    journal = ctx.fetch_journal(tl)
    duration = time.monotonic() - started

    err503 = len(_entries(journal, "err-503"))
    err429 = len(_entries(journal, "err-429"))
    healthy = len(_entries(journal, "healthy"))
    tl.add(
        "journal_counts",
        f"err-503 x{err503}, err-429 x{err429}, healthy x{healthy}",
    )
    assertions = [
        AssertionResult(
            "client eventually receives an error (no success)",
            not stream.success,
            f"manifestation={stream.manifestation()}; {_router_note(stream)}",
        ),
        AssertionResult(
            "bounded failure: turn duration < 60s (no hang)",
            turn_duration < 60.0,
            f"turn_duration={turn_duration:.2f}s",
        ),
        AssertionResult(
            "journal: err-503 hits >= 2 (retried against the sibling in the "
            "all-failing pool)",
            err503 >= 2,
            f"err-503 hits={err503}",
        ),
        AssertionResult(
            "journal: err-429 hits >= 2 (backup pool retried against its "
            "sibling before total exhaustion)",
            err429 >= 2,
            f"err-429 hits={err429}",
        ),
        AssertionResult(
            "journal: no healthy hits (sat groups have no healthy member)",
            healthy == 0,
            f"healthy hits={healthy}",
        ),
    ]
    observed = (
        f"client error ({stream.manifestation()}); err-503 x{err503}, "
        f"err-429 x{err429}, healthy x{healthy}; turn {turn_duration:.2f}s, "
        f"total {duration:.2f}s"
    )
    return _make_result(8, "sat-all-failing-bounded", S8_EXPECTED, assertions, tl, journal, duration, observed)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec(1, "healthy-baseline", S1_EXPECTED, scenario_healthy_baseline),
    ScenarioSpec(2, "stall-pre-reroute", S2_EXPECTED, scenario_stall_pre_reroute),
    ScenarioSpec(3, "stall-mid-surface-error", S3_EXPECTED, scenario_stall_mid_surface_error),
    ScenarioSpec(4, "slow-steady-survives", S4_EXPECTED, scenario_slow_steady_survives),
    ScenarioSpec(5, "sat-429-cooldown-spillover", S5_EXPECTED, scenario_sat_429_cooldown_spillover),
    ScenarioSpec(6, "err-5xx-retry-reroute", S6_EXPECTED, scenario_err_5xx_retry_reroute),
    ScenarioSpec(7, "err-404-no-retry", S7_EXPECTED, scenario_err_404_no_retry),
    ScenarioSpec(8, "sat-all-failing-bounded", S8_EXPECTED, scenario_sat_all_failing_bounded),
]
