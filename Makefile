# =============================================================================
# kidde-collector — fleet build/deploy Makefile
# Source of truth for the version is the VERSION file at the repo root.
# Compose never builds; the Makefile builds the image + pushes it to the registry,
# and the dev/prod stacks pull it. Mirrors the bb-boutique fleet standard, trimmed
# for a single-service collector (no db/redis/nginx/css).
# =============================================================================

VERSION := $(shell cat VERSION 2>/dev/null || git -c safe.directory=$(CURDIR) describe --tags --always 2>/dev/null || echo "0.0.0-dev")
TIMESTAMP := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
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
# Locally-built images for the local stacks (up / dev / demo / e2e) — BUILT, never pushed, so
# their names are BARE (repo.image_name_declares_provenance): a registry-qualified name promises
# an artifact a `docker image prune` would silently delete. The committed .env.demo / .env.e2e
# set REGISTRY= (empty), which compose resolves to these same bare names.
LOCAL_IMAGE   := $(notdir $(IMAGE_NAME)):local
FAKE_IMAGE    := $(notdir $(IMAGE_NAME))-fake:local
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
LUXARCH_VERSION  := 0.277.0

# Code-style + type standard (luxlint) — pinned; registry host comes from Makefile.local.
# luxlint ships from the PRIVATE registry only (never GHCR), so the host stays out of this
# public repo exactly like LUXARCH_REGISTRY. Without it, `make lint`/`make format` skip.
LUXLINT_REGISTRY ?=
LUXLINT_VERSION  := 0.63.1
LUXLINT_IMAGE    := $(LUXLINT_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)

# Dependency-vulnerability guard (luxaudit) — pinned; registry host comes from Makefile.local.
# Mount-only, no tail: reads poetry.lock and checks each pinned dep against the LIVE OSV+PyPA
# advisory feed (fetched at run, never baked — so the same pinned image reports a new CVE the day
# it drops, no rebuild). Private registry only. Without the host, `make audit` skips. Same
# out-of-tree pattern as LUXARCH_REGISTRY / LUXLINT_REGISTRY.
LUXAUDIT_REGISTRY ?=
LUXAUDIT_VERSION  := 0.13.2
LUXAUDIT_IMAGE    := $(LUXAUDIT_REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION)
POETRY_RUN := docker run --rm -u $(REPO_UID):$(REPO_GID) -e HOME=/tmp -v $(PWD):/work -w /work python:3.14-slim sh -c
POETRY_PIP := python -m venv /tmp/v && /tmp/v/bin/pip install -q --root-user-action=ignore $(POETRY_SPEC)

# ONE compose.yml (repo.compose_conventions). Stacks differ by PROFILE, environments by
# .env.<env> — there are no per-env overlay files. Nothing is built by compose.
#   (no profile)  collector-only -> your external InfluxDB (.env.dev) or prod (.env.prod)
#   --profile dev    real Kidde account + bundled InfluxDB/Grafana  (.env.demo + .env.dev.local)
#   --profile demo   fake Kidde endpoint + bundled InfluxDB/Grafana (.env.demo)
#   --profile e2e    hardware-free e2e: fake Kidde + bundled InfluxDB (.env.e2e)
DC      := docker compose
RUN_DC  := $(DC) --env-file .env.dev
PROD_DC := $(DC) --env-file .env.prod
DEV_DC  := $(DC) --env-file .env.demo --profile dev
DEMO_DC := $(DC) --env-file .env.demo --profile demo
E2E_DC  := $(DC) --env-file .env.e2e --profile e2e

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
        test-build lint mypy format test arch audit test-e2e harness-build \
        guard-version-check guard-upgrade honest check plan status gh-release \
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

# The WHOLE fleet shares ONE buildx builder (repo.shared_buildx_builder). A per-project
# builder holds a completely separate cache — no base-layer sharing, its own pip mounts,
# unbounded growth (ten builders measured at ~72G, deduplicating to ~10-15G on one).
# Cap it once with a buildkitd GC policy — see luxarch --doc FLEET-BUILD-DEPLOY-STANDARD.
BUILDX_BUILDER ?= luxardo-builder
BUILDKITD_CONFIG ?= $(HOME)/.docker/buildkitd.toml

buildx-setup: ## Ensure the shared, GC-capped fleet buildx builder exists (multi-arch release builds)
	@# The builder is created ONCE and shared, so it must self-prune: a create-once builder
	@# with no buildkitd GC policy grows without bound (repo.buildx_builder_gc_capped). The
	@# name being canonical says nothing about pruning — the cap is the `--buildkitd-config`.
	@if [ ! -f "$(BUILDKITD_CONFIG)" ]; then \
		mkdir -p "$$(dirname $(BUILDKITD_CONFIG))"; \
		printf '[worker.oci]\n  gc = true\n  [[worker.oci.gcpolicy]]\n    keepBytes = "20GB"\n    all = true\n' > "$(BUILDKITD_CONFIG)"; \
		echo "seeded $(BUILDKITD_CONFIG) (20GB buildkit GC cap)"; \
	fi
	@# `buildx create` only CREATES — an old per-project builder keeps running, uncapped, with its
	@# own cache, and only the host can see it. REFUSE it; ALLOW_STRAY_BUILDERS=1 for a deliberate one.
	@strays=$$(docker buildx ls 2>/dev/null | awk 'NR>1 && $$1 !~ /^\\_/ {sub(/\*$$/,"",$$1); print $$1}' \
		| grep -vxE '$(BUILDX_BUILDER)|default|desktop-linux' || true); \
	if [ -n "$$strays" ] && [ -z "$(ALLOW_STRAY_BUILDERS)" ]; then \
		echo "REFUSING: stray buildx builder(s) beside the shared $(BUILDX_BUILDER):"; \
		for b in $$strays; do echo "    docker buildx rm $$b"; done; \
		echo "(remove them, or ALLOW_STRAY_BUILDERS=1 for a deliberate non-fleet builder)"; \
		exit 1; \
	fi
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 \
		|| docker buildx create --name $(BUILDX_BUILDER) --driver docker-container \
			--buildkitd-config $(BUILDKITD_CONFIG) --use
	@docker buildx use $(BUILDX_BUILDER)

