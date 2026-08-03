# =============================================================================
# kidde-collector — fleet build/deploy Makefile
# Source of truth for the version is the VERSION file at the repo root.
# Compose never builds; the Makefile builds the image + pushes it to the registry,
# and the dev/prod stacks pull it. Mirrors the bb-boutique fleet standard, trimmed
# for a single-service collector (no db/redis/nginx/css).
# =============================================================================

VERSION := $(shell cat VERSION 2>/dev/null || git -c safe.directory=$(CURDIR) describe --tags --always 2>/dev/null || echo "0.0.0-dev")
TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)
COMMIT := $(shell git -c safe.directory=$(CURDIR) rev-parse --short HEAD 2>/dev/null || echo 'local')

# Private registry host stays OUT of this public repo — set it in gitignored Makefile.local
# (see Makefile.local.example). Without it, registry-dependent targets (release / dev-build-push /
# lint / format / arch / test-e2e) no-op or skip; build-local + the compose stacks (public GHCR image) work.
-include Makefile.local
REGISTRY ?=
IMAGE_NAME := luxardolabs/kidde-collector
DEV_IMAGE     := $(REGISTRY)/$(IMAGE_NAME):dev
VERSION_IMAGE := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
IMAGE         := $(REGISTRY)/$(IMAGE_NAME):latest
# Locally-built runtime image for the local stacks (up / dev / demo) — no registry needed.
LOCAL_IMAGE   := kidde-collector:local
# Public OSS mirror. EXTERNAL_REGISTRY overridable (default GitHub Container Registry).
EXTERNAL_REGISTRY ?= ghcr.io
PUBLIC_IMAGE := $(EXTERNAL_REGISTRY)/luxardolabs/kidde-collector
PLATFORMS ?= linux/amd64,linux/arm64

BUILD_ARGS := --build-arg BUILD_VERSION=$(VERSION) \
              --build-arg BUILD_TIMESTAMP=$(TIMESTAMP) \
              --build-arg BUILD_COMMIT=$(COMMIT)

# Cache busting: `make dev-build-push NOCACHE=1`
NOCACHE ?=
NO_CACHE_FLAG := $(if $(NOCACHE),--no-cache,)

