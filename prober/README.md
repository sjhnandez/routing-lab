# prober

Production-probe toolkit for the real LiteLLM router. One run performs one
cheap chat completion ("ping", `max_tokens=1`) against the next model in
rotation, measures latency, classifies the outcome and appends one JSONL line
to the history. It complements the lab: `runner/` proves failover semantics
against the deterministic mock, the prober collects day-to-day evidence from
the real router.

Safety properties:

- No network access without an explicit live run; `--dry-run` and `--status`
  are fully offline and write nothing.
- `LITELLM_API_KEY` is read from the environment only. It is never written to
  disk, never printed and never committed.
- A daily budget guard stops probing before the free-model quota is at risk.

## Usage

Run from the repository root so the default relative paths resolve there:

| Command | Effect |
| --- | --- |
| `python -m prober` | Probe the next model in rotation once, append history. |
| `python -m prober --dry-run` | Offline: print base URL, rotation position, today's count/cap and the model that would be probed. Writes nothing. |
| `python -m prober --status` | Offline: last-24h class counts plus today's count/cap, from history only. |
| `python -m prober --probe MODEL` | Probe one specific model once. Does not read or advance the rotation. |

Requires Python 3.12 and `httpx` (pinned in `prober/requirements.txt`).

Example `--dry-run` output before the first live run:

    prober dry-run: no network calls, no writes
      base_url:    http://127.0.0.1:4001
      history_dir: prober/history
      rotation:    position 0 of 0 (no cached model list)
      today:       0/48 probes
      would probe: (unknown - no cached model list; a live run fetches it)

Example `--status` output:

    prober status: history only, no network
      history_dir: prober/history
      window:      2026-10-12T12:00:00Z .. 2026-10-13T12:00:00Z (last 24h)
      probes:      4
        ok:            1
        rate_limited:  1
        unavailable:   1
        timeout:       1
        not_found:     0
        auth:          0
        other_4xx:     0
        network:       0
      today:       3/48 probes (2026-10-13)

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success: plan/counts printed, or a probe ran and was recorded (whatever its class). |
| 1 | Runtime failure before a probe could be recorded (model list fetch failed, empty list). |
| 2 | Configuration error: `LITELLM_API_KEY` missing for a live probe, invalid cap/timeout value. |
| 3 | Daily cap reached; no probe performed. |

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `LITELLM_BASE_URL` | `http://127.0.0.1:4001` | Router base URL (a trailing slash is fine). |
| `LITELLM_API_KEY` | (none) | Bearer key. Required for any live probe; `--dry-run`/`--status` never need it. |
| `PROBER_HISTORY_DIR` | `prober/history` | History and state directory (relative paths resolve against the CWD). |
| `PROBER_DAILY_CAP` | `48` | Maximum live probes per UTC day. |
| `PROBER_TIMEOUT_S` | `30` | Per-request timeout in seconds. |

## Failure classes

Exactly one class per probe:

| Class | Trigger |
| --- | --- |
| `ok` | 2xx response. |
| `rate_limited` | HTTP 429. |
| `unavailable` | HTTP 5xx (500/502/503/504 are the canonical ones). |
| `timeout` | Request timed out, or the connect attempt failed (refused/unreachable): no response was received in time. |
| `not_found` | HTTP 404 (e.g. the model no longer exists). |
| `auth` | HTTP 401/403. |
| `other_4xx` | Any other HTTP 4xx. |
| `network` | Other transport failures (connection reset mid-exchange, protocol errors). |

When the response body carries a parseable litellm/OpenAI `error.type`
(e.g. `RateLimitError`, `no_deployments_available`), it takes precedence over
the HTTP status; unknown types fall back to the status.

## History and state

One file per UTC day, one JSON line per probe:

    {"ts":"2026-10-13T12:00:00Z","model":"openrouter/qwen3-8b:free","ok":true,"class":"ok","http_status":200,"latency_ms":812,"error_type":null,"budget":{"date":"2026-10-13","count":7,"cap":48}}

`budget.count` is the number of probes recorded for that UTC day AFTER this
probe. Two dotfiles in the history directory keep rotation state:

- `.rotation` - index of the next model position (advances only after actual
  probes).
- `.models` - cached model list from the last live `GET /v1/models`, used by
  `--dry-run` to name the next model offline.

Both live under `PROBER_HISTORY_DIR`, which is gitignored via
`prober/history/*`.

## Cron (EXAMPLE ONLY - do not install yet)

30-minute spacing matches the default daily cap of 48:

    # EXAMPLE ONLY - do not install yet.
    */30 * * * * cd /home/server/stacks/routing-lab && python -m prober >> prober/history/prober.log 2>&1

Cron does not inherit your shell environment: provide `LITELLM_API_KEY` via
the crontab environment (never hardcode a key in a file inside this repo).

## Notes

- `max_tokens=1` keeps every probe as cheap as a free-tier model allows.
- Rotation covers the full model list returned by `GET /v1/models`; the list
  is refreshed on every live default run, while the rotation index advances
  only after an actual probe. `--probe MODEL` never touches the index.
- The budget guard counts today's history lines before probing and exits 3
  with `daily cap reached (n/n)` when the cap is hit, so a cron schedule
  cannot burn through the free quota.
- Credentials are env-driven end to end; nothing in this directory contains
  or writes a secret.
