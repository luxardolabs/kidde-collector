# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Kidde Collector is a **Python** headless data collector that bridges the **Kidde HomeSafe** cloud API (smart smoke / CO / air-quality detectors) with **InfluxDB** for time-series storage, visualized in **Grafana**. It authenticates against the Kidde cloud with a cookie session, polls the location/device REST endpoints on an interval (via `aiohttp`), and writes device + air-quality metrics to InfluxDB. It reaches OUT to Kidde and OUT to InfluxDB — it publishes **no inbound port**.

This is a Luxardo Labs fleet collector; it follows `/mnt/luxardolabs/COLLECTOR-FLEET-STANDARD.md` with **kasa-collector** as the reference implementation.

<!-- luxarch:claude-pointer asset v7 - DO NOT edit this marker line; it is how repo.claude_pointer_present knows your copy is current. Re-emit with `luxarch --emit claude-pointer`. -->

## How to work here (fleet conduct — read the standard, not just this block)

**`luxarch --doc FLEET-AGENT-CONDUCT-STANDARD` — read it in full before your first change.** It is the one home for *how* agents work in this fleet. This block is a pointer plus the handful of rules that get broken most; it is not a summary and does not replace reading it.

**Run `/fleet-start` at the start of every session and after every compact.** It rehydrates from LuxPM and restates the session rules.

**Report the result, not the mountain.** No "heavy", "multi-hour", "the big one", no narrating difficulty. Done + next in one line, with numbers.

**Decide; do not hand back a menu.** Whether to ask the owner is decided by the **class of action**, never by how confident you feel:

- **Ask** — deleting anything; changing scope; a deferral/allowlist/exemption; publishing outward (pushing another repo, a force-push, a history rewrite); a genuine product fork where the choice is taste, not correctness.
- **Do it** — aligning code to a ratified standard or a guard red; anything you have evidence for that is reversible in one commit. The standard already decided; say what you did.

**Work the guard reds in `luxarch --plan` order. Never ask which family or sweep is next.** The order is decided. An escalation covers ONE site: its family keeps going.

**An owner hold is exactly as wide as the owner said.** "Hold off on X" excludes X and nothing else. It is not permission to pause, ask, or check in about anything outside X. Skip the held family, say so in one line, and keep burning down the rest.

**End every turn that worked guard reds with `reds: N (was M)`**, plus the held families by name. A turn that ends on a question while N > 0, outside an ask-class, is the failure this block exists to stop.

When you do ask: **one decision per message**, the evidence that makes it answerable, your recommendation stated as one, and a question answerable in one word. **A recommendation that ends in a menu is not a recommendation** — if you rejected the alternatives, re-offering them asks the owner to redo your analysis.

**Use the fleet skills; don't improvise the procedure.** `/fleet-start` to open a session. `/wrap-up` before you call anything done (tests you saw fail before the fix, every red in touched files, docs, gate, LuxPM closed out, all with evidence). `/adversarial` to have an independent agent attack a change touching auth, tenancy, data, money, secrets or deploys. `/pin-bump` to upgrade the guards. `/escalate` when a guard is wrong. `/release` to cut a release.

**You touched it, you own it.** Edit a file for any reason and it has a mypy, ruff or luxarch red: fix every one in that file, not just yours. Never spend time proving a red predates you; fix it. Test what you changed first. **Before fixing any mypy red, read `luxlint --playbook mypy-sweep` in full.**

**Align or escalate; never route around.** A guard red is fixed by changing the code, or escalated to the guard maintainer as genuinely wrong. Never by an exemption, a `# noqa`, a deferral, or a local config. Verification is not authorization: proving something is unreferenced does not license deleting it.

**Escalations go in THIS repo's LuxPM project** — label `fleet-escalation`, title `[<guard> ESCALATION] …`, self-contained enough to forward whole. **Search LuxPM for an existing issue first** (and comment on it if found); filing a new one is pre-authorized. **Never a GitHub issue** — there is no fallback. The maintainer sweeps the label across every project and picks it up where you filed it.

**A red stays RED while its escalation is open.** The fleet does not gate CI on red. A lit red is honest; a silenced one is a lie you will inherit.

**Commit as `luxardolabs`** using the global git config, and never `git -c user.email=…`. No AI attribution in commit messages.

## Common Development Commands

The `Makefile` is the source of truth. `VERSION` (repo root) is the version source of truth.

