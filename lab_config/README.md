# lab_config

LiteLLM proxy configs for routing-lab: the layer between the frozen contract and
the mock upstream. This is the interface the scenario runner reads.

| File | Role |
| --- | --- |
| `litellm.config.yaml` | FROZEN production router contract (do not edit). |
| `router.full.yaml` | FULL profile: production values (contract copied verbatim + lab groups/fallbacks). |
| `router.quick.yaml` | QUICK profile: same structure, demo-scaled timings only. |

## How requests reach the mock

- Every deployment calls the mock upstream with `litellm_params.model: "openai/<mode>"`,
  `api_base: "http://mock-upstream:8000/v1"`, `api_key: "sk-mock-dummy"` (dummy only;
  no real secrets or environment variables anywhere).
- LiteLLM strips the `openai/` prefix, so the mock receives `"model": "<mode>"` and
  injects that fault mode.
- Modes: `healthy`, `stall-pre`, `stall-mid`, `slow-steady`, `err-429`, `err-503`,
  `err-500`, `err-404`.
- The proxy master key is the dummy `sk-lab-master-dummy`. Callers send
  `Authorization: Bearer sk-lab-master-dummy` to `http://litellm-lab:4000`
  (the runner gets this as `LAB_BASE_URL`).

## Group inventory

Scenario legend: S1 healthy baseline; S2 pre-content stall reroutes; S3 mid-content
stall surfaces error; S4 slow-but-steady NOT killed; S5 429 storm -> cooldown
spillover; S6 5xx retry -> reroute; S7 404 never retried; S8 saturation -> bounded
failure time.

| Group | Deployment model string(s) | Intended scenario |
| --- | --- | --- |
| `mode-healthy` | `openai/healthy` | S1 baseline |
| `mode-stall-pre` | `openai/stall-pre` | S2: stalls before first chunk -> stream silence kill -> reroute to `healthy-pool` |
| `mode-stall-mid` | `openai/stall-mid` | S3: stalls after content started -> error surfaces |
| `mode-slow-steady` | `openai/slow-steady` | S4: steady small chunks, gaps under `stream_timeout` -> never killed |
| `mode-err-429` | `openai/err-429` x2 (ids `mode-err-429-a`, `mode-err-429-b`) | S5: cooldown on first fail -> spillover to `healthy-pool` (retry reaches the sibling) |
| `mode-err-503` | `openai/err-503` x2 (ids `mode-err-503-a`, `mode-err-503-b`) | S6: retried against the sibling deployment, then rerouted to `healthy-pool` |
| `mode-err-500` | `openai/err-500` x2 (ids `mode-err-500-a`, `mode-err-500-b`) | S6: retried per `retry_policy` against the sibling, then rerouted to `healthy-pool` |
| `mode-err-404` | `openai/err-404` x2 (ids `mode-err-404-a`, `mode-err-404-b`) | S7: not a retryable class -> never retried (even with a healthy sibling) |
| `healthy-pool` | `openai/healthy` x2 (ids `healthy-pool-a`, `healthy-pool-b`) | fallback target for every `mode-*` group |
| `sat-all-failing` | `openai/err-503` x2 (ids `sat-all-failing-a`, `sat-all-failing-b`) | S8: all-failing pool, chained to `sat-backup-failing` |
| `sat-backup-failing` | `openai/err-429` x2 (ids `sat-backup-failing-a`, `sat-backup-failing-b`) | S8: backup all-failing pool, chained back to `sat-all-failing` |
| `text-chat` | `openai/healthy` (`supports_vision: false`) | production-shape alias (text tier) |
| `vision-chat` | `openai/healthy` (`supports_vision: true`) | production-shape alias (vision tier) |
| `free-chat` | `auto_router/complexity_router` -> tiers `text-chat` / `vision-chat` | production-shape entry alias (S1 baseline through complexity routing) |

Fallback chains (both profiles):

- `mode-<x>: ["healthy-pool"]` for all 8 single-mode groups.
- `sat-all-failing: ["sat-backup-failing"]` and `sat-backup-failing: ["sat-all-failing"]`.
- `text-chat: ["vision-chat"]`, `vision-chat: ["text-chat"]`, `free-chat: ["text-chat", "vision-chat"]`.

The `sat-*` pair chains to each other so total exhaustion still has a defined path.

## Why some groups are 2-deployment pools

Production model groups are multi-deployment pools, and that shape is what makes
retries observable at the upstream:

- With `allowed_fails: 0` the first failure cools a deployment instantly.
- LiteLLM's `should_retry_this_error()` declines the retry when the model group
  has no healthy deployments left (`_num_healthy_deployments <= 0`). In a
  single-deployment group that is true the moment the only deployment is cooled,
  so the failure is rerouted through the fallback chain without any retry
  reaching the upstream - the mock journal sees exactly one hit.
- In a 2-deployment pool the first failure leaves a healthy sibling, so the
  retry is permitted and re-dispatched immediately (LiteLLM does not sleep
  before a retry while healthy deployments remain), producing a second upstream
  hit before the fallback reroute.

The retry-exercised groups (`mode-err-429`, `mode-err-503`, `mode-err-500`,
`mode-err-404`, `sat-all-failing`, `sat-backup-failing`) therefore carry two
deployments each: same `openai/<mode>` upstream, same `api_base`/`api_key`,
distinct explicit `model_info.id`s so cooldown state is tracked per deployment
and scenarios cannot cross-contaminate. The 404 pool proves the negative: even
with a healthy sibling available, a `NotFoundError` is never retried.

`mode-healthy`, `mode-stall-pre`, `mode-stall-mid` and `mode-slow-steady` stay
single-deployment: their scenarios test baseline success, reroute-on-silence,
mid-stream error surfacing and gap survival - not retries - and their calibrated
timings stay stable.

## quick vs full deviations

`router.full.yaml` = production contract values; `router.quick.yaml` demo-scales
only these settings (everything else is identical):

| Setting | full | quick |
| --- | --- | --- |
| `num_retries` | 5 | 2 |
| `cooldown_time` | 120 | 8 |
| `timeout` | 20 | 3 |
| `stream_timeout` | 20 | 3 |
| `retry_policy.RateLimitErrorRetries` | 5 | 2 |
| `retry_policy.InternalServerErrorRetries` | 5 | 2 |
| `retry_policy.TimeoutErrorRetries` | 3 | 2 |

Identical in both: `routing_strategy: simple-shuffle`, `allowed_fails: 0`,
`retry_after: 1`, `allowed_fails_policy` (all `0`), the whole `model_list`,
all fallback chains, and the dummy keys.

## Launch (from the repo root)

```bash
# provenance (recommended): record the repo revision in the battery report
export LAB_GIT_REV=$(git rev-parse --short HEAD)

# quick profile (default)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d

# full profile
LAB_PROFILE=full docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d

# tear down (match the profile you started)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml down

# scenario battery (starts dependencies, runs runner once, removes it)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile run run --rm runner
```

`mock-upstream` and `runner` are built from `mock_upstream/` and `runner/`.
The litellm image is digest-pinned in `deploy/pinned-versions.env`.
