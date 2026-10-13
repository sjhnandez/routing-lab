# Scenarios

The battery runs eight scenarios in order. Each one has a single claim under
test, a deterministic setup, and hard assertions evaluated from (a) the client
outcome and (b) the mock upstream's hit journal. A scenario passes only when
every assertion passes; the runner exits non-zero otherwise.

Common setup for all scenarios:

- The lab proxy (`litellm-lab`) runs `lab_config/router.quick.yaml` (or
  `router.full.yaml`): the frozen production `router_settings` contract plus a
  lab `model_list` and fallback chains.
- Every `mode-*` group ("fault mode") points at `mock-upstream` via
  `openai/<mode>`; LiteLLM strips the prefix and the mock injects that mode's
  fault behavior.
- The journal is reset at scenario start; hit counts below are from the
  scenario's isolated window. Times are the quick profile; the full battery is
  ~45s wall clock.

Pool note: the retry-exercised groups (`mode-err-429`, `mode-err-503`,
`mode-err-500`, `mode-err-404`, `sat-all-failing`, `sat-backup-failing`) are
2-deployment pools so retries are observable upstream. Rationale in
`docs/methodology.md` ("Retry, cooldown and why the pools exist").

## S1 - healthy-baseline

**Claim under test:** the happy path is clean: the complexity-router alias
streams a completion and a plain mode group answers non-stream, with no
error/abort entries in the journal.

- Setup: `free-chat` (complexity router -> `text-chat`/`vision-chat`) and
  `mode-healthy`.
- Steps: reset journal; stream `free-chat`; non-stream `mode-healthy`; fetch
  journal.
- Hard assertions:
  1. `free-chat` stream: HTTP 200, `[DONE]`, non-empty content.
  2. `mode-healthy` non-stream: HTTP 200 with content.
  3. Journal: >= 2 `healthy` hits, every entry `completed`.
  4. Duration < 10s.
- Calibration (quick): healthy x2, ~1.0s. Stable across runs.

## S2 - stall-pre-reroute

**Claim under test:** a provider that accepts the request but never sends a
first chunk is killed by the stream silence timeout and the router REROUTES:
the client still gets a clean completion, and the journal attributes the abort
to `stall-pre` and the success to a healthy deployment.

- Setup: `mode-stall-pre` -> fallback `healthy-pool`.
- Steps: stream `mode-stall-pre`; fetch journal once the abort has settled.
- Hard assertions:
  1. Client success via fallback (no error surfaced).
  2. Journal: `stall-pre` hit(s) `aborted` with `chunks_sent == 0`.
  3. Journal: >= 1 completed `healthy` hit.
  4. Duration < 30s.
- Calibration (quick): abort after ~3s (stream_timeout), reroute success,
  ~4.0s total. The pre-content reroute is possible because nothing was sent to
  the client yet.

## S3 - stall-mid-surface-error

**Claim under test:** once content has been streamed, a provider stall cannot
be papered over: the turn FAILS for the client and there is no healthy
fallback hit (rerouting after content would double-send).

- Setup: `mode-stall-mid` (3 chunks, then silence).
- Steps: stream `mode-stall-mid`; fetch journal.
- Hard assertions:
  1. >= 1 content chunk received before the failure.
  2. No successful `[DONE]`; error surfaced.
  3. Journal: `stall-mid` hit(s) `aborted` with `chunks_sent >= 1`.
  4. Journal: no `healthy` hits.
- Calibration (quick): 3 chunks, then an in-band SSE error event
  (`APIConnectionError: Timeout on reading data from socket`) and stream end
  without `[DONE]`, ~3.8s total.

## S4 - slow-steady-survives

**Claim under test:** `stream_timeout` is a SILENCE gap limit, not a total
duration cap: a stream that keeps making progress with gaps under the limit is
never killed.

- Setup: `mode-slow-steady` (12 chunks, 800ms gaps).
- Steps: stream `mode-slow-steady`; measure the turn duration; fetch journal.
- Hard assertions:
  1. Client completes with `[DONE]` and non-empty content.
  2. >= 12 content chunks.
  3. Turn duration >= 9s (well past the 3s `stream_timeout`).
  4. Journal: exactly 1 `slow-steady` completed hit; no `healthy` hits (no
     reroute).
- Calibration (quick): 12 chunks, turn ~9.6s, total ~10.4s.

## S5 - sat-429-cooldown-spillover

**Claim under test:** a 429 cools the deployment pool; traffic immediately
spills over to the fallback without touching the cooling deployments, and the
pool becomes eligible again after cooldown expiry.

- Setup: `mode-err-429` (2-deployment pool) -> fallback `healthy-pool`; quick
  `cooldown_time` is 8s.
- Steps:
  A. stream `mode-err-429`;
  B. non-stream `mode-err-429` immediately after (pool in cooldown);
  C. non-stream after cooldown expiry (~9.5s after the first 429 hit).
- Hard assertions:
  1. A: client success via fallback.
  2. A: journal `err-429` hits >= 1 and >= 1 completed `healthy` hit.
  3. B: client success.
  4. B: `err-429` hit delta == 0 (cooling pool untouched), served by fallback.
  5. C: `err-429` hit delta >= 1 after expiry, client success.
