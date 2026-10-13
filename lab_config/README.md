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
| `mode-err-429` | `openai/err-429` | S5: cooldown on first fail -> spillover to `healthy-pool` |
| `mode-err-503` | `openai/err-503` | S6: retried per `retry_policy`, then rerouted to `healthy-pool` |
| `mode-err-500` | `openai/err-500` | S6: retried per `retry_policy`, then rerouted to `healthy-pool` |
| `mode-err-404` | `openai/err-404` | S7: not a retryable class -> never retried |
| `healthy-pool` | `openai/healthy` x2 (ids `healthy-pool-a`, `healthy-pool-b`) | fallback target for every `mode-*` group |
| `sat-all-failing` | `openai/err-503` | S8: all-failing group, chained to `sat-backup-failing` |
| `sat-backup-failing` | `openai/err-429` | S8: backup all-failing group, chained back to `sat-all-failing` |
| `text-chat` | `openai/healthy` (`supports_vision: false`) | production-shape alias (text tier) |
| `vision-chat` | `openai/healthy` (`supports_vision: true`) | production-shape alias (vision tier) |
| `free-chat` | `auto_router/complexity_router` -> tiers `text-chat` / `vision-chat` | production-shape entry alias (S1 baseline through complexity routing) |

Fallback chains (both profiles):

- `mode-<x>: ["healthy-pool"]` for all 8 single-mode groups.
- `sat-all-failing: ["sat-backup-failing"]` and `sat-backup-failing: ["sat-all-failing"]`.
- `text-chat: ["vision-chat"]`, `vision-chat: ["text-chat"]`, `free-chat: ["text-chat", "vision-chat"]`.

The `sat-*` pair chains to each other so total exhaustion still has a defined path.

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
# quick profile (default)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d

# full profile
LAB_PROFILE=full docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d

# tear down (match the profile you started)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml down

# scenario battery (starts dependencies, runs runner once, removes it)
LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile run run --rm runner
```

`mock-upstream` and `runner` are built from `mock_upstream/` and `runner/` (their
Dockerfiles arrive in later tasks). The litellm image is digest-pinned in
`deploy/pinned-versions.env`.
