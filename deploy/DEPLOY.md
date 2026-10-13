# routing-lab - P4 production deployment runbook (GATED)

Status: **prepared, not executed.** Nothing below has run yet: no cron
installed, no `terraform apply`, no prod container started. Execute only after
a user go/no-go. Every command is run as user `server` unless stated.

## What ships in P4

- `deploy/docker-compose.lab.yml` - prod lab stack (`routing-lab-lab`):
  mock-upstream + litellm-lab (quick config, digest-pinned image) + runner.
  Internal-only, resource-capped; the executor tears it down after each run.
- `deploy/docker-compose.api.yml` - prod dashboard API (`routing-lab-api`):
  `lab-api` on the `apps_default` network, reachable by the tunnel at
  `http://lab-api:8000`. No host ports, no docker.sock.
- `deploy/executor.sh` - new `EXECUTOR_COMPOSE_FILE` (default keeps the dev
  stack); prod cron points it at `deploy/docker-compose.lab.yml`.
- `deploy/cron/` - ready-to-install crontab files + install/uninstall notes.
- `network/terraform/terraform.tfvars` (on disk only, gitignored) - apps-tunnel
  route `lab.superseb.xyz -> http://lab-api:8000`; applied by the final step.
- `deploy/docker-compose.dev.yml` untouched; local dev keeps working.

## Ordered deployment steps

### 1. Final dashboard build (from the repo root)

    cd /home/server/stacks/routing-lab
    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/dashboard:/w" -v "$PWD/.npmcache:/cache" -w /w -e npm_config_cache=/cache node:22-bookworm-slim sh -c "npm ci && npm run build"
    test -f dashboard/dist/index.html && echo DIST-OK

`lab-api` serves this directory read-only (`/app/static`), so build before the
container starts.

### 2. Prober environment (once)

    cd /home/server/stacks/routing-lab
    python3 -m venv .venv
    ./.venv/bin/pip install -r prober/requirements.txt
    ./.venv/bin/python -m prober --dry-run

`--dry-run` is offline and needs no key; it just proves the venv works. `.venv`
is gitignored.

### 3. Install the crontab

    ( crontab -l 2>/dev/null; cat /home/server/stacks/routing-lab/deploy/cron/routing-lab-executor.cron /home/server/stacks/routing-lab/deploy/cron/routing-lab-prober.cron ) | crontab -
    crontab -l | grep routing-lab

Details and uninstall: `deploy/cron/README.md`. The prober line reads the
master key from `/home/server/stacks/apps/.env` at run time; no secret is ever
stored in the crontab.

### 4. Start the prod dashboard API

    cd /home/server/stacks/routing-lab
    docker compose -f deploy/docker-compose.api.yml up -d --build
    docker ps --filter name=lab-api

Reachable from the tunnel network (this is what cloudflared will use):

    docker run --rm --network apps_default --entrypoint python routing-lab-dev-runner -c "import httpx; print(httpx.get('http://lab-api:8000/api/health').status_code)"

Expected: `200`.

### 5. Terraform: plan review -> apply (last gate)

The `lab.superseb.xyz` route is already in `terraform.tfvars` (P4 prep, plan
verified; the file is gitignored and stays on disk only). If cloning fresh, add
this route to the `apps` tunnel block first:

    {
      hostname = "lab.superseb.xyz"
      service  = "http://lab-api:8000"
    }

Then:

    cd /home/server/stacks/network/terraform
    terraform plan -no-color
    terraform apply

Expected lab diff: **1 DNS record to add** (proxied CNAME
`lab.superseb.xyz -> <tunnel>.cfargotunnel.com`) + **1 update** (apps tunnel
ingress config gains the `lab.superseb.xyz -> http://lab-api:8000` route).

**Do not blind-apply.** As of P4 prep the plan also contains pre-existing,
unrelated drift on `cloudflare_zero_trust_access_application.admin`: the
committed `access.tf` declares `example.com` placeholder hostnames while the
live state holds the real `*.superseb.xyz` Access applications, so the plan
proposes 6 destroys + 6 creates there (`Plan: 7 to add, 1 to change,
6 to destroy`). That churn is not part of P4 and would delete real Access
applications. Before applying, either reconcile that drift (fix
`access.tf`/state until only the two lab changes remain - outside P4 scope) or
apply targeted, so the lab route cannot drag it along:

    terraform apply -target='cloudflare_dns_record.tunnel_cnames["apps-lab.superseb.xyz"]' -target='cloudflare_zero_trust_tunnel_cloudflared_config.configs["apps"]'

Verify afterwards that the Access applications in the dashboard are
unchanged.

## Verification checklist