dev-build-push: ## Build + push :dev ONLY (tooling stage: dev deps + tests baked)
	docker build $(NO_CACHE_FLAG) --target dev -f Dockerfile $(BUILD_ARGS) -t $(DEV_IMAGE) .
	docker push $(DEV_IMAGE)
	@echo "Pushed $(DEV_IMAGE)"

harness-build: ## Build the fake-Kidde emulator image from ./harness (outside compose, never pushed)
	docker build $(NO_CACHE_FLAG) -t $(FAKE_IMAGE) ./harness
	@echo "Built $(FAKE_IMAGE)"

build-local: ## Build the runtime image from CURRENT source as a local tag (no push, no registry)
	docker build $(NO_CACHE_FLAG) --target base -f Dockerfile $(BUILD_ARGS) -t $(LOCAL_IMAGE) .

# ── Release gates, shared by every target that pushes an immutable tag ──────────────────────────
# A released version is never re-pushed (prod pins it); the tag names HEAD only if the tree is
# clean, gitignored files in the build context included; every precondition the release needs is
# checked BEFORE the first push; and the exact bits promoted are the bits scanned, by digest.
CANDIDATE_IMAGE := $(REGISTRY)/$(IMAGE_NAME):candidate-$(COMMIT)
RELEASE_NOTES   := app/release_notes/$(VERSION).md
# PLATFORMS is buildx's comma list; the per-platform scan loop needs it word-split.
COMMA := ,
SPACE := $(subst ,, )

# `git status --porcelain` does not list gitignored files, but the build context holds them: a
# session-cookie dump under app/output/ would pass and ship. So also refuse any ignored file under
# app/ (what the Dockerfile COPYs) except the caches .dockerignore drops at every depth.
define refuse_dirty_tree
	@if [ -n "$$(git status --porcelain 2>/dev/null)" ]; then \
	  echo "REFUSING: the working tree is dirty, so $(COMMIT) would not describe these bits:"; \
	  git status --short | sed 's/^/    /'; \
	  echo "Commit first, then release what you committed."; \
	  exit 1; \
	fi
	@ign=$$(git ls-files --others --ignored --exclude-standard -- app 2>/dev/null \
	  | grep -vE '(^|/)__pycache__/|\.py[cod]$$|(^|/)\.DS_Store$$' || true); \
	if [ -n "$$ign" ]; then \
	  echo "REFUSING: gitignored files sit in the build context and would ship:"; \
	  echo "$$ign" | sed 's/^/    /'; \
	  echo "Remove them (or add them to .dockerignore) before releasing."; \
	  exit 1; \
	fi
endef

# Fails CLOSED: `manifest inspect` also exits 1 for an unreachable registry, so only the
# registry's own not-found answer reads as unreleased.
define refuse_released
	@if docker manifest inspect $(1) >/dev/null 2>&1; then \
	  echo "REFUSING: $(1) is already RELEASED. A released version is immutable: bump VERSION."; exit 1; \
	fi
	@out=$$(docker manifest inspect $(1) 2>&1) || case "$$out" in \
	  *[Nn]"o such manifest"*|*"manifest unknown"*|*"not found"*) ;; \
	  *) echo "REFUSING: cannot verify $(1) is unreleased: $$out"; exit 1 ;; \
	esac
endef

