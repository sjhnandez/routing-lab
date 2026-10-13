# routing-lab dashboard

Single-page dashboard for routing-lab. Click **Run experiment**, the
eight-scenario failover battery runs against the pinned LiteLLM lab, and every
stage streams back over SSE until the results and history tables refresh
themselves — no page reload.

Stack: React 19 · Vite 8 · TypeScript 7 (strict) · Tailwind CSS v4 · `motion`
· `react-markdown`. Same design language as the profile site: dark `#0a0c10`,
accent gradient `#4f8cff → #8b5cf6`, Space Grotesk / Inter / JetBrains Mono
via `@fontsource-variable/*`.

## Build

The host Node may be too old; build via dockerized Node 22 from the repo root:

```sh
docker run --rm --user $(id -u):$(id -g) \
  -v "$PWD/dashboard:/w" -v "$PWD/.npmcache:/cache" \
  -w /w -e npm_config_cache=/cache \
  node:22-bookworm-slim sh -c "npm install && npm run build"
```

`npm run build` runs `tsc -b` first, then Vite; output lands in
`dashboard/dist/`. Once `package-lock.json` exists, use `npm ci` in place of
`npm install`.

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE` | `/api` | Base URL of the lab API. Baked in at build time. |

`npm run dev` serves the app with a proxy for `/api` to `http://127.0.0.1:3003`
(see `vite.config.ts`).

## What it consumes (frozen API contract)

| Endpoint | Used for |
| --- | --- |
| `POST /api/run` | Run button; `202 {id}`, `429 {reason, retry_after_s?}`, `503 {reason: trigger_disabled}` drive the cooldown / blocked states |
| `GET /api/run/{id}/events` | SSE stage stream behind the live stages panel (replays from the start; closed on `done`/`error`) |
| `GET /api/last-run` | Results card on load and after every finished run |
| `GET /api/history` | History table (newest first) |
| `GET /api/reports/{run_id}` | "View" from history, and the JSON download buttons |
| `GET /api/docs` | Method section (methodology + scenarios markdown) |
| `GET /api/fleet` | Fleet section: prober history aggregate (empty state until the prober runs) |
| `GET /api/health` | Status readout, plus automatic re-enable after blocked states |

## Scripts

| Script | Purpose |
| --- | --- |
| `npm run dev` | Vite dev server with the `/api` proxy to `127.0.0.1:3003` |
| `npm run build` | `tsc -b && vite build` into `dist/` |
| `npm run typecheck` | `tsc -b` only |
| `npm run preview` | Serve the built bundle locally |