- Calibration (quick, real runs): A shows err-429 x2 (the RateLimitError retry
  reaches the sibling before both cool down) + healthy x1; B delta 0 (pool
  still cooling); C delta 2-3 (both members re-eligible; policy-driven retries
  can re-dispatch a second or third time depending on cooldown-registration
  timing). Assertions use >= 1 and stay truthful under both variants.
  The C wait (9.5s) is calibrated for the quick profile's 8s cooldown.

## S6 - err-5xx-retry-reroute

**Claim under test:** 5xx failures in a pooled group are retried against the
healthy sibling (>= 2 upstream hits) before the fallback reroutes to
`healthy-pool`; holds for streaming and non-streaming requests.

- Setup: `mode-err-503` pool (stream), `mode-err-500` pool (stream AND
  non-stream); quick retry counts: 2.
- Steps: reset; stream `err-503`; fetch; stream `err-500`; fetch; wait out the
  err-500 pool's cooldown (derived from the active profile's `cooldown_time`,
  ~9.5s in quick); non-stream `err-500`; fetch.
- Hard assertions:
  1. err-503 stream: client success after pool retry + reroute.
  2. Journal: err-503 hits >= 2 (retried against the sibling deployment).
  3. err-500 stream: client success; journal err-500 hits >= 2 for this step
     (retry reached the sibling).
  4. err-500 non-stream: client success; journal err-500 hits >= 2 for this
     step, after the pool leaves cooldown.
  5. Journal: one completed `healthy` fallback hit per rerouted request (3
     total).
- Calibration (quick, real runs):
  - err-503 stream: exactly 2 hits, then reroute, ~0.3s. Empirically the 503
    class IS retried once a healthy sibling exists (see "Class findings").
  - err-500 stream: 2 hits; err-500 non-stream: 2-3 hits (the retry policy can
    re-dispatch a third attempt if a cooldown registration races; assertions
    are >= 2 under both variants).
  - Router-side response headers captured as supplementary evidence:
    `x-litellm-attempted-retries: 0` and `x-litellm-attempted-fallbacks: 1` on
    the fallback-served successes. The header describes the final successful
    call; the journal carries the retry evidence of the failed group.
  - Scenario duration ~10.8s (includes the deliberate cooldown-expiry wait so
    both 5xx requests hit a fresh pool).

## S7 - err-404-no-retry

**Claim under test:** a 404 is never retried, in any topology: exactly one
upstream hit even though the pool has a healthy sibling, then the fallback
reroutes.

- Setup: `mode-err-404` pool -> fallback `healthy-pool`.
- Steps: non-stream `mode-err-404`; fetch journal.
- Hard assertions:
  1. Journal: exactly 1 `err-404` hit (404 never retried).
- Calibration (quick): 1 hit, fallback success (HTTP 200), ~0.8s total.

## S8 - sat-all-failing-bounded

**Claim under test:** when every member of a group fails AND its chain
(`sat-all-failing` -> `sat-backup-failing` -> back) also fails entirely, the
client turn still terminates with an ERROR in bounded time: pool retries are
attempted, nothing healthy is touched, nothing hangs.

- Setup: `sat-all-failing` (err-503 x2) chained to `sat-backup-failing`
  (err-429 x2).
- Steps: stream `sat-all-failing`; measure the failing turn; fetch journal.
- Hard assertions:
  1. Client eventually receives an error (no success).
  2. Turn duration < 60s (bounded; no hang).
  3. Journal: err-503 hits >= 2 (all-failing pool retried against its sibling).
  4. Journal: err-429 hits >= 2 (backup pool retried too before exhaustion).
  5. Journal: no healthy hits (nothing healthy exists in the chain).
- Calibration (quick): err-503 x2, err-429 x2, healthy x0; client error after
  ~4.1s. The error body is the litellm chain-exhaustion explanation
  ("Fallback to sat-backup-failing also failed: No deployments available...
  All deployments for selected model are in cooldown"). Policy-driven retries
  against an exhausted pool add latency but cannot reach the upstream (every
  member is cooling), which is why the failure stays bounded.

## Class findings (503 / 404) under the frozen contract

The pinned image (`router.py:7897-7959`) decides retries as follows: a matching
`retry_policy` value wins outright; otherwise `should_retry_this_error()` raises
for non-retryable status codes and `NotFoundError`, declines the retry when the
group has no healthy deployments left (`_num_healthy_deployments <= 0`), and
allows it otherwise. Retries are re-dispatched with no backoff while a healthy
deployment remains (`router.py:8011`).

- 503: retryability is topology-dependent.
  - Pooled group: the first 503 cools one member but leaves a healthy sibling,
    so the retry is allowed and reaches the sibling (journal: 2 hits), then the
    fallback reroutes. The contract's `retry_policy` has no
    ServiceUnavailableError key, so 503 uses the generic `num_retries` path
    plus the health check, not a policy override.
  - Singleton group (pre-pool lab, single-deployment production groups): the
    sole member is cooled by its first failure, so the health check declines
    the retry and the 503 reroutes after exactly 1 upstream hit
    (`x-litellm-attempted-retries: 0`). The "503 fast-fail" observation is a
    no-healthy-sibling phenomenon, not a property of the 503 class.
- 404: never retried in any topology. `should_retry_this_error()` raises
  immediately for `NotFoundError` (`router.py:7935`) before any health or
  policy logic; the pooled group shows exactly 1 upstream hit even with a
  healthy sibling available.