# Everything the release needs that is NOT the image, checked before anything is pushed: a push
# that then fails here burns the version (refuse_released blocks the retry) with no GitHub Release.
define release_preflight
	@t=$$(git rev-parse -q --verify "refs/tags/v$(VERSION)^{commit}" 2>/dev/null); \
	if [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is not tagged at HEAD. Tag and push it first:"; \
	  echo "    git tag -a v$(VERSION) -m v$(VERSION) && git push origin v$(VERSION)"; exit 1; \
	fi
	@r=$$(git ls-remote origin "refs/tags/v$(VERSION)^{}" 2>/dev/null | cut -f1); \
	[ -n "$$r" ] || r=$$(git ls-remote origin "refs/tags/v$(VERSION)" 2>/dev/null | cut -f1); \
	if [ "$$r" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) on origin is '$${r:-absent}', not HEAD: git push origin v$(VERSION)"; exit 1; \
	fi
	@[ -f "$(RELEASE_NOTES)" ] || { echo "REFUSING: $(RELEASE_NOTES) is missing — write the release notes first"; exit 1; }
	@command -v gh >/dev/null 2>&1 || { echo "REFUSING: the GitHub CLI (gh) is not installed; the release publishes its notes with it"; exit 1; }
	@gh auth status >/dev/null 2>&1 || { echo "REFUSING: gh is not authenticated (gh auth login)"; exit 1; }
endef

# Scan a PUSHED multi-arch image on every platform before any release tag points at it.
# Its index digest is read first and re-read after the last scan: if the tag moved meanwhile, the
# scanned bits are not what would be promoted, so it refuses. `pull --platform` re-points the
# host's LOCAL copy of the tag at each platform in turn (the last is arm64 on an x86 host), so the
# local tag is dropped afterwards; the registry's is untouched.
#   $(1) the pushed ref  $(2) scan label
define scan_pushed
	@set -e; T=$$(mktemp); trap 'rm -f "$$T"; docker image rm $(1) >/dev/null 2>&1 || true' EXIT INT TERM; \
	digest() { docker buildx imagetools inspect $(1) --format '{{json .Manifest}}' \
	  | python3 -c 'import json,sys; print(json.load(sys.stdin)["digest"])'; }; \
	D=$$(digest); [ -n "$$D" ] || { echo "REFUSING: could not resolve the digest of $(1)"; exit 1; }; \
	echo "scanning $(1) ($$D)"; \
	for p in $(subst $(COMMA),$(SPACE),$(PLATFORMS)); do \
	  docker pull -q --platform $$p $(1) >/dev/null; \
	  docker save $(1) -o "$$T"; chmod 644 "$$T"; \
	  docker run --rm -v $(PWD):/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro \
	    $(LUXAUDIT_IMAGE) --image-archive /candidate.tar --image-label "$(2) ($$p)"; \
	done; \
	[ "$$(digest)" = "$$D" ] || { echo "REFUSING: $(1) moved while it was being scanned"; exit 1; }
endef

# The lock audit gates every version push: the image scan only refuses fixable HIGH/CRITICAL, and
# a MEDIUM CVE in a locked package (multidict CVE-2026-104874 in 2026.10.0) passes it.
version-build-push: audit buildx-setup ## Build + scan + push :$(VERSION) ONLY (multi-arch, no :latest, no GitHub Release)
	$(call refuse_released,$(VERSION_IMAGE))
	$(refuse_dirty_tree)
	$(release_preflight)
	docker buildx build $(NO_CACHE_FLAG) --target base --platform $(PLATFORMS) -f Dockerfile $(BUILD_ARGS) \
		-t $(CANDIDATE_IMAGE) --push .
	$(call scan_pushed,$(CANDIDATE_IMAGE),$(VERSION_IMAGE))
	docker buildx imagetools create -t $(VERSION_IMAGE) $(CANDIDATE_IMAGE)
	@echo "Pushed $(VERSION_IMAGE)"

# The cut release, multi-arch: build ONCE under a tag nothing pins, scan THAT image on every
# platform, then promote it (repo.release_scans_candidate), then the GitHub Release.
release: audit buildx-setup ## Cut VERSION: build + scan + push :$(VERSION) AND :latest (multi-arch), then the GitHub Release
	$(call refuse_released,$(VERSION_IMAGE))
	$(refuse_dirty_tree)
	$(release_preflight)
	docker buildx build $(NO_CACHE_FLAG) --target base --platform $(PLATFORMS) -f Dockerfile $(BUILD_ARGS) \
		-t $(CANDIDATE_IMAGE) --push .
	$(call scan_pushed,$(CANDIDATE_IMAGE),$(VERSION_IMAGE))
	docker buildx imagetools create -t $(VERSION_IMAGE) -t $(IMAGE) $(CANDIDATE_IMAGE)
	@echo "Pushed $(VERSION_IMAGE) + $(IMAGE)"
	@$(MAKE) --no-print-directory gh-release

# The GitHub Release is where this repo's notes actually live — a git tag is NOT a Release,
# and the LuxPM release record is the fleet INDEX, not this repo's record. Without this the
# /releases page is empty and app/release_notes/$(VERSION).md goes unused. Enforced by
# repo.github_release_wired; see luxarch --doc FLEET-RELEASE-PROCESS §9. Idempotent.
gh-release: ## Publish the GitHub Release for $(VERSION) from its committed release notes (`release` runs it)
	@notes="$(RELEASE_NOTES)"; \
	if [ ! -f "$$notes" ]; then echo "!! $$notes missing — write the release notes before cutting the Release"; exit 1; fi; \
	if ! git ls-remote --exit-code --tags origin "refs/tags/v$(VERSION)" >/dev/null 2>&1; then echo "!! tag v$(VERSION) is not on origin — tag and push it first"; exit 1; fi; \
	if gh release view "v$(VERSION)" >/dev/null 2>&1; then \
	  echo "GitHub Release v$(VERSION) already exists — skipped (a Release, like its tag, is immutable)"; \
	else \
	  gh release create "v$(VERSION)" --title "$(VERSION)" --notes-file "$$notes" --verify-tag; \
	fi

# Promote the released, already-scanned :$(VERSION) to GHCR. It probes ITS destination tag (a
# public :VERSION is immutable too), re-scans the source, and copies that scanned digest.
release-public: audit ## Promote the released :$(VERSION) + :latest (multi-arch) to GHCR by digest — run `make release` first
	$(call refuse_released,$(PUBLIC_IMAGE):$(VERSION))
	$(refuse_dirty_tree)
	@docker buildx imagetools inspect $(VERSION_IMAGE) >/dev/null 2>&1 \
		|| { echo "$(VERSION_IMAGE) not found — run 'make release' before 'make release-public'"; exit 1; }
	$(call scan_pushed,$(VERSION_IMAGE),$(PUBLIC_IMAGE):$(VERSION))
	docker buildx imagetools create \
		-t $(PUBLIC_IMAGE):$(VERSION) -t $(PUBLIC_IMAGE):latest \
		$(VERSION_IMAGE)
	@echo "Promoted $(VERSION_IMAGE) -> $(PUBLIC_IMAGE):$(VERSION) + :latest (same digest — no rebuild)"

docker-inspect: ## Inspect release image metadata
	@docker inspect $(IMAGE) --format='Version: {{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || echo "Image not built"
	@docker inspect $(IMAGE) --format='Built:   {{index .Config.Labels "org.opencontainers.image.created"}}' 2>/dev/null || true
	@docker inspect $(IMAGE) --format='Commit:  {{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true

docker-clean: ## Remove local image tags (:dev, :$(VERSION), :latest, test image)
	docker rmi $(DEV_IMAGE) $(VERSION_IMAGE) $(IMAGE) $(TEST_IMAGE) 2>/dev/null || true
	rm -f .test-image.stamp

##@ Collector-only — plug into your existing InfluxDB/Grafana (compose.yml, .env.dev)

up: build-local ## Build locally + start the collector against YOUR external InfluxDB (edit .env.dev)
	$(RUN_DC) up -d
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
	$(DEV_DC) up -d
	@echo "kidde-collector [dev] — Grafana http://localhost:$(or $(GRAFANA_PORT),3000) (admin/admin)"

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

prod-sync: check-prod-node ## Push compose.yml + .env.prod to the node (repo is source of truth)
	rsync -az --chown=1000:1000 compose.yml .env.prod $(PROD_USER)@$(PROD_NODE):$(PROD_DIR)/
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

demo-up: build-local harness-build ## Bring up the demo stack — FAKE Kidde endpoint + auto-provisioned InfluxDB + Grafana
	$(DEMO_DC) up -d
	@echo "Grafana:  http://localhost:$(or $(GRAFANA_PORT),3000)  (admin/admin)  — dashboards populate from the fake Kidde feed"

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

lint: ## luxlint (ruff + the canonical checks, mount-only). Needs LUXLINT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping lint"; \
	else docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE); fi