1. **Incognito + phone**: open `https://lab.superseb.xyz` in an incognito
   window and on a phone (this hostname is public like `profile.superseb.xyz`;
   no Cloudflare Access app is attached). Dashboard loads and
   `curl -s https://lab.superseb.xyz/api/health` returns `status: ok`,
   `trigger_enabled: true`.
2. **Full run through the tunnel**: click Run in the Lab tab. Within a minute
   the executor cron picks it up: boot -> 8 scenario events -> teardown -> done;
   a new `reports/<id>.json` renders in the UI.
3. **Second run is friendly**: click Run again immediately; expect a graceful
   429 (`run_in_progress`, then `cooldown`) surfaced as a friendly message, not
   an error page.
4. **Kill switch**: `LAB_TRIGGER=off docker compose -f deploy/docker-compose.api.yml up -d`
   -> `POST /api/run` returns `503 {"reason":"trigger_disabled"}`; restore with
   `LAB_TRIGGER=on docker compose -f deploy/docker-compose.api.yml up -d`
   (or plain `up -d`, the default is `on`).
5. **Zero prod diff**: `docker ps --format '{{.Names}}' | sort` before usage and
   after a run shows the same pre-existing containers; only `lab-api` is added,
   and no `routing-lab-lab` containers remain between runs
   (`docker compose -f deploy/docker-compose.lab.yml ps` -> empty).
6. **Prober**: dry-run, then one real probe (same shape as the cron line):

       cd /home/server/stacks/routing-lab
       . /home/server/stacks/apps/.env
       LITELLM_API_KEY="$LITELLM_MASTER_KEY" ./.venv/bin/python -m prober

   Expect one new `prober/history/<UTC-date>.jsonl` line; later probes arrive
   via cron every 30 min (`prober/history/prober.log` logs the runs).
7. **Fleet tab populates**: after the first history entry,
   `curl -s https://lab.superseb.xyz/api/fleet` reports `available: true` and
   the Fleet tab shows the probe.
8. **Cron smoke**: within 2 minutes `queue/executor.cron.log` shows idle
   "nothing to do" ticks from the executor.

## Rollback

Order matters: stop public triggering first, then flake out components.

1. **Kill switch** (stop new runs immediately):

       cd /home/server/stacks/routing-lab
       LAB_TRIGGER=off docker compose -f deploy/docker-compose.api.yml up -d
       curl -s -X POST https://lab.superseb.xyz/api/run   # expect 503 trigger_disabled

2. **Remove the API container**:

       docker compose -f deploy/docker-compose.api.yml down

   `apps_default` and every apps stack container stay untouched.

3. **Remove the crontab entries**:

       crontab -l | grep -v 'routing-lab' | crontab -
       crontab -l | grep routing-lab || echo CRON-REMOVED

4. **Revert the tunnel route**: delete the `lab.superseb.xyz` block from
   `/home/server/stacks/network/terraform/terraform.tfvars`, then

       cd /home/server/stacks/network/terraform
       terraform plan -no-color     # expect 1 DNS record to destroy + ingress update
       terraform apply

   Same drift caveat as deployment step 5: target the two lab resources if the
   pre-existing Access-app churn is still unresolved.

   **DNS note**: the apply removes the proxied CNAME and the ingress route.
   Because the record is Cloudflare-proxied, removal takes effect at the edge
   immediately; resolvers may keep serving the old answer for a short TTL
   window. `dig +short lab.superseb.xyz` should return nothing once done.

5. **Optional cleanup**: if a battery was interrupted,
   `docker compose -f deploy/docker-compose.lab.yml down`; then
   `rm -rf /home/server/stacks/routing-lab/.venv` (only after step 3, or cron
   will keep failing).

## Rollback rehearsal checklist (do before the go/no-go)

- [ ] Kill switch round trip (~5 min): `LAB_TRIGGER=off ... up -d` -> assert
      503 -> `LAB_TRIGGER=on ... up -d` -> assert 200/429.
- [ ] API down/up (~2 min): `docker compose -f deploy/docker-compose.api.yml down`
      -> `lab-api` gone, `apps_*` containers unchanged -> `up -d` brings it back.
- [ ] Crontab removal line rehearsed read-only: pipe `crontab -l` through
      `grep -v 'routing-lab'` and inspect the output (do not install).
- [ ] Terraform revert is plan-only: temporarily remove the lab route block from
      tfvars, `terraform plan` (expect the DNS record to destroy; the
      pre-existing Access churn of step 5 may also show), restore the block,
      re-plan (expect it to add) - no apply in the rehearsal.
- [ ] Executor teardown: trigger one run, wait for `done`, then
      `docker compose -f deploy/docker-compose.lab.yml ps` -> empty.
