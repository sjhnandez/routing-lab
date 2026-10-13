# cron entries (P4 - NOT installed)

Copy-paste-ready crontab lines for the production deployment, to be installed
as user `server` (not root). **They are NOT installed by this repo and nothing
here runs until the gated P4 deployment is executed** (see `../DEPLOY.md`).

| File | Schedule | What it does |
| --- | --- | --- |
| `routing-lab-executor.cron` | every minute | single-pass battery executor against the prod lab stack (`EXECUTOR_COMPOSE_FILE=deploy/docker-compose.lab.yml`); idle ticks exit immediately and overlap is prevented by the executor's own `flock` |
| `routing-lab-prober.cron` | every 30 min | one live probe against the real LiteLLM router (`http://127.0.0.1:4001`); reads `LITELLM_MASTER_KEY` from `/home/server/stacks/apps/.env` at run time (never stored in the crontab) |

Both lines `cd` to the repo root first, use absolute paths and contain no `%`
characters, so they are copy-paste safe. Logs: `queue/executor.cron.log`
(cron stdout; the executor also appends `queue/executor.log`) and
`prober/history/prober.log`.

## Install (after the go/no-go)

    ( crontab -l 2>/dev/null; cat /home/server/stacks/routing-lab/deploy/cron/routing-lab-executor.cron /home/server/stacks/routing-lab/deploy/cron/routing-lab-prober.cron ) | crontab -

Prerequisites: the panel `.venv` exists (`python3 -m venv .venv` +
`pip install -r prober/requirements.txt` from the repo root) and the Lab tab
(`routing-lab-api`) is up.

## Uninstall

    crontab -l | grep -v 'routing-lab' | crontab -

(`grep -v routing-lab` drops any crontab line mentioning routing-lab; review
`crontab -l` first if you have unrelated entries with that string.)

## Verify

    crontab -l | grep routing-lab

After install, within 2 minutes `queue/executor.cron.log` should show idle
"nothing to do" executor ticks, and within 30 minutes `prober/history/` should
gain its first JSONL entry.