# The type leg is its OWN gate step (repo.makefile_canonical). Since luxlint 0.2x it is
# mount-only like ruff — the fleet's typed-dependency union is baked into the image, so
# there is no in-repo mypy tail to install and no second, drifting mypy pinned here.
mypy: ## mypy via luxlint (fleet stubs baked, mount-only). Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping mypy"; \
	else docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --mypy; fi

format: ## Apply the CANONICAL fixer in place: ruff check --fix + ruff format + markdown. Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping format"; \
	else docker run --rm --user $(REPO_UID):$(REPO_GID) -v $(PWD):/repo $(LUXLINT_IMAGE) --format; fi

# Test-block settings: the suite runs against a REAL InfluxDB (compose service `kidde_influxdb_test`,
# profile `test`), never a mock (repo.test_stack_parity). .env.e2e is committed and fills the
# `${TAG:?}`/`${ENV_FILE:?}` interpolation compose does for every service, even unstarted ones.
TEST_ENV_FILE := .env.e2e
TEST_SERVICES := kidde_influxdb_test
TEST_ENV := -e TEST_INFLUXDB_URL=http://kidde_influxdb_test:8086 -e TEST_INFLUXDB_TOKEN=kidde-test-token \
            -e TEST_INFLUXDB_ORG=kidde -e TEST_INFLUXDB_BUCKET=kidde

# luxarch:test-block asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-block`.
# ── Test: THE suite, in the test image, against an isolated stack of real services ───────────────
# Emitted by `luxarch --emit test-block`; paste below the image block (it uses TEST_IMAGE and
# test-build from there). Enforced by `repo.test_block_wired`. Settings are `?=` defaults: set them
# above this block. What the suite can see, and what only `make smoke` sees: --doc
# FLEET-MAKEFILE-STANDARD §1.
#
# Before this block every repo wrote its own `make test`: four repos, four ways (a lint image with
# the source mounted, the dev image with pytest pip-installed at run time, a repo script, a test
# image), each with its own readiness loop and coverage wiring, and a coverage pipe under make's
# /bin/sh that hid pytest's failure. This is the documented practice of the tools instead:
#   - ISOLATED STACK (Docker Compose): the backing services run in a compose project of their own,
#     one per run (`-p`), so parallel runs never collide, the suite cannot reach the dev database or
#     cache at all (it is on another network), and teardown (`down --volumes --remove-orphans`)
#     removes exactly this run's containers and data, pass or fail, never the dev stack.
#   - READINESS from each service's own compose healthcheck (`up --wait`), not a sleep or a loop:
#     the service declares when it is ready. For Postgres, probe over TCP with the real role
#     (`pg_isready -h 127.0.0.1 -U <user> -d <db>`); over the socket it answers while initdb's
#     temporary server is still up. `repo.test_stack_parity` checks these are the services prod runs.
#   - Ctrl-C stops the suite (`--init` forwards the signal; a shell as PID 1 ignores it).
#   - THE TEST IMAGE built from this source (`test-build`, the image's `--with dev` stage), with the
#     fleet's pytest config (`luxlint --emit-config pytest`: -ra, strict markers and config,
#     warnings are errors), readable by the image's non-root user. The source is mounted read-only
#     and pytest writes no cache into it.
#   - COVERAGE with coverage.py itself, not pytest-cov: `coverage run --branch` under the sysmon
#     core (fast branch coverage on Python 3.14), data in /tmp, then `coverage report` judged by
#     `luxlint --coverage-ratchet` against `[test].coverage_min` (off until you set a floor; it
#     only ratchets up). `coverage` belongs in the dev dependency group.
#   - BOTH EXIT CODES reach make: pytest's and the ratchet's. No pipe carries either. The ratchet
#     reads coverage's own report file, never the suite's output (a printed `TOTAL … 100%` or
#     pytest's `[100%]` would otherwise pass for a measurement), and a report that measured nothing
#     fails.
#   - Each run's project is named from its own `mktemp -d` token, and refuses to run without one (a
#     PID repeats across containers and CI runners), and everything mounts the makefile's directory ($(CURDIR)), so `make -C` runs the
#     right suite.

