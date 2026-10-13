# deploy

Dev stack for routing-lab: `mock-upstream` + `litellm-lab` (digest-pinned LiteLLM) + `runner` (profile `run`) + `lab-api` (profile `api`, localhost-only). Lab services are internal-only (no host ports); `lab-api` publishes `127.0.0.1:3003`.

From the repo root - profile defaults to `quick`, switch with `LAB_PROFILE=full`:
- Up: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d`
- Down: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml down`
- Battery: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile run run --rm runner`
- Provenance: `export LAB_GIT_REV=$(git rev-parse --short HEAD)` before the battery so the report records the revision (`lab.git_rev`)

`LITELLM_IMAGE` is digest-pinned in `deploy/pinned-versions.env`; proxy configs and semantics live in `lab_config/` (see `lab_config/README.md`).

## lab-api (dashboard backend)

Run-trigger + queue API for the dashboard, on `127.0.0.1:3003`. It has **no docker access** (no docker.sock anywhere): `POST /api/run` validates the gates and drops a request file into `queue/`; the host-side executor below picks it up.

Prod-like local run:
- Up: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile api up -d --build lab-api`
- Down: same command with `down` in place of `up -d --build lab-api` (or `--profile api down`)

Endpoints: `POST /api/run`, `GET /api/run/{id}/events` (SSE replay+tail), `GET /api/last-run`, `GET /api/history`, `GET /api/reports/{run_id}`, `GET /api/docs`, `GET /api/health`, `GET /api/fleet`. `GET /` serves `dashboard/dist` (SPA fallback; plain 503 placeholder until the dashboard is built). `GET /api/fleet` aggregates the prober's JSONL history, mounted read-only from `prober/history` at `/app/prober-history` (`LAB_PROBER_HISTORY_DIR`); it reports `available:false` + an empty state until the host-side prober has run.

Gates on `POST /api/run`: one run at a time (`429 run_in_progress`), 120 s cooldown since the newest run (`429 cooldown`), 1 accepted request / 10 min / IP via `CF-Connecting-IP` (`429 rate_limited`, falls back to the socket peer).

Kill switch: set `LAB_TRIGGER=off` and recreate the service, e.g.
`LAB_TRIGGER=off LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile api up -d lab-api` - `POST /api/run` then returns `503 {"reason":"trigger_disabled"}` until recreated with `LAB_TRIGGER=on`.

## host-side executor (`deploy/executor.sh`)

Polls `queue/` for pending requests and runs each battery against the dev stack, streaming staged events into `queue/<id>.events.jsonl` (replayed by the lab-api SSE endpoint) and reports into `reports/`. It always runs `docker compose down` at the end of a run.

- Single pass: `./deploy/executor.sh --once` (exit 0 = nothing to do or the run finished green, 1 = the run failed)
- Loop mode: `./deploy/executor.sh` - polls every `EXECUTOR_POLL_S` seconds (default 5)
- Single instance: `flock` on `queue/.executor.lock` (a second instance exits immediately)
- Hard battery timeout: `EXECUTOR_BATTERY_TIMEOUT_S` (default 300 s) -> `error` event (classification `timeout`) + status `failed`
- Logs: `queue/executor.log` (appended) and stdout
- Flow per request: mark `running` -> `boot` -> `compose up -d --build` -> `compose --profile run build runner` -> `compose --profile run run --rm runner` (one `scenario` event per `[PASS]/[FAIL] scenario N name (Xs)` line) -> `teardown` -> `done` with `report_id` (or `error` + status `failed`)

Cron install and the public tunnel routing come in the deployment phase (not set up in this repo yet).
