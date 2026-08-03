# Deployment

How to deploy the collector — from a single `docker run` to a remote production node. For local development stacks (demo/dev), see [GETTING-STARTED.md](GETTING-STARTED.md).

## The image

Published multi-arch (linux/amd64 + linux/arm64) to the GitHub Container Registry:

```
ghcr.io/luxardolabs/kidde-collector:latest      # rolling
ghcr.io/luxardolabs/kidde-collector:2026.8.0    # pinned (immutable per version)
```

Pin a specific `:<version>` in production so a deploy is reproducible; `:latest` always points at the newest release. The image runs as a non-root user (`appuser`, uid 1000) and has no build tooling in it.

## The four run stacks

| Stack          | Compose file                         | Kidde source    | InfluxDB / Grafana        | Command              |
| -------------- | ------------------------------------ | --------------- | ------------------------- | -------------------- |
| collector-only | `compose.yml` (+ `compose.prod.yml`) | real            | external (yours)          | `make up` / `prod-*` |
| dev            | `compose.dev.yml`                    | real            | bundled, auto-provisioned | `make dev-up`        |
| demo           | `compose.demo.yml`                   | fake (emulator) | bundled, auto-provisioned | `make demo-up`       |
| test           | `compose.e2e.yml`                    | fake            | ephemeral, no Grafana     | `make test-e2e`      |

All stacks are `.yml`, run on the Docker bridge network (the Kidde API is a cloud endpoint — no host networking), and Compose never builds except the fake-Kidde harness in the demo/test stacks.

## Configuration and secrets

All configuration is via `KIDDE_COLLECTOR_*` environment variables ([CONFIGURATION.md](CONFIGURATION.md) has the full list). Secrets live in **gitignored** env files — only `.env.example` (the template) is committed.

```bash
cp .env.example .env.prod      # then fill in Kidde creds + your InfluxDB URL/org/bucket/token
```

Required: `KIDDE_COLLECTOR_KIDDE_USERNAME`, `_KIDDE_PASSWORD`, `_INFLUXDB_URL`, `_INFLUXDB_TOKEN`, `_INFLUXDB_ORG`, `_INFLUXDB_BUCKET`. Inside a Compose network, set `_INFLUXDB_URL` to the InfluxDB **service name** (e.g. `http://influxdb:8086`), not `localhost`.

## Option A — `docker run`

Simplest single-container deploy against an InfluxDB you already run:

```bash
docker run -d --name kidde-collector --restart always \
  --env-file .env.prod \
  -v "$PWD/output:/app/output" \
  ghcr.io/luxardolabs/kidde-collector:2026.8.0
```

The `output/` volume holds the persisted session cookie (so restarts don't re-login) and, if enabled, raw API captures. The image ships a `HEALTHCHECK` (`python -m app.health.check`).

## Option B — Docker Compose (collector-only)

`compose.prod.yml` runs the published `:latest` against your external InfluxDB with `restart: always`:

```bash
make prod-up                   # docker compose -f compose.prod.yml --env-file .env.prod pull && up -d
make prod-logs                 # follow
make prod-ps                   # status
make prod-down                 # stop
```

## Option C — Remote production node (over SSH)

Build on your build host, push the image, then deploy to a node that only pulls images (no source, no builds). Set the target once with `PROD_NODE`:

```bash
# on the build host — cut a release (private registry) then promote to GHCR
make release && make release-public

# one-time: create the bind-mount data dir on the node (owned by uid 1000)
make prod-init    PROD_NODE=collector01.example.com

# push compose.prod.yml + .env.prod to the node (repo is the source of truth)
make prod-sync    PROD_NODE=collector01.example.com

# pull :latest and (re)create the container on the node
make prod-deploy  PROD_NODE=collector01.example.com
```

Operating the node:

```bash
make prod-status       PROD_NODE=…   # container status
make prod-logs-remote  PROD_NODE=…   # follow logs
make prod-health       PROD_NODE=…   # run the in-container health check
make prod-rollback     PROD_NODE=…   # list image tags cached on the node to roll back to
```

The deploy dir on the node (`/opt/kidde-collector` by default) holds only `compose.prod.yml`, `.env.prod`, and the `output/` bind mount — never source.

## Health, restart, and data

- **Health:** `docker run --rm <image> python -m app.health.check` — non-zero exit means unhealthy (it checks the collector is alive and, if raw capture is on, that the capture file is fresh; see `KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE`).
- **Restart:** the Compose stacks use `restart: always`.
- **State:** the only durable state is `output/` (the session cookie + optional captures). InfluxDB holds the metrics.

## Upgrading

Bump the pinned tag (or pull `:latest`) and recreate: `make prod-deploy PROD_NODE=…`. Released `:<version>` tags are immutable, so a pinned deploy never changes under you.