# ANSI colors for `make help`
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
CYAN := \033[0;36m
NC := \033[0m
BOLD := \033[1m

# Lean TEST image (pytest deps from the lock; source MOUNTED at run) — built by Dockerfile.test,
# NOT from :dev (see luxarch --doc FLEET-BUILD-DEPLOY-STANDARD). ruff/format run mount-only in the
# luxlint image and mypy on a fresh slim, so neither needs an app image; only pytest needs the deps.
TEST_IMAGE := kidde-collector-test

# Poetry-in-docker — the build hosts carry no host poetry. A throwaway
# python:3.14-slim installs poetry into a /tmp venv with the repo mounted so the
# regenerated poetry.lock is written back to the host as the checkout owner.
REPO_UID := $(shell stat -c %u . 2>/dev/null || echo 1000)
REPO_GID := $(shell stat -c %g . 2>/dev/null || echo 1000)
# Pin Poetry for the poetry-in-docker targets to match the Dockerfile's POETRY_VERSION
# (overridable: `make poetry-lock POETRY_VERSION=x.y.z`). Keep in sync with the Dockerfile.
POETRY_VERSION ?= 2.4.1
POETRY_SPEC := poetry$(if $(POETRY_VERSION),==$(POETRY_VERSION),)

# Ruff pin for `make format` (auto-fix uses the luxlint-emitted canonical config). The
# ruff BINARY must match the version luxlint BAKES so `make format` output satisfies
# luxlint's `lint.format` check exactly — keep this in sync with luxlint's ruff, NOT the
# app's dev-dep (`docker run --entrypoint ruff $(LUXLINT_IMAGE) --version`).
RUFF_VERSION ?= 0.15.22

# Architecture guard (luxarch) — pinned; registry host comes from Makefile.local (see above).
LUXARCH_REGISTRY ?=
LUXARCH_VERSION  ?= 0.19.0

# Code-style + type standard (luxlint) — pinned; registry host comes from Makefile.local.
# luxlint ships from the PRIVATE registry only (never GHCR), so the host stays out of this
# public repo exactly like LUXARCH_REGISTRY. Without it, `make lint`/`make format` skip.
LUXLINT_REGISTRY ?=
LUXLINT_VERSION  ?= 0.9.0
LUXLINT_IMAGE    := $(LUXLINT_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)

# Dependency-vulnerability guard (luxaudit) — pinned; registry host comes from Makefile.local.
# Mount-only, no tail: reads poetry.lock and checks each pinned dep against the LIVE OSV+PyPA
# advisory feed (fetched at run, never baked — so the same pinned image reports a new CVE the day
# it drops, no rebuild). Private registry only. Without the host, `make audit` skips. Same
# out-of-tree pattern as LUXARCH_REGISTRY / LUXLINT_REGISTRY.
LUXAUDIT_REGISTRY ?=
LUXAUDIT_VERSION  ?= 0.1.8
LUXAUDIT_IMAGE    := $(LUXAUDIT_REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION)
POETRY_RUN := docker run --rm -u $(REPO_UID):$(REPO_GID) -e HOME=/tmp -v $(PWD):/work -w /work python:3.14-slim sh -c
POETRY_PIP := python -m venv /tmp/v && /tmp/v/bin/pip install -q --root-user-action=ignore $(POETRY_SPEC)

# Compose stacks (all .yml, short-form volumes). Four flavors:
#   compose.yml       collector-only -> your external InfluxDB/Grafana (.env.dev / :dev)
#   compose.prod.yml  collector-only -> external, prod (.env.prod / :latest)
#   compose.dev.yml   full LOCAL dev stack: your real Kidde account + bundled InfluxDB+Grafana
#   compose.demo.yml  DEMO: fake Kidde endpoint + bundled InfluxDB+Grafana (no account)
#   compose.e2e.yml   hardware-free e2e test (fake Kidde + ephemeral InfluxDB) -> `make test-e2e`
RUN_DC  := docker compose -f compose.yml --env-file .env.dev
PROD_DC := docker compose -f compose.prod.yml --env-file .env.prod
DEV_DC  := docker compose -f compose.dev.yml --env-file .env.demo
DEMO_DC := docker compose -f compose.demo.yml --env-file .env.demo

# Remote prod deploy over SSH. Set the node explicitly (no fleet default).
#   make prod-deploy PROD_NODE=collector01.example.com
PROD_NODE ?=
PROD_USER ?= root
PROD_DIR  ?= /opt/kidde-collector
PROD_SSH  := ssh -o BatchMode=yes $(PROD_USER)@$(PROD_NODE)

.PHONY: help version \
        dev-build-push build-local version-build-push release release-public buildx-setup \
        docker-inspect docker-clean \
        up down restart logs ps shell \
        dev-up dev-down dev-clean dev-logs dev-ps dev-shell \
        prod-up prod-down prod-restart prod-logs prod-ps \
        demo-up demo-down demo-clean demo-logs demo-ps \
        check-prod-node prod-init prod-sync prod-deploy prod-status prod-logs-remote prod-health prod-rollback \
        poetry-lock poetry-update poetry-install \
        test-build lint format test arch audit guard-version-check test-e2e check \
        gitleaks gitleaks-staged install-hooks clean clean-all

.DEFAULT_GOAL := help

##@ General

help: ## Show this grouped command help
	@printf "\n$(BOLD)$(CYAN)kidde-collector$(NC)  $(YELLOW)v$(VERSION) ($(COMMIT))$(NC)\n"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@/ { printf "\n$(BOLD)$(BLUE)%s$(NC)\n", substr($$0, 5); next } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  $(GREEN)%-24s$(NC) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\n"

version: ## Show version / build info
	@echo "Version:   $(VERSION)"
	@echo "Commit:    $(COMMIT)"
	@echo "Timestamp: $(TIMESTAMP)"
	@echo "Dev:       $(DEV_IMAGE)"
	@echo "Release:   $(VERSION_IMAGE)  +  $(IMAGE)"
	@echo "Public:    $(PUBLIC_IMAGE):$(VERSION)"

##@ Docker — Build & Registry

buildx-setup: ## Ensure a buildx builder exists (multi-arch release builds)
	@docker buildx inspect kidde-builder >/dev/null 2>&1 \
		|| docker buildx create --name kidde-builder --use
	@docker buildx use kidde-builder

dev-build-push: ## Build + push :dev ONLY (tooling stage: dev deps + tests baked)
	docker build $(NO_CACHE_FLAG) --target dev -f Dockerfile $(BUILD_ARGS) -t $(DEV_IMAGE) .
	docker push $(DEV_IMAGE)
	@echo "Pushed $(DEV_IMAGE)"

build-local: ## Build the runtime image from CURRENT source as a local tag (no push, no registry)
	docker build $(NO_CACHE_FLAG) --target base -f Dockerfile $(BUILD_ARGS) -t $(LOCAL_IMAGE) .

version-build-push: ## Build + push :$(VERSION) ONLY (runtime base stage) to the private registry
	docker build $(NO_CACHE_FLAG) --target base -f Dockerfile $(BUILD_ARGS) -t $(VERSION_IMAGE) .
	docker push $(VERSION_IMAGE)
	@echo "Pushed $(VERSION_IMAGE)"

release: buildx-setup ## Build + push :$(VERSION) AND :latest (multi-arch) to the private registry
	docker buildx build $(NO_CACHE_FLAG) --target base --platform $(PLATFORMS) -f Dockerfile $(BUILD_ARGS) \
		-t $(VERSION_IMAGE) -t $(IMAGE) --push .
	@echo "Pushed $(VERSION_IMAGE) + $(IMAGE)"

release-public: ## Promote the released :$(VERSION) + :latest (multi-arch) to GHCR by digest — run `make release` first
	@docker buildx imagetools inspect $(VERSION_IMAGE) >/dev/null 2>&1 \
		|| { echo "$(VERSION_IMAGE) not found — run 'make release' before 'make release-public'"; exit 1; }
	docker buildx imagetools create \
		-t $(PUBLIC_IMAGE):$(VERSION) -t $(PUBLIC_IMAGE):latest \
		$(VERSION_IMAGE)
	@echo "Promoted $(VERSION_IMAGE) -> $(PUBLIC_IMAGE):$(VERSION) + :latest (same digest — no rebuild)"

docker-inspect: ## Inspect release image metadata
	@docker inspect $(IMAGE) --format='Version: {{index .Config.Labels "version"}}' 2>/dev/null || echo "Image not built"
	@docker inspect $(IMAGE) --format='Built:   {{index .Config.Labels "build_timestamp"}}' 2>/dev/null || true
	@docker inspect $(IMAGE) --format='Commit:  {{index .Config.Labels "commit"}}' 2>/dev/null || true

docker-clean: ## Remove local image tags (:dev, :$(VERSION), :latest, test image)
	docker rmi $(DEV_IMAGE) $(VERSION_IMAGE) $(IMAGE) $(TEST_IMAGE) 2>/dev/null || true
	rm -f .test-image.stamp

##@ Collector-only — plug into your existing InfluxDB/Grafana (compose.yml, .env.dev)

up: build-local ## Build locally + start the collector against YOUR external InfluxDB (edit .env.dev)
	KIDDE_IMAGE=$(LOCAL_IMAGE) $(RUN_DC) up -d
	@echo "kidde-collector $(VERSION) running (collector only)"

down: ## Stop the collector
	$(RUN_DC) down

restart: ## Restart the collector
	$(RUN_DC) restart

logs: ## Follow collector logs
	$(RUN_DC) logs -f

ps: ## Collector status
	$(RUN_DC) ps

shell: ## Shell into the collector container
	$(RUN_DC) exec kidde-collector /bin/bash

##@ Dev — full LOCAL stack (your real Kidde account + bundled InfluxDB + Grafana)

dev-up: build-local ## Build locally + start the full dev stack (real Kidde account; Grafana http://localhost:3000)
	KIDDE_IMAGE=$(LOCAL_IMAGE) $(DEV_DC) up -d
	@echo "kidde-collector [dev] — Grafana http://localhost:3000 (admin/admin)"

dev-down: ## Stop the dev stack (keep data volumes)
	$(DEV_DC) down

dev-clean: ## Stop the dev stack AND delete its data volumes
	$(DEV_DC) down -v

dev-logs: ## Follow dev stack logs
	$(DEV_DC) logs -f

dev-ps: ## Dev stack status
	$(DEV_DC) ps

dev-shell: ## Shell into the collector container
	$(DEV_DC) exec kidde-collector /bin/bash

##@ Prod — local stack (pulls :latest, .env.prod)

prod-up: ## Pull :latest + start prod stack
	$(PROD_DC) pull
	$(PROD_DC) up -d

prod-down: ## Stop prod stack
	$(PROD_DC) down

prod-restart: ## Restart prod stack
	$(PROD_DC) restart

prod-logs: ## Follow prod logs
	$(PROD_DC) logs -f

prod-ps: ## Prod container status
	$(PROD_DC) ps

##@ Prod — remote deploy (set PROD_NODE=<host>)

check-prod-node:
	@test -n "$(PROD_NODE)" || { echo "Set PROD_NODE=<host> (e.g. make prod-deploy PROD_NODE=collector01.example.com)"; exit 1; }

prod-init: check-prod-node ## One-time: create the output data dir on the node (owned by appuser:1000)
	$(PROD_SSH) 'mkdir -p $(PROD_DIR)/output && chown -R 1000:1000 $(PROD_DIR)/output'
	@printf "✓ output dir created on $(PROD_NODE)\n"

prod-sync: check-prod-node ## Push compose.prod.yml + .env.prod to the node (repo is source of truth)
	rsync -az --chown=1000:1000 compose.prod.yml .env.prod $(PROD_USER)@$(PROD_NODE):$(PROD_DIR)/
	@printf "✓ synced config to $(PROD_NODE):$(PROD_DIR)\n"

prod-deploy: check-prod-node ## Pull :latest + recreate the collector on the node (run release first)
	$(PROD_SSH) 'cd $(PROD_DIR) && $(PROD_DC) pull && $(PROD_DC) up -d'
	@printf "✓ deployed to $(PROD_NODE)\n"

prod-status: check-prod-node ## Container status on the node
	$(PROD_SSH) 'cd $(PROD_DIR) && $(PROD_DC) ps'

prod-logs-remote: check-prod-node ## Follow collector logs on the node
	$(PROD_SSH) 'cd $(PROD_DIR) && $(PROD_DC) logs --tail=100 -f'

prod-health: check-prod-node ## Run the in-container health check on the node
	$(PROD_SSH) 'cd $(PROD_DIR) && $(PROD_DC) exec -T kidde-collector python3 -m app.health.check'

prod-rollback: check-prod-node ## List image tags cached on the node for rollback
	$(PROD_SSH) 'docker images $(REGISTRY)/$(IMAGE_NAME) --format "table {{.Tag}}\t{{.CreatedAt}}"'

##@ Demo / quickstart (self-contained: collector + InfluxDB + Grafana)

demo-up: build-local ## Bring up the demo stack — FAKE Kidde endpoint + auto-provisioned InfluxDB + Grafana
	KIDDE_IMAGE=$(LOCAL_IMAGE) $(DEMO_DC) up -d --build
	@echo "Grafana:  http://localhost:3000  (admin/admin)  — dashboards populate from the fake Kidde feed"
	@echo "InfluxDB: http://localhost:8086"

demo-down: ## Stop the demo stack (keep data volumes)
	$(DEMO_DC) down

demo-clean: ## Stop the demo stack AND delete its data volumes
	$(DEMO_DC) down -v

demo-logs: ## Follow demo stack logs
	$(DEMO_DC) logs -f

demo-ps: ## Demo stack status
	$(DEMO_DC) ps

##@ Dependencies (poetry in docker — no host poetry required)

poetry-lock: ## Generate/refresh poetry.lock from pyproject.toml (docker, no install)
	$(POETRY_RUN) '$(POETRY_PIP) && /tmp/v/bin/poetry lock'

poetry-update: ## Update deps to latest allowed + rewrite poetry.lock (docker)
	$(POETRY_RUN) '$(POETRY_PIP) && /tmp/v/bin/poetry update --lock'

poetry-install: ## Verify deps resolve + install cleanly from poetry.lock (docker, throwaway venv)
	$(POETRY_RUN) '$(POETRY_PIP) && /tmp/v/bin/poetry install --no-root --only main'

##@ Quality (lint · types · tests · secrets)

# Lock-keyed test image: the stamp depends on poetry.lock (+ pyproject / the test Dockerfile), so
# `docker build` runs ONLY when deps change — not every `make test`. No :dev, no registry pull.
.test-image.stamp: poetry.lock pyproject.toml Dockerfile.test
	DOCKER_BUILDKIT=1 docker build $(NO_CACHE_FLAG) -f Dockerfile.test -t $(TEST_IMAGE) .
	@touch $@

test-build: .test-image.stamp ## Build the lean test image from the lock (only when deps change)

lint: ## luxlint (ruff, mount-only) + mypy tail — ONE recipe; fails if either fails. Needs LUXLINT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping lint"; \
	else set +e; \
	  docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE); ruff=$$?; \
	  docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config mypy > .luxlint.mypy.ini; \
	  docker run --rm -v $(PWD)/.luxlint.mypy.ini:/cfg/mypy.ini:ro -v $(PWD):/w -w /w -e MYPYPATH=/w python:3.14-slim \
	    sh -c 'pip install -q "mypy>=2.3" types-aiofiles && mypy --config-file /cfg/mypy.ini app'; mypy=$$?; \
	  rm -f .luxlint.mypy.ini; \
	  if [ $$ruff -ne 0 ] || [ $$mypy -ne 0 ]; then \
	    echo "lint FAILED (luxlint=$$ruff mypy=$$mypy)"; exit 1; \
	  fi; \
	fi

format: ## Auto-fix + format with the CANONICAL ruff config (luxlint-emitted to gitignored .ruff.local.toml). Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping format"; \
	else \
	  docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config ruff > .ruff.local.toml; \
	  docker run --rm --user $(REPO_UID):$(REPO_GID) -e HOME=/tmp -v $(PWD):/w -w /w python:3.14-slim \
	    sh -c 'python -m venv /tmp/v && /tmp/v/bin/pip install -q --root-user-action=ignore "ruff==$(RUFF_VERSION)" && \
	           { /tmp/v/bin/ruff check --config /w/.ruff.local.toml --fix app; /tmp/v/bin/ruff format --config /w/.ruff.local.toml app; }'; \
	fi

test: .test-image.stamp ## Canonical pytest suite: lock-built deps image + over-mounted source (no :dev). Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping"; exit 0; \
	fi; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config pytest > .luxlint.pytest.ini; \
	docker run --rm -w /app \
	  -v $(PWD)/app:/app/app:ro -v $(PWD)/tests:/app/tests:ro \
	  -v $(PWD)/.luxlint.pytest.ini:/cfg/pytest.ini:ro $(TEST_IMAGE) \
	  pytest -c /cfg/pytest.ini -p no:cacheprovider tests -q; rc=$$?; \
	rm -f .luxlint.pytest.ini; \
	exit $$rc

arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml). Needs LUXARCH_REGISTRY (Makefile.local).
	@if [ -z "$(LUXARCH_REGISTRY)" ]; then \
	  echo "luxarch: LUXARCH_REGISTRY unset (see Makefile.local.example) — skipping arch"; \
	else docker run --rm -v $(PWD):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION); fi