# Setting: the compose command, and the profile holding the test services --------------------
TEST_COMPOSE ?= docker compose
TEST_PROFILE ?= test
# Setting: the env file compose interpolates the file with (it reads EVERY service, so an app
# service's `${TAG:?}` needs a value even when only the test services start) -----------------
TEST_ENV_FILE ?= $(firstword $(wildcard .env.test .env.dev .env.example))
# Setting: the backing services the suite needs (each with a healthcheck); empty: none -----------
TEST_SERVICES ?= db-test
# Setting: the suite's environment: the test services' URLs, by service name on the test network -
TEST_ENV ?= -e TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db-test:5432/postgres
# Setting: where the suite runs from (a monorepo's apps/backend), and every package coverage
# measures, comma-separated (`app,collector`): it replaces any [tool.coverage.run] source ---------
TEST_WORKDIR ?= .
TEST_COV ?= app
# Setting: NONE. For a one-off run only, on the command line: `make test PYTEST_ARGS='-k orders'`.
# A committed value narrows THE suite for everyone (`repo.test_block_wired` reds one) ---------
PYTEST_ARGS ?=

# One-off pytest arguments reach the container through the environment, never spliced into a quoted
# command line (`-k 'a or b'` would otherwise split it).
export PYTEST_ARGS

test: test-build ## THE suite: test image, an isolated stack of real services, coverage ratchet
	@set -u; \
	D=$$(mktemp -d); tok=$$(basename "$$D" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9'); \
	[ $${#tok} -ge 8 ] || { echo "REFUSING: could not make a unique name for this run"; rm -rf "$$D"; exit 2; }; \
	run="t$$(printf '%s' '$(notdir $(CURDIR))' | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')-test-$$tok"; \
	dc="$(TEST_COMPOSE) -p $$run $(if $(TEST_ENV_FILE),--env-file $(TEST_ENV_FILE)) --profile $(TEST_PROFILE)"; \
	chmod 777 "$$D"; \
	trap '$$dc down --volumes --remove-orphans >/dev/null 2>&1; rm -rf "$$D"' EXIT INT TERM; \
	net=; if [ -n "$(TEST_SERVICES)" ]; then \
	  $$dc up -d --wait --wait-timeout 120 $(TEST_SERVICES) \
	    || { echo "FAIL  the test services did not become healthy: $(TEST_SERVICES)"; exit 1; }; \
	  cid=$$($$dc ps -q $(firstword $(TEST_SERVICES))); net=; \
	  for n in $$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}} {{end}}' $$cid); do \
	    [ "$$(docker network inspect -f '{{index .Labels "com.docker.compose.project"}}' $$n)" = "$$run" ] && { net=$$n; break; }; done; \
	  [ -n "$$net" ] || { echo "FAIL  $(firstword $(TEST_SERVICES)) joined no network of this run's own project ($$run)"; exit 1; }; \
	  net="--network $$net"; fi; \
	docker run --rm -v $(CURDIR):/repo $(LUXLINT) --emit-config pytest > "$$D/pytest.ini" || exit 2; \
	chmod 644 "$$D/pytest.ini"; \
	docker run --rm --init $$net $(TEST_ENV) -e PYTEST_ADDOPTS="$${PYTEST_ARGS:-}" \
	  -e COVERAGE_CORE=sysmon -e COVERAGE_FILE=/out/.coverage -e PYTHONDONTWRITEBYTECODE=1 \
	  -v $(CURDIR):/repo:ro -v "$$D":/out -w /repo/$(TEST_WORKDIR) $(TEST_IMAGE) \
	  sh -c 'python -m coverage run --branch --source=$(TEST_COV) -m pytest -c /out/pytest.ini --rootdir=. -p no:cacheprovider; s=$$?; python -m coverage report --show-missing > /out/coverage.txt; echo $$? > /out/coverage.rc; cat /out/coverage.txt; exit $$s'; \
	rc=$$?; \
	[ "$$rc" = 0 ] || { echo "FAIL  the suite failed (exit $$rc)"; exit 1; }; \
	[ "$$(cat "$$D/coverage.rc" 2>/dev/null)" = 0 ] || { echo "FAIL  coverage measured nothing (coverage report: $$(tail -n 1 "$$D/coverage.txt" 2>/dev/null)): check TEST_COV names the package the suite imports"; exit 1; }; \
	docker run --rm -i -v $(CURDIR):/repo $(LUXLINT) --coverage-ratchet < "$$D/coverage.txt" > "$$D/ratchet.txt"; crc=$$?; \
	sed -n '/coverage ratchet/,$$p' "$$D/ratchet.txt"; \
	[ "$$crc" = 0 ] || { echo "FAIL  the coverage ratchet failed (its verdict is above): add tests, never lower the floor"; exit 1; }

arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml). Needs LUXARCH_REGISTRY (Makefile.local).
	@if [ -z "$(LUXARCH_REGISTRY)" ]; then \
	  echo "luxarch: LUXARCH_REGISTRY unset (see Makefile.local.example) — skipping arch"; \
	else docker run --rm -v $(PWD):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION); fi

audit: ## Dependency-vuln scan via luxaudit (pinned; poetry.lock vs live OSV+PyPA feed). Needs LUXAUDIT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXAUDIT_REGISTRY)" ]; then \
	  echo "luxaudit: LUXAUDIT_REGISTRY unset (see Makefile.local.example) — skipping audit"; \
	else docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE); fi

test-e2e: build-local harness-build ## Hardware-free end-to-end test: fake Kidde endpoint -> collector -> InfluxDB
	./scripts/e2e-test.sh

