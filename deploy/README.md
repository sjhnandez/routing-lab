# deploy

Dev stack for routing-lab: `mock-upstream` + `litellm-lab` (digest-pinned LiteLLM) + `runner` (profile `run`), all internal-only (no host ports).

From the repo root - profile defaults to `quick`, switch with `LAB_PROFILE=full`:
- Up: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml up -d`
- Down: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml down`
- Battery: `LAB_PROFILE=quick docker compose --env-file deploy/pinned-versions.env -f deploy/docker-compose.dev.yml --profile run run --rm runner`
- Provenance: `export LAB_GIT_REV=$(git rev-parse --short HEAD)` before the battery so the report records the revision (`lab.git_rev`)

`LITELLM_IMAGE` is digest-pinned in `deploy/pinned-versions.env`; proxy configs and semantics live in `lab_config/` (see `lab_config/README.md`).