audit: ## Dependency-vuln scan via luxaudit (pinned; poetry.lock vs live OSV+PyPA feed). Needs LUXAUDIT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXAUDIT_REGISTRY)" ]; then \
	  echo "luxaudit: LUXAUDIT_REGISTRY unset (see Makefile.local.example) — skipping audit"; \
	else docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE); fi

E2E_IMAGE := $(REGISTRY)/$(IMAGE_NAME):e2e
test-e2e: ## Hardware-free end-to-end test: fake Kidde endpoint -> collector -> InfluxDB
	docker build $(NO_CACHE_FLAG) --target base -f Dockerfile $(BUILD_ARGS) -t $(E2E_IMAGE) .
	KIDDE_IMAGE=$(E2E_IMAGE) ./scripts/e2e-test.sh

guard-version-check: ## Warn (non-fatal) if any fleet guard pin is behind latest — pulls :latest FIRST so the compare is honest
	@reg="$(LUXARCH_REGISTRY)"; \
	if [ -z "$$reg" ]; then echo "guard-version-check: registry unset (Makefile.local) — skipping"; else \
	  for gv in "luxarch $(LUXARCH_VERSION)" "luxlint $(LUXLINT_VERSION)" "luxaudit $(LUXAUDIT_VERSION)"; do \
	    set -- $$gv; name=$$1; pin=$$2; \
	    docker pull -q $$reg/luxardolabs/$$name:latest >/dev/null 2>&1 || true; \
	    latest=$$(docker run --rm $$reg/luxardolabs/$$name:latest --version 2>/dev/null | awk '{print $$2}'); \
	    if [ -n "$$latest" ] && [ "$$latest" != "$$pin" ]; then \
	      printf "⚠ %-9s pinned %s, latest %s — bump (the pinned tool changed)\n" "$$name" "$$pin" "$$latest"; \
	    else printf "✓ %-9s %s (latest)\n" "$$name" "$$pin"; fi; \
	  done; \
	fi