guard-version-check: ## FATAL: fail the gate if any fleet guard pin is behind the published latest
	@reg="$(LUXARCH_REGISTRY)"; \
	if [ -z "$$reg" ]; then echo "guard-version-check: registry unset (Makefile.local) — skipping"; exit 0; fi; \
	rc=0; \
	for gv in "luxarch $(LUXARCH_VERSION)" "luxlint $(LUXLINT_VERSION)" "luxaudit $(LUXAUDIT_VERSION)"; do \
	  set -- $$gv; name=$$1; pin=$$2; \
	  docker pull -q $$reg/luxardolabs/$$name:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $$reg/luxardolabs/$$name:latest --version 2>/dev/null | awk '{print $$2}'); \
	  if [ -n "$$latest" ] && [ "$$latest" != "$$pin" ]; then \
	    printf "✗ %-9s pinned %s, latest %s — behind. Preview: --new-rules --since %s; then \`make guard-upgrade\`\n" "$$name" "$$pin" "$$latest" "$$pin"; rc=1; \
	  else printf "✓ %-9s %s (latest)\n" "$$name" "$$pin"; fi; \
	done; exit $$rc

# luxarch:guard-upgrade asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit guard-upgrade`.
guard-upgrade:  ## Bump every guard pin to the published latest (prints what newly bites)
	@for g in luxarch luxlint luxaudit; do \
	  docker pull -q $(REGISTRY)/luxardolabs/$$g:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(REGISTRY)/luxardolabs/$$g:latest --version 2>/dev/null | awk '{print $$2}'); \
	  var=$$(echo $$g | tr a-z A-Z)_VERSION; \
	  old=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ -z "$$old" ]; then echo "!! no $$var pin found in Makefile — NOT bumped"; continue; fi; \
	  if [ -z "$$latest" ]; then echo "!! could not read $$g:latest — $$var left at $$old"; continue; fi; \
	  checked=1; \
	  sed -i -E "s|^($$var[[:space:]]*:=[[:space:]]*).*|\\1$$latest|" Makefile; \
	  new=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ "$$new" != "$$latest" ]; then echo "!! $$var did NOT change (still $$new)"; exit 1; fi; \
	  if [ "$$old" != "$$latest" ]; then echo "$$var $$old -> $$latest"; bumped=1; fi; \
	  [ "$$g" = luxarch ] && [ "$$old" != "$$latest" ] && docker run --rm -v $(PWD):/repo $(REGISTRY)/luxardolabs/luxarch:$$latest --new-rules --since $$old || true; \
	done; \
	if [ -n "$$bumped" ]; then echo "pins bumped — re-run make check"; \
	elif [ -n "$$checked" ]; then echo "all pins already at latest"; \
	else echo "!! could not reach the registry — NO pin was checked; currency NOT established"; exit 1; fi