```bash
make help            # grouped command help
make build-local     # build the runtime image from current source (no push)
make test-e2e        # hardware-free end-to-end: fake Kidde -> collector -> InfluxDB, asserted
make demo-up         # self-contained demo: fake Kidde + bundled InfluxDB + Grafana (localhost:3000)
make dev-up          # dev stack: real Kidde account + bundled InfluxDB + Grafana

make lint            # luxlint: canonical ruff + the code-style/doc/secret-config checks (mount-only)
make mypy            # luxlint type leg: mypy, fleet stubs baked (mount-only; its OWN gate step)
make format          # THE canonical fixer, in place: ruff --fix + ruff format + markdown
make test            # THE suite (emitted test block): the Dockerfile test stage, a real InfluxDB per run, branch-coverage ratchet
make arch            # luxarch: architecture conformance (pinned, mount-only)
make audit           # luxaudit: dependency-CVE scan against the live OSV + PyPA feed
make check           # THE fleet gate: guard-version-check honest lint mypy test arch audit gitleaks
make plan            # the full luxarch red board at once — the worklist (`make check` stops at the first red)
make status          # regenerate the committed .lux*-status.json guard-status files
make guard-upgrade   # bump every guard pin to latest (prints what newly bites)
make poetry-lock     # regenerate poetry.lock (poetry-in-docker; no host poetry needed)
make release         # build + scan + push :VERSION (multi-arch) to the private registry, then the GitHub Release

# Run directly (after setting env; see Configuration)
python -m app.main
python -m app.health.check   # container healthcheck
```

Dependencies are managed with **Poetry** as the dependency manager; the **build backend is hatchling** and `VERSION` is the single version source (`dynamic = ["version"]`), so nothing else carries a version literal. There is no `requirements.txt`. `make lint`/`make mypy` are **mount-only** — they run inside the pinned luxlint image against the source, so the repo installs no ruff/mypy of its own; `make test` builds the Dockerfile's `test` stage (deps from `poetry.lock`), starts a throwaway InfluxDB (`kidde_influxdb_test`, profile `test`) in its own compose project, and mounts CURRENT source read-only (never exec into the baked container — stale code). Only **Grafana** is published to the host, on Grafana's default `3000` — InfluxDB stays on the compose network, so it can never collide. If 3000 is taken on your machine, override `GRAFANA_PORT` locally; the shipped default stays 3000.

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
- `app/core/logging_config.py` — the fleet logging module (`luxarch --emit logging`, never hand-edited): JSON lines to stdout, `configure_logging()` called once in `app/main.py`; modules use `logging.getLogger(__name__)`
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

## The run stacks (one compose.yml, five profiles)

Distinguished by source (real vs fake Kidde) and observability (external vs bundled). There is **one** `compose.yml` (`repo.compose_conventions`): stacks differ by compose `profiles:`, environments by `.env.<env>`. Everything runs on the **bridge network** (Kidde is a cloud API — no host networking), and **compose never builds** — `make build-local` and `make harness-build` build the images outside compose and compose only runs the tag.

| Stack          | how it runs                           | source          | InfluxDB/Grafana          | make            |
| -------------- | ------------------------------------- | --------------- | ------------------------- | --------------- |
| collector-only | `--env-file .env.dev` (no profile)    | real            | external (yours)          | `make up`       |
| prod           | `--env-file .env.prod` (no profile)   | real            | external (yours)          | `make prod-*`   |
| dev            | `--env-file .env.demo --profile dev`  | real            | bundled, auto-provisioned | `make dev-up`   |
| demo           | `--env-file .env.demo --profile demo` | fake (emulator) | bundled, auto-provisioned | `make demo-up`  |
| test           | `--env-file .env.e2e --profile e2e`   | fake            | bundled, no Grafana       | `make test-e2e` |

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
1. **Docker**: four-stage `Dockerfile` (builder → builder-dev → production → test). Publishing goes through the emitted image block (`luxarch --emit image-block`, never hand-edited). `make publish-sha` builds, scans and pushes `:sha-<commit>`, plus the `:dev` alias. `make dev-deploy` does that, pins `.env.dev`'s `TAG=` to it, restarts the stack and runs `make smoke`, which checks that the `kidde-collector` container runs this build and stays healthy. `make dev-pin TAG=…` is the rollback path. `make release` refuses a dirty tree, an already-released `:VERSION` and a `v$(VERSION)` tag that is not at HEAD, and checks `gh` and the release notes. Then it pushes a multi-arch `candidate-<commit>`, scans every platform by digest, creates `:VERSION` from that digest and publishes the GitHub Release. No `:latest` is pushed, because a deployable never rolls. Prod pins `TAG=<version>` in `.env.prod`. `make release-public` rescans the released `:VERSION` and copies it to GHCR by digest. Two repo gates run in front of `release`, `publish-sha` and `release-public` as prerequisites: `make audit` (the lock scan, since the image scan only refuses fixable HIGH/CRITICAL) and `release-context-clean`, which refuses gitignored files under `app/`. `make test` builds the Dockerfile's `test` stage (`kidde-collector:test`, local and never pushed) and mounts the current source. Lint and type checks run mount-only, with no app image.
1. **NO AI attribution** in commits/PRs (house rule — no Co-Authored-By, "Generated with", robot emoji).

Migration/alignment chunks are tracked as LuxPM issues (project `KIDDECOLLE`).