check: guard-version-check lint arch audit test gitleaks ## Version-check + lint + arch + audit + test + secret scan

# Secret scan uses the CANONICAL luxlint gitleaks config (built-in credential rules + the fleet org
# denylist for internal infra domain / retired identity + the fleet allowlist), EMITTED at scan time to
# a /tmp file OUTSIDE the repo — never committed (secret.no_local_gitleaks_config forbids a local
# .gitleaks.toml; a committed config would print the very strings it forbids). Repo-specific non-secret
# carve-outs live in .luxlint.toml [gitleaks].allow. Needs LUXLINT_REGISTRY.
gitleaks: ## Full-history secret scan (canonical luxlint config: fleet denylist + allowlist)
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then echo "luxlint: LUXLINT_REGISTRY unset — skipping"; exit 0; fi; \
	gl=$$(mktemp); docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config gitleaks > $$gl; \
	docker run --rm -v $(PWD):/repo -v $$gl:/gl.toml:ro -w /repo \
	  ghcr.io/gitleaks/gitleaks:latest git /repo -c /gl.toml --redact -v; rc=$$?; \
	rm -f $$gl; exit $$rc

gitleaks-staged: ## Pre-commit secret scan of STAGED changes (canonical luxlint config)
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then echo "luxlint: LUXLINT_REGISTRY unset — skipping"; exit 0; fi; \
	gl=$$(mktemp); docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config gitleaks > $$gl; \
	docker run --rm -v $(PWD):/repo -v $$gl:/gl.toml:ro -w /repo \
	  ghcr.io/gitleaks/gitleaks:latest protect --staged /repo -c /gl.toml --redact -v; rc=$$?; \
	rm -f $$gl; exit $$rc

install-hooks: ## Install the committed git pre-commit hook (secret scan of staged changes)
	git config core.hooksPath .githooks
	@echo "✓ pre-commit hook active (runs 'make gitleaks-staged' before each commit)"

##@ Utilities

clean: ## Clean python/test caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ .coverage htmlcov/
	rm -f .test-image.stamp .luxlint.mypy.ini .luxlint.pytest.ini .ruff.local.toml

clean-all: clean docker-clean ## Clean caches + local docker image tags