# HONESTY gate: a green `make check` must mean nothing was silently unchecked.
#   luxarch --assert-scans : FAIL iff a rule family scanned ZERO files (a hollow green) — NOT on reds
#   luxlint --preflight    : FAIL iff the mypy leg would be dishonest (shared-overlay Any-flood)
honest: ## Honesty gate — fail if a guard family scanned nothing or the type leg is dishonest
	@if [ -z "$(LUXARCH_REGISTRY)" ] || [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "honest: guard registry unset (Makefile.local) — skipping"; exit 0; fi
	@docker run --rm -v $(PWD):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --assert-scans
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --preflight

# THE fleet gate — byte-identical composition across every repo (repo.makefile_canonical).
# Order: pin drift -> honesty -> ruff -> types -> tests -> architecture -> dep CVEs -> secrets.
# It is a GATE, not a report: Make stops at the first failing step. For the full red board at
# once, run `make plan` (luxarch --plan) — reds phase-ordered and clustered by file.
check: guard-version-check honest lint mypy test arch audit gitleaks ## THE fleet gate — run before every commit

plan: ## The full luxarch red board at once — phase-ordered, clustered by file (the worklist)
	@if [ -z "$(LUXARCH_REGISTRY)" ]; then echo "luxarch: LUXARCH_REGISTRY unset — skipping"; exit 0; fi; \
	docker run --rm -v $(PWD):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --plan

# Committed guard-status files: the fleet READS these instead of re-running every guard on
# every repo (luxarch --doc FLEET-STATUS). They are guard-generated lockfiles, never
# hand-edited — `--json` plus the commit SHA + UTC timestamp they were computed at, so a
# stale green is DETECTED on read rather than silently trusted. A red repo still commits a
# valid red status. `|| true` because `--json` exits non-zero when the repo is red — the
# verdict is IN the JSON. `set -e` so a failed stamp aborts instead of writing a false green.
# STAMP refuses a document that is not the expected guard's own (`"guard"` in every --json), and
# each run writes a per-run `mktemp` — never a fixed /tmp path every repo on the host shares.
# LUXARCH_STATUS_WRITE=1 keeps repo.guard_status_current from judging the files being replaced.
STAMP = python3 -c 'import json,sys,os; d=json.load(open(sys.argv[1])); g=d.get("guard"); g==sys.argv[3] or sys.exit(f"status: {sys.argv[1]} holds {g!r} output, expected {sys.argv[3]!r}; refusing to stamp"); d["commit"]=os.environ["SHA"]; d["generated_at"]=os.environ["TS"]; json.dump(d,open(sys.argv[2],"w"),indent=2)'

status: ## Regenerate the committed guard-status files (.lux*-status.json) — then COMMIT them
	@if [ -z "$(LUXARCH_REGISTRY)" ] || [ -z "$(LUXLINT_REGISTRY)" ] || [ -z "$(LUXAUDIT_REGISTRY)" ]; then \
	  echo "status: guard registry unset (Makefile.local) — skipping"; exit 0; fi
	@set -e; export SHA=$$(git rev-parse HEAD) TS=$$(date -u +%FT%TZ); \
	J=$$(mktemp); trap 'rm -f "$$J"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --json > "$$J" || true; $(STAMP) "$$J" .luxlint-status.json luxlint; \
	docker run --rm -v $(PWD):/repo -e LUXARCH_STATUS_WRITE=1 $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --json > "$$J" || true; $(STAMP) "$$J" .luxarch-status.json luxarch; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) --json > "$$J" || true; $(STAMP) "$$J" .luxaudit-status.json luxaudit; \
	echo "wrote .lux*-status.json at $$SHA — commit them"

# The asset reads the luxlint image as $(LUXLINT); the gitleaks mirror lives beside it.
LUXLINT = $(LUXLINT_IMAGE)

# luxarch:gitleaks asset v12 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit gitleaks`.
# ── The privacy gate: BOTH surfaces ─────────────────────────────────────────────────────────────
# Emitted by `luxarch --emit gitleaks`. Drop in verbatim.
#
# `gitleaks` scans DIFF CONTENT. A commit's author/committer address lives in the commit object
# HEADER and never appears in a patch, so no content rule can ever match it — it is a surface the
# scanner does not read. A repo reported `no leaks found` over 963 commits while 29 of them carried a
# personal address in both the author and committer fields, and it would have reported exactly the
# same thing after the scrub: identical output, opposite truth. Measured across the fleet, EIGHT
# repos carry a personal address in history and two of them are PUBLIC.

# Commit identities this repo accepts. The fleet account's `users.noreply.github.com` address, plus
# GitHub's own web-UI committer. Widen ONLY for a real outside contributor, with a comment saying who.
# NOT for the org account's real address: a role mailbox in commit metadata is published with every
# clone exactly like a personal one (six fleet repos carried it, one PUBLIC). Its
# omission here is the policy, not an oversight: the answer is the scrub printed below, and the
# repo's agent performs it once the OWNER approves the force-push.
# Anchored on the CLOSING BRACKET, because the compared line is `Name <email>` — not a bare
# address. The first cut allowed `^noreply@github.com$$`, which can NEVER match a
# `Name <email>` line, so the GitHub web-UI identity was silently DENIED and the canonical
# recipe would have refused on any repo carrying a web-UI commit. Measured across the fleet: it
# denied 4 of 6 distinct identity lines instead of the 3 real offenders.
# It was missed because the only repo it was tested on has no web-UI commits, so the broken
# branch never ran. The bracket also closes a substring hole: unanchored,
# `<x@users.noreply.github.com.attacker.test>` would have been allowed.
# v11: the noreply address is `<local@users.noreply.github.com>`, and the local part has no `@`. v10's
# `<[^>]*users…` admitted `<dev.real@gmail.com.users.noreply.github.com>`, a real address in the clear.
# v12: the fleet ACCOUNT, not any noreply address. v11 accepted `<anyone@users.noreply.github.com>`, so a
# stranger's (or a second account's) noreply committer passed. Measured: the fleet's whole history holds
# exactly two noreply identities, the fleet account and GitHub's web-UI committer.
GIT_IDENTITY_OK ?= <214140984\+luxardolabs@users\.noreply\.github\.com>$$|<noreply@github\.com>$$

# The secret scanner, PINNED and MIRRORED in the fleet registry. The fleet bans a moving tag
# everywhere it can see one, and this used to ship `ghcr.io/gitleaks/gitleaks:latest` inside the asset every
# repo adopts verbatim: the privacy gate could not run with ghcr unreachable or the local copy pruned, and
# nothing recorded which scanner said "no leaks found". New detection rules still arrive, through the fleet's
# own mechanism: luxarch bumps this pin in a release, and `repo.emitted_assets_current` tells you to re-emit.
# v5: the HOST is never written here. v4 inlined the private registry, so dropping
# this asset in "verbatim" put the host into a committed Makefile, and on a public repo the fleet's
# own gitleaks disclosure tier refused the commit. The mirror lives beside the guards, so the ref is
# derived from wherever this repo already pulls luxlint (`$(LUXLINT)`, which the scan below needs
# anyway). It works whichever variable holds your guard registry (REGISTRY, LUXARCH_REGISTRY, …).
# Recursive `=` so it resolves at use, whatever order LUXLINT is defined in.
# v9: PINNED BY DIGEST, and buildable off-network. The digest is the scanner's identity; the registry is
# only where it is fetched from. Beside a registry-qualified `$(LUXLINT)` it pulls the fleet mirror; with
# a local guard build (`luxlint:local`, on a machine with no access to the fleet registry, such as an
# airgapped laptop) it pulls the public image. v8 derived `./gitleaks:…` there, an unpullable reference, so the
# privacy gate could not run at all. The mirror and the public image share the digest, so both
# resolve to the same bits, and a tampered or re-tagged copy fails the pull instead of scanning.
GITLEAKS_IMAGE = $(if $(findstring /,$(LUXLINT)),$(dir $(LUXLINT)),zricethezav/)gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

gitleaks: ## secret scan over FULL HISTORY + the commit-identity pass (the hooks cover commit/push)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) git /repo -c /gl.toml --redact -v --ignore-gitleaks-allow
	@# v12: what `gitleaks git` never reads. It scans git's PATCHES, and git prints no patch for a file it
	@# treats as binary: a NUL byte, UTF-16 (a `Localizable.strings`), or a `binary` / `-diff` attribute.
	@# A token in any of them reached the remote with "no leaks found". So every path git ever showed as
	@# binary is re-read as text, NULs stripped, and scanned by path (the config's path allowlists hold).
	@set -e; C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	git -c core.quotePath=false log --branches --tags HEAD --format= -p --no-ext-diff --no-textconv \
	  > "$$D/patches"; \
	sed -n 's|^Binary files .* and b/\(.*\) differ$$|\1|p' "$$D/patches" | sort -u > "$$D/binary"; \
	if [ -s "$$D/binary" ]; then \
	  mkdir "$$D/t" "$$D/none"; \
	  while IFS= read -r f; do \
	    mkdir -p "$$D/t/$$(dirname "$$f")"; \
	    git -c core.quotePath=false log --branches --tags HEAD --format= -p --text --no-ext-diff \
	      --no-textconv -- "$$f" > "$$D/p"; \
	    grep -a '^+' "$$D/p" | tr -d '\000' > "$$D/t/$$f"; \
	  done < "$$D/binary"; \
	  docker run --rm -v "$$D/t":/scan:ro -w /scan -v "$$D/none":/none:ro -v "$$C":/gl.toml:ro \
	    $(GITLEAKS_IMAGE) dir . -c /gl.toml --redact -v --ignore-gitleaks-allow \
	    --gitleaks-ignore-path /none; \
	fi
	@# The identity pass — the half gitleaks structurally cannot do. Cheap: one `git log`.
	@# Walks what THIS repo publishes (branches, tags, HEAD), NOT `--all`: a remote-tracking ref caches the
	@# remote's state, which during a scrub is by definition the un-rewritten history you are about to
	@# force-push over — `--all` refused the verified fix, and any `git fetch` re-armed it.
	@bad=$$(git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' 2>/dev/null | sort -u \
	  | grep -vE '$(GIT_IDENTITY_OK)' || true); \
	if [ -n "$$bad" ]; then \
	  echo "REFUSING: a non-fleet identity appears in commit METADATA (author/committer):"; \
	  echo "$$bad" | sed 's/^/    /'; \
	  echo "gitleaks cannot see this — it scans diffs, not commit headers, so it reported no leaks."; \
	  echo "An address here is attached to every affected commit forever, not to one line of one file."; \
	  echo "Scrub per FLEET-ONBOARDING-STANDARD §2: mirror backup -> git filter-repo -> re-verify with"; \
	  echo "  git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' | sort -u"; \
	  echo "-> ask the OWNER to approve the force-push, then do it yourself. Never force-push unapproved."; \
	  exit 1; \
	fi

# v6: the STAGED scan the commit hook calls (`hooks/pre-commit` → `make gitleaks-staged`) is part of the
# asset now. v5 shipped only the full-history half, so 9 of 10 adopting repos hand-wrote this target
# and the tenth had none, leaving its pre-commit hook pointing at a missing recipe. If your Makefile
# carries its own `gitleaks-staged`, delete it when you re-emit: this one replaces it.
# v7: `-w /repo` is LOAD-BEARING. Without it git runs outside the repo, falls back to `git diff
# --no-index`, rejects `--staged`, and gitleaks EXITS 0: v6 let a staged secret through while printing
# a git error (measured on a planted GitHub token: v6 exit 0, v7 "leaks found: 1" exit 1).
# v8: the denylist goes to a PER-RUN `mktemp` file, removed on exit. v7 wrote a fixed
# `/tmp/gl.toml` that outlived the run: on a host where commit and push run as different users, the
# next user's redirect was refused (`fs.protected_regular=1`, the Fedora default, blocks O_CREAT on
# another user's file in sticky /tmp even for root), so the privacy gate failed every commit or push
# after a user switch (2 of 2 measured). Two repos scanning at once also shared one file, so one could
# scan with the other's carve-outs. The full-history scan now also passes `-w /repo`, like the staged one.
# v12: the staged bytes are read on the HOST and scanned as files, by path. v11 ran `protect --staged`
# inside the container, which read `.git/index`, not the temporary index git hands the hook in
# `$$GIT_INDEX_FILE`: `git commit -a` and `git commit <path>` were never scanned. It also read git's
# patches, which skip binary, NUL, UTF-16 and `-diff` files, and it honoured a `# gitleaks:allow` on
# the secret's own line and a committed `.gitleaksignore`, so a commit's author could waive their own
# leak. New content gets no waiver: `.gitleaksignore` stays a ledger for the full-history scan only.
gitleaks-staged: ## secret scan of the STAGED changes (run by hooks/pre-commit)
	@set -e; C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	mkdir "$$D/t" "$$D/none"; \
	git -c core.quotePath=false diff --cached --name-only --diff-filter=ACMR -z > "$$D/names"; \
	tr '\000' '\n' < "$$D/names" | while IFS= read -r f; do \
	  [ -n "$$f" ] || continue; \
	  mkdir -p "$$D/t/$$(dirname "$$f")"; \
	  git -c core.quotePath=false diff --cached --text --no-ext-diff --no-textconv -U0 -- "$$f" \
	    > "$$D/p"; \
	  grep -a '^+' "$$D/p" | tr -d '\000' > "$$D/t/$$f"; \
	done; \
	docker run --rm -v "$$D/t":/scan:ro -w /scan -v "$$D/none":/none:ro -v "$$C":/gl.toml:ro \
	  $(GITLEAKS_IMAGE) dir . -c /gl.toml --redact -v --ignore-gitleaks-allow \
	  --gitleaks-ignore-path /none

install-hooks: ## Activate the committed fleet secret hooks (core.hooksPath=hooks; pre-commit + pre-push)
	git config core.hooksPath hooks
	@echo "✓ secret hooks active (core.hooksPath=hooks: gitleaks-staged on commit, gitleaks on push)"

##@ Utilities

clean: ## Clean python/test caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ .coverage htmlcov/
	rm -f .test-image.stamp .luxlint.mypy.ini .ruff.local.toml

clean-all: clean docker-clean ## Clean caches + local docker image tags
