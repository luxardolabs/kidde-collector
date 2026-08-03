# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Kidde Collector is a **Python** headless data collector that bridges the **Kidde HomeSafe** cloud API (smart smoke / CO / air-quality detectors) with **InfluxDB** for time-series storage, visualized in **Grafana**. It authenticates against the Kidde cloud with a cookie session, polls the location/device REST endpoints on an interval (via `aiohttp`), and writes device + air-quality metrics to InfluxDB. It reaches OUT to Kidde and OUT to InfluxDB — it publishes **no inbound port**.

This is a Luxardo Labs fleet collector; it follows `/mnt/luxardolabs/COLLECTOR-FLEET-STANDARD.md` with **kasa-collector** as the reference implementation.

## Common Development Commands

The `Makefile` is the source of truth. `VERSION` (repo root) is the version source of truth.

```bash
make help            # grouped command help
make build-local     # build the runtime image from current source (no push)
make test-e2e        # hardware-free end-to-end: fake Kidde -> collector -> InfluxDB, asserted
make demo-up         # self-contained demo: fake Kidde + bundled InfluxDB + Grafana (localhost:3000)
make dev-up          # dev stack: real Kidde account + bundled InfluxDB + Grafana

make lint            # ruff check + ruff format --check + mypy (fresh image, current source)
make test            # pytest suite (needs the :dev image; dev-build-push first, or build locally)
make check           # lint + test
make poetry-lock     # regenerate poetry.lock (poetry-in-docker; no host poetry needed)
make release         # build + push :VERSION + :latest (multi-arch) to the private registry

# Run directly (after setting env; see Configuration)
python -m app.main
python -m app.health.check   # container healthcheck
```

Dependencies are managed with **Poetry** (`pyproject.toml` + committed `poetry.lock`). There is no `requirements.txt`. `make lint`/`make test` build a fresh image from CURRENT source (never exec into the baked container — stale code). Ports 3000/8086 collide with other running fleet stacks; override `GRAFANA_PORT`/`INFLUX_PORT` for local dev/demo.

## Architecture Overview

A simple asyncio poll loop. `app/main.py` validates the environment, connects to InfluxDB, and runs the collector until SIGTERM/SIGINT.

### Layout (fleet standard — `app/` package at the repo root, `app.`-prefixed imports)

- `app/main.py` — entrypoint / orchestrator (`python -m app.main`)
- `app/core/config.py` — env-driven config + validation (all `KIDDE_COLLECTOR_*` vars)
- `app/collector/` — the collection logic:
  - `client.py` — `KiddeClient` (cookie-session login, location/device/event REST calls)
  - `session.py` — `KiddeSession` (cookie persistence to the output dir; re-auth on 403)
  - `poller.py` — `KiddeCollector` (the poll loop)
  - `endpoints.py` — centralized Kidde API URL builders (base is `config.API_BASE_URL`)
- `app/storage/influxdb.py` — `InfluxDBStorage`, asyncio-native `InfluxDBClientAsync` (open in `connect()`, `ping()` fail-fast, one awaited batch per poll cycle), measurement `kidde_collector_device`
- `app/utils/logging.py` — colored console logger
- `app/health/check.py` — Docker HEALTHCHECK

Validate layout conformance with `python /mnt/luxardolabs/check_layout.py .` (must be green).

### Data flow

```
Kidde cloud API -> app/collector (session + client + poller) -> app/storage/influxdb -> InfluxDB -> Grafana
```

### Data model

One measurement, `kidde_collector_device`:

- **tags**: `id`, `serial_number`, `location_id`, `location_label`, `label`
- **fields**: every scalar device attribute, plus per-metric `{name}_value` / `{name}_status` for the air-quality panel (`iaq_temperature`, `humidity`, `hpa`, `tvoc`, `iaq`, `co2`). Non-IAQ detectors write only the scalar fields.

## The four run stacks

Distinguished by source (real vs fake Kidde) and observability (external vs bundled). All are `.yml`, short-form volumes, and run on the **bridge network** (Kidde is a cloud API — no host networking). Compose never builds except the fake-Kidde service in demo/e2e (`build: ./harness`).

| Stack          | compose file                         | source          | InfluxDB/Grafana          | make                 |
| -------------- | ------------------------------------ | --------------- | ------------------------- | -------------------- |
| collector-only | `compose.yml` (+ `compose.prod.yml`) | real            | external (your fleet)     | `make up` / `prod-*` |
| dev            | `compose.dev.yml`                    | real            | bundled, auto-provisioned | `make dev-up`        |
| demo           | `compose.demo.yml`                   | fake (emulator) | bundled, auto-provisioned | `make demo-up`       |
| test           | `compose.e2e.yml`                    | fake            | ephemeral, no Grafana     | `make test-e2e`      |

- Bundled `influxdb:2.7` + Grafana are dev/demo/test only. InfluxQL dashboards need a DBRP mapping (`ops/influxdb/init-dbrp.sh`); Grafana is provisioned via `grafana/provisioning/` (datasource pinned uid `kidde_influxdb`; dashboards from `grafana/shared-local/`, using the `${data_source}` picker var). The dashboards use only core panels — no plugins to install.
- **Emulator**: `harness/fake_kidde.py` — pure-stdlib Kidde cloud fake (cookie-session login + location/device/event REST). Point the collector at it with `KIDDE_COLLECTOR_API_BASE_URL`. See `harness/README.md`.

## Configuration

All config is via `KIDDE_COLLECTOR_*` environment variables (see `app/core/config.py`). **Secrets live in gitignored `.env.dev` / `.env.prod`** (copy from `.env.example`); the dev stack layers an optional gitignored `.env.dev.local` (copy from `.env.dev.local.example`) with your real Kidde account. `.env.demo` is committed, non-secret bundled-stack config. Run `make gitleaks-staged` before committing.

Required: `INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, `KIDDE_USERNAME`, `KIDDE_PASSWORD` (all `KIDDE_COLLECTOR_`-prefixed).

## Important Implementation Notes

1. **Cookie session**: the collector logs in once and persists the session cookies to the bind-mounted output dir; a 403 clears them so the next cycle re-authenticates.
1. **API base URL is env-overridable** (`KIDDE_COLLECTOR_API_BASE_URL`) so the dev/demo/e2e stacks can target the harness emulator instead of the real cloud.
1. **InfluxDB writes** use the asyncio-native `InfluxDBClientAsync` (fleet ingestion standard): opened in `connect()` inside the loop, `ping()` fails fast on an unreachable server, and each poll cycle's points are one awaited batch write (no Rx thread, no buffer to flush; gzip on). Auth/bucket errors (401/404) log actionable guidance and keep retrying.
1. **Error handling**: the loop continues despite individual failures — check logs.
1. **Docker**: four-stage `Dockerfile` (builder → builder-dev → base → dev). Prod pulls `:latest`; the local dev/demo stacks build the runtime image. Lint/type checks run mount-only against the source (no app image); `make test` builds a lean `Dockerfile.test` image from `poetry.lock` and over-mounts the source — neither inherits `:dev`.
1. **NO AI attribution** in commits/PRs (house rule — no Co-Authored-By, "Generated with", robot emoji).

Migration/alignment chunks are tracked as LuxPM issues (project `KIDDECOLLE`).
