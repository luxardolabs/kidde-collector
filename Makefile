# =============================================================================
# kidde-collector — fleet build/deploy Makefile
# Source of truth for the version is the VERSION file at the repo root.
# Compose never builds; the Makefile builds the image + pushes it to the registry,
# and the dev/prod stacks pull it. Mirrors the bb-boutique fleet standard, trimmed
# for a single-service collector (no db/redis/nginx/css).
# =============================================================================

VERSION := $(shell cat VERSION 2>/dev/null || git -c safe.directory=$(CURDIR) describe --tags --always 2>/dev/null || echo "0.0.0-dev")
TIMESTAMP := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

# Private registry host stays OUT of this public repo — set it in gitignored Makefile.local
# (see Makefile.local.example). Without it, registry-dependent targets (release / publish-sha /
# lint / format / arch / test-e2e) refuse or skip; build-local + the compose stacks work.
# The deploy image names (SHA_IMAGE, VERSION_IMAGE, the :dev alias, PUBLIC_IMAGE) come from the
# image block below.
-include Makefile.local
REGISTRY ?=
IMAGE_NAME := luxardolabs/kidde-collector
# Locally-built images for the local stacks (up / dev / demo / e2e) — BUILT, never pushed, so
# their names are BARE (repo.image_name_declares_provenance): a registry-qualified name promises
# an artifact a `docker image prune` would silently delete. The committed .env.demo / .env.e2e
# set REGISTRY= (empty), which compose resolves to these same bare names.
LOCAL_IMAGE   := $(notdir $(IMAGE_NAME)):local
FAKE_IMAGE    := $(notdir $(IMAGE_NAME))-fake:local

# Recursive (`=`), so BUILD_COMMIT reads the image block's COMMIT, the one its `sha-<commit>` tag
# uses: a second, separately computed COMMIT here labelled images with a commit the tag did not name.
BUILD_ARGS = --build-arg BUILD_VERSION=$(VERSION) \
              --build-arg BUILD_TIMESTAMP=$(TIMESTAMP) \
              --build-arg BUILD_COMMIT=$(COMMIT)

# Cache busting for the local builds: `make build-local NOCACHE=1`
NOCACHE ?=
NO_CACHE_FLAG := $(if $(NOCACHE),--no-cache,)

# ANSI colors for `make help`
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
CYAN := \033[0;36m
NC := \033[0m
BOLD := \033[1m

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
LUXARCH_VERSION  := 0.289.3

# Code-style + type standard (luxlint) — pinned; registry host comes from Makefile.local.
# luxlint ships from the PRIVATE registry only (never GHCR), so the host stays out of this
# public repo exactly like LUXARCH_REGISTRY. Without it, `make lint`/`make format` skip.
LUXLINT_REGISTRY ?=
LUXLINT_VERSION  := 0.66.0
LUXLINT_IMAGE    := $(LUXLINT_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)

# Dependency-vulnerability guard (luxaudit) — pinned; registry host comes from Makefile.local.
# Mount-only, no tail: reads poetry.lock and checks each pinned dep against the LIVE OSV+PyPA
# advisory feed (fetched at run, never baked — so the same pinned image reports a new CVE the day
# it drops, no rebuild). Private registry only. Without the host, `make audit` skips. Same
# out-of-tree pattern as LUXARCH_REGISTRY / LUXLINT_REGISTRY.
LUXAUDIT_REGISTRY ?=
LUXAUDIT_VERSION  := 0.14.0
# Empty without the registry, never a hostless `/luxardolabs/…` ref: the image block's
# require_scanner then refuses before anything is built or pushed.
LUXAUDIT_IMAGE    := $(if $(LUXAUDIT_REGISTRY),$(LUXAUDIT_REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION))
POETRY_RUN := docker run --rm -u $(REPO_UID):$(REPO_GID) -e HOME=/tmp -v $(CURDIR):/work -w /work python:3.14-slim sh -c
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
        build-local publish-sha release release-context-clean buildx-setup \
        dev-deploy dev-pin smoke docker-inspect docker-clean \
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
	@echo "Build:     $(SHA_IMAGE)  (alias $(DEV_ALIAS))"
	@echo "Release:   $(VERSION_IMAGE)"
	@echo "Public:    $(PUBLIC_IMAGE)"

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

harness-build: ## Build the fake-Kidde emulator image from ./harness (outside compose, never pushed)
	docker build $(NO_CACHE_FLAG) -t $(FAKE_IMAGE) ./harness
	@echo "Built $(FAKE_IMAGE)"

build-local: ## Build the runtime image from CURRENT source as a local tag (no push, no registry)
	docker build $(NO_CACHE_FLAG) --target production -f Dockerfile $(BUILD_ARGS) -t $(LOCAL_IMAGE) .

# Image-block settings (above the block). REGISTRY comes from the gitignored Makefile.local; the
# release goes to the same private registry, and PUBLIC_REGISTRY adds the GHCR copy (release-public).
# PLATFORMS: the public image runs on arm64 hosts too, so the release is multi-arch.
PLATFORMS       ?= linux/amd64,linux/arm64
PUBLIC_REGISTRY ?= ghcr.io

# ── Release gates this repo adds IN FRONT of the image block (prerequisites, never recipe edits) ─
# The lock audit gates every push: the block's candidate scan only refuses fixable HIGH/CRITICAL in
# the image, and a MEDIUM CVE in a locked package (multidict CVE-2026-104874 in 2026.10.0) passes it.
# `git status --porcelain` does not list gitignored files, but the build context holds them: a
# session-cookie dump under app/ would pass the block's dirty-tree check and ship. So refuse any
# ignored file under app/ (what the Dockerfile COPYs) except the caches .dockerignore drops, and
# any tracked file git has been told to stop looking at (skip-worktree / assume-unchanged): its
# local edit is invisible to `git status` but still in the build context.
PUBLISH_GOALS := release publish-sha release-public dev-deploy
release publish-sha: audit release-context-clean
ifneq ($(strip $(PUBLIC_REGISTRY)),)
release-public: audit release-context-clean
endif

# The gates are separate targets, and `make -i` (or an `i` in MAKEFLAGS, which every sub-make such
# as dev-deploy's inherits) ignores a failing prerequisite and runs the push recipe anyway. The
# block survives -i by refusing inside its one shell; these cannot, so publishing under -i is
# refused before anything runs.
ifneq ($(filter $(PUBLISH_GOALS),$(MAKECMDGOALS)),)
ifneq ($(findstring i,$(firstword -$(MAKEFLAGS))),)
$(error REFUSING: make -i ignores a failing release gate (audit, release-context-clean), so nothing would stop the push. Publish without -i)
endif
endif

release-context-clean:
	@ign=$$(git ls-files --others --ignored --exclude-standard -- app) || { \
	  echo "REFUSING: git ls-files failed, so nothing proves the build context is clean"; exit 1; }; \
	ign=$$(printf '%s\n' "$$ign" | grep -vE '^$$|(^|/)__pycache__/|\.py[cod]$$|(^|/)\.DS_Store$$' || true); \
	if [ -n "$$ign" ]; then \
	  echo "REFUSING: gitignored files sit in the build context and would ship:"; \
	  printf '%s\n' "$$ign" | sed 's/^/    /'; \
	  echo "Remove them (or add them to .dockerignore) before publishing."; \
	  exit 1; \
	fi; \
	flags=$$(git ls-files -v) || { \
	  echo "REFUSING: git ls-files -v failed, so nothing proves no tracked file is hidden from git status"; exit 1; }; \
	hidden=$$(printf '%s\n' "$$flags" | grep -E '^([a-z]|S) ' || true); \
	if [ -n "$$hidden" ]; then \
	  echo "REFUSING: tracked files are marked skip-worktree/assume-unchanged, so a local edit to them ships unseen:"; \
	  printf '%s\n' "$$hidden" | sed 's/^/    /'; \
	  echo "Clear them (git update-index --no-skip-worktree / --no-assume-unchanged <path>) before publishing."; \
	  exit 1; \
	fi

# luxarch:image-block asset v16 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit image-block`.
# ── Images: ONE naming scheme, and the NAME declares what the image IS ──────────────────────────
# Emitted by `luxarch --emit image-block`. Two axes, both readable from the name alone:
#
#   Registry-qualified  ->  DEPLOY artifact  ->  built AND PUSHED
#   Bare (no registry)  ->  LOCAL artifact   ->  built from source every run, NEVER pushed
#
#   Immutable tag (:$(VERSION), :sha-…)  ->  a stack MAY pin it
#   Moving tag (:dev, :latest)           ->  a human alias; NOTHING pins it, NOTHING builds FROM it
#
# **Immutable tags are the point.** "A deployable never rolls" is why `:latest` is banned as a
# deploy tag — and `:dev` rolls identically. Every `docker push` of `:dev` silently changes what
# every stack pinned to it will run on next restart, so a moving tag cannot be a deployment record:
# you cannot say which bits are in dev, and you cannot roll back to them. One build -> one permanent
# identifier -> environments reference THAT. The sha costs nothing: this repo already computes it
# for BUILD_COMMIT.
#
# **A local-only image must not wear a registry name.** One repo's compose pulled
# `$(REGISTRY)/luxardolabs/<app>:dev` with no `build:` fallback, its Makefile never pushed it,
# the registry held ZERO tags, and a routine `docker image prune` took the service down with
# nothing to re-pull. The name promised a registry artifact; only a local one existed.
#
# Enforced by `repo.image_name_declares_provenance`.

# v14: the registry HOST is never written here. v1-v13 defaulted REGISTRY to the fleet's private host
# in this do-not-edit text, so a PUBLIC repo could not commit the block at all: its own gitleaks
# disclosure tier refuses that host in a committed file. Define it ABOVE the block: a private repo in
# its Makefile (`REGISTRY := <host>:<port>`), a public one in its gitignored `Makefile.local`
# (`-include Makefile.local` above the block). Left empty, every target that pushes or pins refuses.
REGISTRY      ?=
# v13: the registry the RELEASE goes to, the one prod pulls from. The fleet has more than one: dev
# stacks pull from the local registry above, prod at a remote host pulls from a registry it can reach
# (a public app: GHCR). v1-v12 pushed `:$(VERSION)` to REGISTRY, so a repo whose prod cannot reach it
# had to override REGISTRY on the command line for every release, and a release cut without the
# override landed where prod could not pull it. Set it above the block when prod pulls elsewhere,
# e.g. `RELEASE_REGISTRY := registry.example.com`; one registry for both needs nothing.
RELEASE_REGISTRY ?= $(REGISTRY)
# v12: the repo's NAME, from its git remote, not the checkout directory. v11 used `$(notdir $(CURDIR))`, so a
# repo cloned into another directory (checked out under a shorter name on a build host) built and
# pushed an image nothing pulls, and the deploy reported success. With no remote it falls back to the
# directory. A repo whose image is named otherwise sets `IMAGE_NAME := luxardolabs/<name>` above the block.
REPO_SLUG     := $(basename $(notdir $(shell git config --get remote.origin.url 2>/dev/null)))
IMAGE_NAME    ?= luxardolabs/$(or $(REPO_SLUG),$(notdir $(CURDIR)))

# v16: one repo, one VERSION, one release, MORE THAN ONE image. v1-v15 published exactly one image per
# makefile, so a repo shipping two (a server and its agent, both built from the root with different
# Dockerfiles and both run by compose from one `TAG=`) could not adopt the block: two copies collide,
# and one makefile per image splits the release and pins `.env.dev`'s TAG twice. EMPTY, the default,
# is v15's one image (IMAGE_NAME, DOCKERFILE, BUILD_CONTEXT) and the same commands. Set, list the
# images above the block and give each its settings there, as `?=`:
#   IMAGES                 ?= backend agent
#   backend_IMAGE_NAME     ?= luxardolabs/backend
#   backend_DOCKERFILE     ?= Dockerfile
#   backend_BUILD_CONTEXT  ?= .
#   agent_IMAGE_NAME       ?= luxardolabs/agent
#   agent_DOCKERFILE       ?= agent/Dockerfile
#   agent_BUILD_CONTEXT    ?= .
#   agent_TARGET           ?= production
# `<name>_BUILD_CONTEXT` defaults to `.`, `<name>_DOCKERFILE` to `<context>/Dockerfile`, `<name>_TARGET`
# to `production`; `<name>_IMAGE_NAME` is required. `release` and `publish-sha` build and scan EVERY
# image before they push ANY: one failing scan and nothing is pushed, so a release ships whole or not
# at all. Every image gets the same `:$(VERSION)` and `:sha-<commit>`, and `dev-pin` checks each is in
# the registry before it writes the one shared `TAG=` once. The FIRST image is the one the single-image
# names below (IMAGE, VERSION_IMAGE, SHA_IMAGE, TEST_IMAGE) mean, so the suite, smoke and prod-deploy
# read it. A name is a shell word (letters, digits, `_`).
IMAGES        ?=
IMAGE_LIST    := $(strip $(IMAGES))
IMAGE_PRIMARY := $(firstword $(IMAGE_LIST))
# One image's settings and tags, `$(call img_<x>,<name>)`. An EMPTY name is the one image of IMAGES
# empty, so each value is exactly the single-image variable it stands beside.
img_name       = $(if $(1),$($(1)_IMAGE_NAME),$(IMAGE_NAME))
img_context    = $(if $(1),$(or $($(1)_BUILD_CONTEXT),.),$(BUILD_CONTEXT))
img_dockerfile = $(if $(1),$(or $($(1)_DOCKERFILE),$(call img_context,$(1))/Dockerfile),$(DOCKERFILE))
img_target     = $(or $(if $(1),$($(1)_TARGET)),production)
# With an EMPTY name each is the single-image VARIABLE itself, not a recomputation of it, so a value
# set on the command line (`make publish-sha IMAGE=…`) is the one checked AND the one pushed, as in
# v15 (an adversarial pass: v16 checked `$(SHA_IMAGE)` but pushed a recomputed ref, and re-pushed a
# published `:sha`).
img_base       = $(if $(1),$(REGISTRY)/$(call img_name,$(1)),$(IMAGE))
img_release    = $(if $(1),$(RELEASE_REGISTRY)/$(call img_name,$(1)),$(RELEASE_IMAGE))
img_version    = $(if $(1),$(call img_release,$(1)):$(VERSION),$(VERSION_IMAGE))
img_sha        = $(if $(1),$(call img_base,$(1)):sha-$(COMMIT),$(SHA_IMAGE))
img_alias      = $(if $(1),$(call img_base,$(1)):dev,$(DEV_ALIAS))
img_candidate  = $(if $(1),$(call img_base,$(1)):candidate-$(COMMIT),$(CANDIDATE))
img_public     = $(if $(1),$(PUBLIC_REGISTRY)/$(call img_name,$(1)):$(VERSION),$(PUBLIC_IMAGE))
img_test       = $(if $(1),$(notdir $(call img_name,$(1))):test,$(TEST_IMAGE))
# `$(call image_block_each,<macro>,<separator>)`: the macro once per image, in IMAGES order, joined by
# the separator; with IMAGES empty, once for the one image. It is why `make -n` prints each image's
# own commands.
image_block_each = $(call $(1),$(firstword $(IMAGE_LIST)))$(foreach i,$(wordlist 2,$(words $(IMAGE_LIST)),$(IMAGE_LIST)),$(2) $(call $(1),$(i)))

# BASE — no tag; every tag composes from it. The comment sits ABOVE the value, never after it:
# GNU make keeps the whitespace between a value and an inline `#`, so `IMAGE := …/repo   # note`
# defines IMAGE *with trailing spaces*, and every tag built from it — `…/repo   :0.1.0` — is an
# invalid docker reference. This block shipped that way from 0.197.0 and `make release` could not
# run at all; no repo had executed it, so three green rules stood over a recipe that was broken on
# its first line. Locked by test_emitted_makefiles_have_no_inline_comment_on_an_assignment.
IMAGE         := $(REGISTRY)/$(if $(IMAGE_PRIMARY),$($(IMAGE_PRIMARY)_IMAGE_NAME),$(IMAGE_NAME))
RELEASE_IMAGE := $(RELEASE_REGISTRY)/$(if $(IMAGE_PRIMARY),$($(IMAGE_PRIMARY)_IMAGE_NAME),$(IMAGE_NAME))
COMMIT        := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)

# IMMUTABLE deploy tags — the ONLY tags a stack may pin (`TAG=` in .env.<env>).
#   VERSION_IMAGE = cut releases, in the registry prod pulls from;  SHA_IMAGE = every other build,
#   addressable, no ceremony, in the dev registry.
VERSION_IMAGE := $(RELEASE_IMAGE):$(VERSION)
SHA_IMAGE     := $(IMAGE):sha-$(COMMIT)

# MOVING alias — for a human reading `docker images`. Never pinned by a stack, never a build input,
# never a base. If you delete this line nothing breaks; that is the test of an alias.
DEV_ALIAS     := $(IMAGE):dev

# LOCAL verification image — BARE on purpose: it cannot be pushed by accident and cannot be
# mistaken for a deployable. Rebuilt from source every run, so the suite never inherits a stale
# interpreter or stale deps. It is NOT `:dev`; a repo that tested on `:dev` kept failing correct
# code against a 13-day-old Python 3.13 image after the tree had moved to 3.14.
TEST_IMAGE    := $(notdir $(if $(IMAGE_PRIMARY),$($(IMAGE_PRIMARY)_IMAGE_NAME),$(IMAGE_NAME))):test

# Where the image is built from. v9: v1-v8 built `.` with `./Dockerfile`, so a monorepo whose app builds
# from `apps/backend/` could only adopt the block by editing it. Set these above the block when the app
# is not at the root, e.g. `BUILD_CONTEXT := apps/backend`. Keep the context as narrow as the app: in a
# repo that holds data which must never ship, a narrow context is what keeps that data out of every
# image, structurally, rather than one `.dockerignore` pattern away.
BUILD_CONTEXT ?= .
DOCKERFILE    ?= $(BUILD_CONTEXT)/Dockerfile

# v14: the architectures a release builds. EMPTY, the default, builds the host's one architecture with
# `docker build --load`, as v13 did: everything the fleet runs itself is x86 (amd64). Set it
# only when this repo publishes an image someone ELSE runs on another architecture, as buildx's comma
# list, above this block (it picks the recipe when make reads it): `PLATFORMS := linux/amd64,linux/arm64`.
# Set, `release` and `publish-sha` build every platform once with `docker buildx build --push` on the
# shared builder (`buildx-setup`, `luxarch --emit buildx-setup`, in the same Makefile) under a
# `candidate-<commit>` tag nothing pins, scan EACH platform's image, and only then point the immutable
# tag at that candidate with `imagetools create`. The immutable tag is never built directly.
# --doc FLEET-BUILD-DEPLOY-STANDARD, "Architectures".
PLATFORMS ?=
IMAGE_BLOCK_COMMA := ,
PLATFORM_LIST := $(subst $(IMAGE_BLOCK_COMMA), ,$(strip $(PLATFORMS)))
# v14: where a multi-arch build pushes its candidate: REGISTRY, the private one, ALWAYS. A candidate
# exists to be scanned, and a rejected one must never be public: a release whose prod pulls from a
# public registry (RELEASE_REGISTRY := ghcr.io) would otherwise publish unscanned bits under a tag
# anyone can pull. `imagetools create` copies the scanned digest into RELEASE_REGISTRY afterwards.
CANDIDATE     := $(IMAGE):candidate-$(COMMIT)

# ── Deploy: build AND push, always together ─────────────────────────────────────────────────────
# Never split them. A `docker build` of a registry-qualified tag with no matching push is what
# leaves a deployable name pointing at nothing.
#
# v14: every target that pushes or pins is ONE `set -e` shell: its refusals, its build, its scan and
# its push are a single recipe line. v13 put each refusal and the scan on a line of its own, and make
# carries on past a failing line under `make -i`, a `-i` in MAKEFLAGS or a `.IGNORE:`: an existing
# `:$(VERSION)` was overwritten and a failing scan still pushed. The pieces below are shell text,
# joined into that one line by each target; never give one of them a recipe line of its own.

# Named `release`, not `publish`. An earlier cut of this block called it `publish` while
# `--emit release-skill` step 11, `--doc FLEET-RELEASE-PROCESS` §8 and `repo.github_release_wired`
# all said `release` — so a repo that correctly adopted THIS asset ended up with no `release` target
# and the rule stopped looking. Adopting the newer asset was what made the older rule go quiet.
# One name, and `release` is the one the skill, the standard, the rule and every
# fleet Makefile already use.

define require_registry
[ -n '$(strip $(REGISTRY))' ] && [ -n '$(strip $(RELEASE_REGISTRY))' ] || { \
  echo "REFUSING: REGISTRY is empty. Set it above the image block (a public repo: in Makefile.local)"; exit 1; }
endef

# v5: the scanner every push is gated by (luxaudit >= 0.13.0 in LUXAUDIT_IMAGE or LUXAUDIT), checked
# before anything is built.
define require_scanner
[ -n '$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))' ] || { \
  echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the candidate must be scanned before it is pushed"; exit 2; }
endef

# v16: every image IMAGES lists is named, by a shell word, and no two publish to one name.
define require_images
$(if $(IMAGE_LIST),for i in $(foreach i,$(IMAGE_LIST),'$(i)'); do case "$$i" in (''|[0-9]*|*[!A-Za-z0-9_]*) \
  echo "REFUSING: IMAGES entry '$$i' is not a shell word ([A-Za-z_][A-Za-z0-9_]*)"; exit 1 ;; esac; done; \
$(foreach i,$(IMAGE_LIST),[ -n '$(strip $($(i)_IMAGE_NAME))' ] || { \
  echo "REFUSING: IMAGES lists $(i) but $(i)_IMAGE_NAME is empty: set it above the image block"; exit 1; }; )\
[ $(words $(IMAGE_LIST) $(foreach i,$(IMAGE_LIST),$($(i)_IMAGE_NAME))) -eq $(words $(sort $(IMAGE_LIST)) $(sort $(foreach i,$(IMAGE_LIST),$($(i)_IMAGE_NAME)))) ] || { \
  echo "REFUSING: IMAGES names one image twice or gives two images one IMAGE_NAME"; exit 1; }; )
endef

# A `sha-<commit>` tag is immutable only if the bits ARE that commit. `git rev-parse HEAD` says nothing
# about the working tree: build with uncommitted changes and the registry holds an image named after
# commit X that contains X plus whatever was dirty, which is indistinguishable afterwards from a
# truthful one and worse than `:dev`, which is at least honestly ambiguous. One repo published
# `sha-b55e9a7b25ed` carrying a lockfile that commit does not have: bump deps, `make dev-build` to see
# it works, commit after. That is the routine order, and this recipe invited it. Untracked
# files count, because they are in the build context. No escape hatch: every fleet repo builds and PUSHES,
# and a stack runs what was pushed, so commit first. `make test-build` is for the TEST suite, not the stack.
# v14: a FAILED `git status` refuses. v13 tested `[ -n "$$(git status --porcelain 2>/dev/null)" ]`,
# and in a checkout git does not trust (owned by another user, no `safe.directory`) git exits 128 and
# prints nothing, which read as a clean tree: the dirty tree was built, labelled HEAD and pushed.
define refuse_dirty_tree
set -e; s=$$(git status --porcelain) || { \
  echo "REFUSING: git status failed, so nothing proves the working tree is clean (see git's error above)"; exit 1; }; \
if [ -n "$$s" ]; then \
  echo "REFUSING: the working tree is dirty, so sha-$(COMMIT) would not describe these bits:"; \
  printf '%s\n' "$$s" | sed 's/^/    /'; \
  echo "Commit first, then deploy what you pushed (make dev-deploy). make test-build is for the TEST suite only; it cannot run the stack."; \
  exit 1; \
fi
endef

# Refuse $(1) when the registry already holds it; $(2) says what to do instead. Fails CLOSED:
# `manifest inspect` exits 1 for "no such manifest" AND for an unreachable registry, so only the
# registry's own not-found answer reads as unpublished (a DNS blip must not let a re-push through).
define refuse_published
if docker manifest inspect $(1) >/dev/null 2>&1; then \
  echo "REFUSING: $(1) is already PUBLISHED. It is immutable: something pins it."; \
  echo "$(2)"; \
  exit 1; \
fi; \
out=$$(docker manifest inspect $(1) 2>&1) || case "$$out" in \
  *[Nn]"o such manifest"*|*"manifest unknown"*) ;; \
  *) echo "REFUSING: cannot verify $(1) is unpublished: $$out"; exit 1 ;; \
esac
endef

# The candidate gate, used by every target that pushes: scan the exact bits just built, mount-only,
# and refuse the push on any fixable HIGH/CRITICAL (luxaudit >= 0.13.0 in LUXAUDIT_IMAGE or LUXAUDIT).
# `make audit`'s image leg reads what the registry already holds, so it can only clear AFTER a release;
# as the gate it deadlocked. This is the gate. (v5)
# v14: `/repo` is `$(CURDIR)`, this makefile's directory, never `$(PWD)`. luxaudit reads the repo's
# `.luxaudit.toml` from `/repo`, and `$(PWD)` is the CALLER's shell directory: `make -C <repo> release`
# run from another repo scanned with THAT repo's `[[ignore]]` list and passed what this one refuses.
define scan_candidate
set -e; ref='$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))'; \
if [ -z "$$ref" ]; then echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the candidate must be scanned before it is pushed"; exit 2; fi; \
T=$$(mktemp); trap 'rm -f "$$T"' EXIT INT TERM; \
docker save $(1) -o "$$T"; chmod 644 "$$T"; \
docker run --rm -v "$(CURDIR)":/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro \
  "$$ref" --image-archive /candidate.tar --image-label $(1)
endef

# v14: promote a PUSHED image by the digest that was scanned, never by its tag. A tag can move
# between the scan and the promotion (another push of the same name), and `imagetools create` from the
# tag would then publish bits nothing scanned. So ONE shell reads the image's digest once, refuses an
# empty or malformed one, pulls and scans EACH platform by `<repo>@sha256:<digest>`, and creates the
# immutable tags from that same `<repo>@sha256:<digest>`. Any finding stops it before the promotion.
#   $(1) the pushed ref  $(2) its repository (no tag)  $(3) the `-t <tag>` list to create
# `pull --platform` fetches one platform's image (an arm64 image is pulled and saved on an x86 host,
# never run) and `docker save --platform` (Docker >= 28) writes exactly that one. The local copy is
# removed before each pull: the classic image store refuses a second platform under one digest.
# v16: in two halves, so every image is scanned before any is promoted: `promote_pin_scan` pins and
# scans ($(3) the shell variable holding the digest, `h` when empty), `promote_create` promotes.
# With more than one image each pins its own digest variable (`h_<name>`) and, once scanned, drops
# its local copy and its archive (the trap only cleans up after the last).
#   promote_pin_scan: $(1) the pushed ref  $(2) its repository (no tag)  $(3) the digest variable
#   promote_create:   $(1) the repository  $(2) the `-t <tag>` list to create  $(3) the digest variable
define promote_pin_scan
set -e; ref='$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))'; \
if [ -z "$$ref" ]; then echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the image must be scanned before it is promoted"; exit 2; fi; \
$(or $(3),h)=$$(docker buildx imagetools inspect $(1) --format '{{.Manifest.Digest.Encoded}}'); \
case "$$$(or $(3),h)" in ''|*[!0-9a-f]*) echo "REFUSING: could not read the digest of $(1); nothing is promoted"; exit 1 ;; esac; \
T=$$(mktemp); trap 'rm -f "$$T"; docker image rm $(2)@sha256:$$$(or $(3),h) >/dev/null 2>&1 || true' EXIT INT TERM; \
$(foreach p,$(or $(PLATFORM_LIST),host),docker image rm $(2)@sha256:$$$(or $(3),h) >/dev/null 2>&1 || true; docker pull -q $(if $(filter-out host,$(p)),--platform $(p) )$(2)@sha256:$$$(or $(3),h); docker save $(if $(filter-out host,$(p)),--platform $(p) )$(2)@sha256:$$$(or $(3),h) -o "$$T"; chmod 644 "$$T"; docker run --rm -v "$(CURDIR)":/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro "$$ref" --image-archive /candidate.tar --image-label '$(1) ($(p))'; )$(if $(3),rm -f "$$T"; docker image rm $(2)@sha256:$$$(3) >/dev/null 2>&1 || true; )
endef

define promote_create
docker buildx imagetools create $(2) $(1)@sha256:$$$(or $(3),h)
endef

# v11: every build names its output (`--load`). On the fleet's shared builder (`buildx-setup`, driver
# `docker-container`) a build with no output writes only to the build cache: it warns, exits 0, and
# the tag the next line scans, pushes or runs does not exist. `--load` is a no-op on the `docker` driver.
# Every commit's deployable: build, scan, push `:sha-<commit>` (what a dev/demo stack pins) and move the
# `:dev` alias. NEVER the version tag. v6: v5's `dev-deploy` ran `release`, which pushed `:$(VERSION)`
# on every run, so the first dev deploy after a version shipped overwrote that RELEASED tag with
# unreleased code (in one repo, prod's `:2026.09.0` silently became another
# commit's bits, caught by hand before the next prod pull).
# v14, PLATFORMS set: the candidate is pushed, its digest pinned, every platform scanned by that digest,
# then `:sha-<commit>` and the alias both point at that one scanned digest.
# v14: `:sha-<commit>` is published ONCE. v13 rebuilt and re-pushed it on every run, and no two builds
# are the same bytes (BUILD_TIMESTAMP, the runtime's package upgrade), so a second `dev-deploy` of one
# commit replaced the image a running stack pins, and one after a multi-arch publish replaced its
# scanned index with a single-arch rebuild. Already in the registry, it is deployed as it is.
# One image ($(1) its name, empty for the one image of IMAGES empty): build + scan, then push.
define publish_sha_build
docker build --load --target $(call img_target,$(1)) $(BUILD_ARGS) -f $(call img_dockerfile,$(1)) -t $(call img_sha,$(1)) $(call img_context,$(1)); \
$(call scan_candidate,$(call img_sha,$(1)))$(if $(1),; rm -f "$$T")
endef

define publish_sha_push
docker push $(call img_sha,$(1)); \
docker tag $(call img_sha,$(1)) $(call img_alias,$(1)); docker push $(call img_alias,$(1))
endef

define publish_sha_multi_build
docker buildx build --platform $(PLATFORMS) --push --target $(call img_target,$(1)) $(BUILD_ARGS) -f $(call img_dockerfile,$(1)) -t $(call img_candidate,$(1)) $(call img_context,$(1)); \
$(call promote_pin_scan,$(call img_candidate,$(1)),$(call img_base,$(1)),$(if $(1),h_$(1)))
endef

define publish_sha_multi_push
$(call promote_create,$(call img_base,$(1)),-t $(call img_sha,$(1)) -t $(call img_alias,$(1)),$(if $(1),h_$(1)))
endef

define publish_sha_single
$(call publish_sha_build,); \
$(call publish_sha_push,)
endef

define publish_sha_multi
$(call publish_sha_multi_build,)\
$(call publish_sha_multi_push,)
endef

# IMAGES empty: the one image, and already published it is deployed as it is.
define publish_sha_one
if docker manifest inspect $(SHA_IMAGE) >/dev/null 2>&1; then \
  echo "$(SHA_IMAGE) is already published: one commit is one image, deployed as it is (not rebuilt)"; \
  exit 0; \
fi; \
out=$$(docker manifest inspect $(SHA_IMAGE) 2>&1) || case "$$out" in \
  *[Nn]"o such manifest"*|*"manifest unknown"*) ;; \
  *) echo "REFUSING: cannot verify $(SHA_IMAGE) is unpublished: $$out"; exit 1 ;; \
esac; \
$(if $(PLATFORM_LIST),$(publish_sha_multi),$(publish_sha_single))
endef

# v16, IMAGES set: an image whose `:sha-<commit>` is already published is skipped and deployed as it
# is (a run interrupted between two pushes resumes, never re-pushes). Every other image is built and
# scanned, and only when every scan has passed is any of them pushed.
define publish_sha_todo
if docker manifest inspect $(call img_sha,$(1)) >/dev/null 2>&1; then \
  echo "$(call img_sha,$(1)) is already published: one commit is one image, deployed as it is (not rebuilt)"; \
else \
  out=$$(docker manifest inspect $(call img_sha,$(1)) 2>&1) || case "$$out" in \
    *[Nn]"o such manifest"*|*"manifest unknown"*) ;; \
    *) echo "REFUSING: cannot verify $(call img_sha,$(1)) is unpublished: $$out"; exit 1 ;; \
  esac; \
  todo="$$todo $(1)"; \
fi
endef

image_block_todo = case " $$todo " in *" $(1) "*) $(call $(2),$(1)) ;; esac
publish_sha_each_build = $(call image_block_todo,$(1),$(if $(PLATFORM_LIST),publish_sha_multi_build,publish_sha_build))
publish_sha_each_push = $(call image_block_todo,$(1),$(if $(PLATFORM_LIST),publish_sha_multi_push,publish_sha_push))

define publish_sha_images
todo=; \
$(call image_block_each,publish_sha_todo,;); \
if [ -z "$$todo" ]; then echo "every image of sha-$(COMMIT) is already published: deployed as they are"; exit 0; fi; \
$(call image_block_each,publish_sha_each_build,;); \
$(call image_block_each,publish_sha_each_push,;)
endef

publish-sha: $(if $(PLATFORM_LIST),buildx-setup) ## Build + scan + PUSH this commit as :sha-<commit> (and move the :dev alias) — never the version
	@set -e; $(require_registry); $(require_images)$(refuse_dirty_tree); $(require_scanner); \
	$(if $(IMAGE_LIST),$(publish_sha_images),$(publish_sha_one)); \
	echo "deploy it with:  make dev-deploy   (or: make dev-pin TAG=sha-$(COMMIT))"

# The GitHub Release: this version's user-facing notes on the repo's /releases page (--doc
# FLEET-RELEASE-PROCESS §9, `repo.github_release_wired`). v8: v1-v7 had no step for it, so an exact copy
# of this DO-NOT-EDIT block was red on that rule and the only ways to green were editing the block or a
# target outside `release` that nothing runs. `release` now runs it last, and checks everything it needs
# BEFORE the first push, against the REMOTE: `gh release create --verify-tag` needs the tag on GitHub, so
# a tag that exists only locally would let the image push and the Release fail. If the Release step
# still fails, re-run `make gh-release` alone: it is idempotent, and it needs the pushed tag, not HEAD
# (`release` refuses an already-released version, and HEAD moves on after the release commit).
# RELEASE_NOTES is found, not assumed: `<app>/release_notes/$(VERSION).md` wherever the app lives, up to
# three directories down (`app/`, `src/<pkg>/`, `apps/backend/app/`). Set it above this block only when
# more than one app ships notes.
RELEASE_NOTES ?= $(wildcard release_notes/$(VERSION).md */release_notes/$(VERSION).md */*/release_notes/$(VERSION).md */*/*/release_notes/$(VERSION).md)

define gh_release_preflight
command -v gh >/dev/null 2>&1 || { echo "REFUSING: the GitHub CLI (gh) is not installed; the release publishes its notes with it"; exit 1; }; \
gh auth status >/dev/null 2>&1 || { echo "REFUSING: gh is not authenticated (gh auth login)"; exit 1; }; \
set -- $(RELEASE_NOTES); if [ $$# -ne 1 ]; then \
  echo "REFUSING: need exactly ONE release notes file for $(VERSION), found $$#: $(or $(RELEASE_NOTES),none)"; \
  echo "Write <app>/release_notes/$(VERSION).md (luxarch --emit release-notes-guide), or set RELEASE_NOTES above this block."; \
  exit 1; \
fi; \
git ls-remote --exit-code --tags origin "refs/tags/v$(VERSION)" >/dev/null 2>&1 || { \
  echo "REFUSING: v$(VERSION) is not on origin; the GitHub Release hangs off the pushed tag:"; \
  echo "    git tag -a v$(VERSION) -m v$(VERSION) && git push origin v$(VERSION)"; exit 1; }
endef

define gh_release_publish
if gh release view "v$(VERSION)" >/dev/null 2>&1; then \
  echo "GitHub Release v$(VERSION) already exists, skipped (a Release, like its tag, is immutable)"; \
else \
  gh release create "v$(VERSION)" --verify-tag --title "$(VERSION)" --notes-file $(RELEASE_NOTES); \
fi
endef

gh-release: ## Publish the GitHub Release for v$(VERSION) from its release notes (idempotent; `release` runs it)
	@set -e; $(gh_release_preflight); \
	$(gh_release_publish)

# Cut VERSION: the immutable release tag prod pins. Refuses when that version is ALREADY released, in
# the registry or as a git tag at another commit: a released version is never re-pushed, not even from
# its own commit (a rebuild is different bytes under a name prod already runs). Bump VERSION instead.
# v14: a release pushes `:$(VERSION)` and nothing else. v13 also re-pushed `:sha-<commit>` and the
# `:dev` alias, overwriting the sha a dev stack pins; those are publish-sha's.
# v14, PLATFORMS set: the same refusals first, then the candidate is pushed to REGISTRY, its digest
# pinned, every platform scanned by that digest, and `:$(VERSION)` created in RELEASE_REGISTRY from it.
# v16, IMAGES set: every image's `:$(VERSION)` is refused if published, then every image is built and
# scanned, and pushed only after the last scan passed (one `set -e` shell): a failing scan of any
# image pushes none, so a release is refused whole.
define release_build
docker build --load --target $(call img_target,$(1)) $(BUILD_ARGS) -f $(call img_dockerfile,$(1)) -t $(call img_version,$(1)) $(call img_context,$(1)); \
$(call scan_candidate,$(call img_version,$(1)))$(if $(1),; rm -f "$$T")
endef

define release_push
docker push $(call img_version,$(1))
endef

define release_multi_build
docker buildx build --platform $(PLATFORMS) --push --target $(call img_target,$(1)) $(BUILD_ARGS) -f $(call img_dockerfile,$(1)) -t $(call img_candidate,$(1)) $(call img_context,$(1)); \
$(call promote_pin_scan,$(call img_candidate,$(1)),$(call img_base,$(1)),$(if $(1),h_$(1)))
endef

define release_multi_push
$(call promote_create,$(call img_base,$(1)),-t $(call img_version,$(1)),$(if $(1),h_$(1)))
endef

define release_single
$(call image_block_each,release_build,;); \
$(call image_block_each,release_push,;)
endef

define release_multi
$(call image_block_each,release_multi_build)\
$(call image_block_each,release_multi_push,;)
endef

define release_refuse
$(call refuse_published,$(call img_version,$(1)),Bump VERSION for a new release; deploy this commit to dev with make dev-deploy. If its GitHub Release is missing: make gh-release)
endef

release: $(if $(PLATFORM_LIST),buildx-setup) ## Cut VERSION: build + scan + PUSH :$(VERSION), then the GitHub Release (refuses an already-released VERSION)
	@set -e; $(require_registry); $(require_images)$(refuse_dirty_tree); $(require_scanner); \
	$(call image_block_each,release_refuse,;); \
	t=$$(git rev-parse -q --verify "refs/tags/v$(VERSION)^{commit}" 2>/dev/null || true); \
	if [ -n "$$t" ] && [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is already tagged at $$t, not HEAD: bump VERSION."; exit 1; \
	fi; \
	if [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is not tagged at HEAD. Tag and push it first:"; \
	  echo "    git tag -a v$(VERSION) -m v$(VERSION) && git push origin v$(VERSION)"; exit 1; \
	fi; \
	$(gh_release_preflight); \
	$(if $(PLATFORM_LIST),$(release_multi),$(release_single)); \
	$(gh_release_publish); \
	echo "released $(call image_block_each,img_version); pin prod to TAG=$(VERSION)"

# v14: a PUBLIC copy of the release, for an image other people pull (a public collector on GHCR). Set
# `PUBLIC_REGISTRY := ghcr.io` above this block and the target exists; unset, there is none. It never
# builds: it pins the RELEASED `:$(VERSION)` by digest, rescans that digest on every platform (a CVE
# published since the release stops it) and copies it, so the public image is bit-identical to the
# one prod pins. Like `release` it refuses a version already public there, and only the registry's
# own not-found answer reads as unpublished.
PUBLIC_REGISTRY ?=
PUBLIC_IMAGE := $(PUBLIC_REGISTRY)/$(if $(IMAGE_PRIMARY),$($(IMAGE_PRIMARY)_IMAGE_NAME),$(IMAGE_NAME)):$(VERSION)

# v16, IMAGES set: every image's checks first, every rescan next, the copies last.
define public_refuse
$(call refuse_published,$(call img_public,$(1)),A released version is immutable: bump VERSION.); \
docker manifest inspect $(call img_version,$(1)) >/dev/null 2>&1 || { \
  echo "REFUSING: $(call img_version,$(1)) is not released; run make release first (publish only what you released)"; exit 1; }
endef

define public_scan
$(call promote_pin_scan,$(call img_version,$(1)),$(call img_release,$(1)),$(if $(1),h_$(1)))
endef

define public_copy
$(call promote_create,$(call img_release,$(1)),-t $(call img_public,$(1)),$(if $(1),h_$(1)))
endef

public_said = $(call img_version,$(1)) as $(call img_public,$(1))

ifneq ($(strip $(PUBLIC_REGISTRY)),)
release-public: ## Copy the RELEASED :$(VERSION) to $(PUBLIC_REGISTRY) by digest after rescanning it (run `make release` first)
	@set -e; $(require_registry); $(require_images)$(require_scanner); \
	$(call image_block_each,public_refuse,;); \
	$(call image_block_each,public_scan)\
	$(call image_block_each,public_copy,;); \
	echo "published $(call image_block_each,public_said,$(IMAGE_BLOCK_COMMA)), the same digest"
endif

# ── Point an environment at a build ─────────────────────────────────────────────────────────────
# Immutable tags move a burden: a NEW tag per build has to actually reach the stack. This block used
# to end by telling a human to go hand-edit a gitignored file — advice beside a working button, and
# it is how a dev node ends up still serving last week's sha while everyone believes otherwise.
#
# The tag is PERSISTED into .env.<env>, not passed in the deploying shell, because the stack has to
# come back after a reboot: compose reads `${TAG:?}`, so a tag that lived only in one shell leaves a
# plain `docker compose up` on that node unable to start the stack at all. Exactly ONE line of that
# file is rewritten in place; nothing else is read, printed or reordered, because it holds secrets.
#
# `.env.prod` takes the same call from whatever `prod-deploy` does over SSH — the pinning is shared,
# the transport is node-specific and stays in the repo.

TAG ?= sha-$(COMMIT)

# Which compose profiles the dev stack runs. v8: v7 restarted with a bare `up -d`, and in a compose.yml
# where every service carries `profiles:` (the fleet's one-file model) that selects NO service unless
# something declares the stack: compose exits 0 and `dev-deploy` reported success over a stack it never
# restarted. Declare it ONE of two ways (`repo.compose_up_selects_services` checks either):
#   - COMPOSE_PROFILES=<profiles> in .env.dev, declared in the committed .env.example (leave this empty);
#   - or set the profiles above this block: `DEV_PROFILES := dev`. A `--profile` REPLACES the
#     env file's COMPOSE_PROFILES, so list every profile the dev stack needs.
# A service with no `profiles:` starts either way.
DEV_PROFILES ?=

# v12: `dev-pin` waits for the stack: running, and healthy where a service has a healthcheck. v11 returned when the
# containers STARTED, so `smoke` hit the proxy before the app listened: every first probe was a 502, and
# the forged-Host probe "passed" because nothing answered at all. Seconds to wait before failing the deploy:
DEV_WAIT_TIMEOUT ?= 120

# v15: a file with more than one `TAG=` line refuses. v14 rewrote the first, and compose reads the
# LAST, so the stack stayed on the old version under a pin that reported success.
# v14: one awk pass writes the file, and the rewrite is refused unless every line but `TAG=` is
# byte-for-byte what it was. v13 appended with `printf >>` after a `cp`, so a file whose last line had
# no newline came out as `SECRET_KEY=abcTAG=sha-…`: a corrupted secret and no TAG, past a line-count
# check the extra newline satisfied. awk ends every line it prints.
define pin_env_tag
f='$(1)'; t='$(2)'; \
[ -f "$$f" ] || { echo "$$f is missing — copy .env.example and fill it in first"; exit 1; }; \
[ "$$(grep -c '^[[:space:]]*TAG=' "$$f")" -le 1 ] || { echo "refusing: $$f has more than one TAG= line, and compose reads the last: keep one (nothing written)"; exit 1; }; \
tmp=$$(mktemp); trap 'rm -f "$$tmp"' EXIT; \
awk -v t="$$t" '/^[[:space:]]*TAG=/ && !d {print "TAG=" t; d=1; next} {print} END {if (!d) print "TAG=" t}' "$$f" > "$$tmp"; \
[ -s "$$tmp" ] || { echo "refusing to write an empty $$f"; exit 1; }; \
[ "$$(grep -v '^[[:space:]]*TAG=' "$$f")" = "$$(grep -v '^[[:space:]]*TAG=' "$$tmp")" ] || \
  { echo "refusing: rewriting $$f would change a line other than TAG= (nothing written)"; exit 1; }; \
grep -qxF "TAG=$$t" "$$tmp" || { echo "refusing: TAG=$$t did not land on a line of its own in $$f"; exit 1; }; \
cat "$$tmp" > "$$f"; \
echo "$$f: TAG=$$t"
endef

# v10: a deploy ends by probing what it deployed (`luxarch --emit smoke`, pasted below this block). A
# deploy that restarted the stack is not one that works: the suite runs in-process and cannot see the
# proxy, the server, the production image or a standalone entry point.
dev-deploy: ## Build+push THIS commit (:sha-…, never the version), pin .env.dev to it, restart the dev stack, smoke it
	@$(MAKE) --no-print-directory publish-sha
	@$(MAKE) --no-print-directory dev-pin TAG=sha-$(COMMIT)
	@$(MAKE) --no-print-directory smoke

# The rollback path, and the only one that does not build: name a tag you already published.
# The registry is checked FIRST because the alternative is the outage above — a stack pinned to a
# name the registry never held, discovered at the next restart when there was nothing to re-pull.
# v16: every image is checked before the one shared `TAG=` is written, once.
define dev_pin_check
docker manifest inspect $(call img_base,$(1)):$(TAG) >/dev/null 2>&1 || \
  { echo "$(call img_base,$(1)):$(TAG) is not in the registry — publish it before pinning a stack to it"; exit 1; }
endef

dev-pin: ## Point the dev stack at an ALREADY-PUBLISHED tag and restart it (rollback path)
	@set -e; $(require_registry); $(require_images)\
	$(call image_block_each,dev_pin_check,;); \
	$(call pin_env_tag,.env.dev,$(TAG)); \
	docker compose --env-file .env.dev $(foreach p,$(DEV_PROFILES),--profile $(p)) up -d --wait \
	  --wait-timeout $(DEV_WAIT_TIMEOUT)

# ── Verify: build from source, locally, every run ───────────────────────────────────────────────
# The test image must carry the SAME app layers production ships, plus the dev group. Two Dockerfile
# shapes do that, and either is canonical:
#   - `FROM production AS test`, putting back what production stripped (`python -m ensurepip`)
#     before `poetry install --with dev`;
#   - one shared `app` stage that both `test` and `production` build FROM, when production also
#     strips poetry. v3 said only "FROM production", which a production stage hardened per
#     FLEET-BUILD-DEPLOY-STANDARD (no pip/poetry in the runtime) cannot satisfy literally.
# Either way the Dockerfile needs a stage named `test`: this target builds it. v16, IMAGES set: the
# first image's, the one the suite runs in (TEST_IMAGE).

test-build: ## Build the LOCAL test image from source (never pushed, never a deploy tag)
	@docker build --load --target test $(BUILD_ARGS) -f $(call img_dockerfile,$(IMAGE_PRIMARY)) -t $(TEST_IMAGE) $(call img_context,$(IMAGE_PRIMARY)) >/dev/null

# Smoke settings: the collector serves no HTTP, so the probe is its container — it must run THIS
# build (the image's revision label) and stay healthy by its own HEALTHCHECK.
SMOKE_CONTAINERS ?= kidde-collector
SMOKE_STATIC_PATH ?=
# The collector's entry point is app.main, which the default `app/*_main.py` glob does not match.
SMOKE_ENTRY_MODULES ?= app.main

# luxarch:smoke asset v8 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit smoke`.
# ── Smoke: probe the DEPLOYED stack from outside, after every dev deploy ──────────────────────────
# Emitted by `luxarch --emit smoke`; paste below the image block. `make test` runs the app in-process
# against its test stack, so it cannot see the proxy, the server, the production image or an entry
# point other than the app's. One repo shipped four bugs under a green `make check` that only a probe
# of the deployed stack found: a rate limiter that took the host down, a proxy serving an unstyled UI,
# a standalone script that crashed on a circular import in the production image, and a Host-header
# defence no test exercised. Each probe below is aimed at one of them. `make dev-deploy` runs it
# (`repo.stack_smoke_wired`); a FAIL fails the deploy, and a probe that cannot run says NOT RUN and
# why, never a silent pass.
#
# Settings are `?=` defaults: override them above this block (or in Makefile.local for a host name).

# Setting: where the dev stack is reachable from the build host (e.g. https://dev.example.com) ---
SMOKE_URL ?=
# Setting: the deployed containers to probe on the docker host the deploy restarted. Each must run
# THIS build (its image's org.opencontainers.image.revision label names the commit) and reach
# `healthy` by its own HEALTHCHECK. For a stack that serves no HTTP (a collector, a poller) this is
# the probe, and SMOKE_URL stays empty; an HTTP stack may list its containers too (v5) ----------
SMOKE_CONTAINERS ?=
# Setting: how many seconds a container may stay `starting` before its health probe FAILs (v5) --
SMOKE_HEALTH_WAIT ?= 180
# Setting: how many seconds every container must STAY healthy, with no restart, after its first
# healthy sample; a HEALTHCHECK with a longer interval stretches it to one interval, so that check
# has run again (v6) ------------------------------------------------------------------------------
SMOKE_HEALTH_SETTLE ?= 30
# Setting: the health path; its response must name the build's commit (any field name) -------
SMOKE_HEALTH_PATH ?= /health
# Setting: a static asset the image serves (empty only if the app serves no static files) -----
SMOKE_STATIC_PATH ?= /static/css/app.css
# Setting: a path that needs authentication; the token comes from the SMOKE_AUTH_TOKEN env var -
SMOKE_AUTH_PATH ?=
# Setting: the package directory of standalone entry points, and the image's import root -------
SMOKE_SCRIPTS_DIR ?= app/scripts
SMOKE_IMPORT_ROOT ?= .
# Setting: entry-point MODULES outside that directory. The default is every `*_main` module at the
# app package's root, where the layout standard (fw.module_homes) puts a service's entry points
# (`app.portal_main`); list them yourself for any other place (v3) ---------------------------
SMOKE_ENTRY_MODULES ?= $(basename $(subst /,.,$(patsubst $(SMOKE_IMPORT_ROOT)/%,%,$(wildcard $(SMOKE_IMPORT_ROOT)/app/*_main.py))))
# Setting: the env file the dev stack runs with; each entry point is imported with it, because a
# module that builds the app's settings needs the config the deployed container has (v2). Its
# `TAG=` line names the build the deploy pinned, and that is the build every probe expects (v6) --
SMOKE_ENV_FILE ?= .env.dev
# Setting: extra `docker run` options for the entry-point imports (e.g. -e NAME=value) ----------
SMOKE_RUN_OPTS ?=
# Setting: extra curl options (e.g. --cacert <file> for a private CA) -------------------------
SMOKE_CURL_OPTS ?=

# v8: a forged-Host 502 also names the SNI cause: a front proxy that sends the client's Host as its
# upstream TLS SNI meets the catch-all's ssl_reject_handshake, so even `return 400` never answers.
# v8: the static-asset probe keeps its expected content type in its own variable. v7 reused `want`,
# the build the stack should run, so every SMOKE_CONTAINERS probe after it compared the revision with
# "text/css" and FAILed a stack that was running the right build.
# v7: git's own error is shown when the commit cannot be read. v6 discarded it, so a checkout owned by
# another user (git's "dubious ownership" refusal) printed only "git rev-parse failed". Trusting the
# checkout is the operator's call, made once with git's own remedy, never bypassed here: the image
# block's dirty-tree check refuses the same checkout for the same reason.
# v6: a forged Host that is answered names the defence to install (FLEET-BUILD-DEPLOY-STANDARD "Trusted hosts"):
# v5 said the defence was off and nothing said what it is, so each repo invented its own.
# v6: one healthy sample was enough, so a container that crashed and restarted right after it PASSed.
# A container now passes only when it is still healthy SMOKE_HEALTH_SETTLE seconds later (at least
# one interval of its HEALTHCHECK) with the same start time and restart count. The build expected is the one SMOKE_ENV_FILE's `TAG=` pins: v5
# compared against HEAD only, so a `make dev-pin TAG=<release>` rollback could never be verified.
# A forged-Host 502 behind a front proxy names its likely cause; a refusal is only credited when the
# same URL answers the real Host (a stack that answers nothing does not refuse anything).
# v5: a stack with no HTTP surface has nothing for the HTTP probes to ask, and v4 refused to run at all
# without SMOKE_URL. It is probed through its containers instead (SMOKE_CONTAINERS): the build each
# runs and the verdict of its own HEALTHCHECK, beside the entry-point imports.
# v4: a 502/503/504 on the forged-Host probe is the PROXY answering for an app that did not, not a refusal.
# v3 printed "PASS  forged Host refused (502)" while the stack was still starting, beside two FAILs that
# said the same thing; the probe can only pass on an answer from the app itself.
smoke: ## Probe the deployed dev stack: build commit, static asset, forged Host refused, auth, containers healthy, standalone entry points
	@set -u; fail=0; \
	[ -n "$(strip $(SMOKE_URL)$(SMOKE_CONTAINERS))" ] || { echo "REFUSING: set SMOKE_URL to the dev stack's address (Makefile.local), or SMOKE_CONTAINERS to its containers when it serves no HTTP"; exit 2; }; \
	probe() { curl -sS --max-time 15 $(SMOKE_CURL_OPTS) -o "$$B" -w '%{http_code} %{content_type}' "$$@" 2>/dev/null || echo "000 -"; }; \
	B=$$(mktemp); trap 'rm -f "$$B"' EXIT INT TERM; \
	sha=$$(git rev-parse --short=7 HEAD) || sha=; \
	pin=; if [ -n "$(SMOKE_ENV_FILE)" ] && [ -f "$(SMOKE_ENV_FILE)" ]; then pin=$$(sed -n 's/^[[:space:]]*TAG=//p' "$(SMOKE_ENV_FILE)" | tail -n 1 | tr -d "\"' \r"); fi; \
	want=$$sha; what="this commit"; img="$(SHA_IMAGE)"; \
	if [ -n "$$pin" ] && { [ -z "$$sha" ] || case "$$pin" in sha-"$$sha"*) false;; *) true;; esac; }; then \
	  img="$(IMAGE):$$pin"; what="the pinned TAG=$$pin"; want=; \
	  if ! docker image inspect "$$img" >/dev/null 2>&1 && ! docker pull -q "$$img" >/dev/null 2>&1; then echo "FAIL  $(SMOKE_ENV_FILE) pins TAG=$$pin, and $$img is neither here nor pullable, so the build the stack should run cannot be named"; fail=1; \
	  else want=$$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$$img" 2>/dev/null | cut -c1-7); \
	    if [ -z "$$want" ]; then echo "FAIL  $$img carries no org.opencontainers.image.revision label, so the build the stack should run cannot be named"; fail=1; \
	    else echo "NOTE  $(SMOKE_ENV_FILE) pins TAG=$$pin (revision $$want), not this checkout ($${sha:-unknown}): probing the pinned build"; fi; fi; \
	elif [ -z "$$sha" ]; then echo "FAIL  cannot read this checkout's commit (git's error is above; a checkout owned by another user needs git config --global --add safe.directory $(CURDIR)), so the deployed build cannot be checked"; fail=1; fi; \
	if [ -z "$(SMOKE_URL)" ]; then echo "NOT RUN  HTTP probes (health, static asset, forged Host, auth): SMOKE_URL is empty, so the stack is probed only through SMOKE_CONTAINERS (right only for a stack that serves no HTTP)"; \
	else \
	r=$$(probe "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); hcode=$${r%% *}; \
	if [ -z "$$want" ]; then :; \
	elif [ "$$hcode" = 200 ] && grep -q "$$want" "$$B"; then echo "PASS  health names $$what ($$want)"; \
	else echo "FAIL  $(SMOKE_HEALTH_PATH): $$r, and the response does not name $$want: the stack is not running $$what"; fail=1; fi; \
	if [ -n "$(SMOKE_STATIC_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_STATIC_PATH)"); ctype=$${r#* }; \
	  case "$(SMOKE_STATIC_PATH)" in *.css) mime=text/css;; *.js|*.mjs) mime=javascript;; *) mime=;; esac; \
	  if [ "$${r%% *}" = 200 ] && [ -s "$$B" ] && ! grep -qi '<html' "$$B" && { [ -z "$$mime" ] || case "$$ctype" in *"$$mime"*) true;; *) false;; esac; }; then echo "PASS  static asset served ($(SMOKE_STATIC_PATH), $$ctype)"; \
	  else echo "FAIL  $(SMOKE_STATIC_PATH): $$r: the proxy or image does not serve the built asset as $${mime:-a file} (a browser refuses a stylesheet or script with the wrong type)"; fail=1; fi; \
	else echo "NOT RUN  static asset: SMOKE_STATIC_PATH is empty (only right for an app that serves no static files)"; fi; \
	r=$$(probe -H "Host: smoke-forged.invalid" "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); \
	case "$${r%% *}" in 2??|3??) echo "FAIL  a forged Host header was answered ($$r): the trusted-host defence is not on in the real stack. The app must refuse it: luxarch --doc FLEET-BUILD-DEPLOY-STANDARD, \"Trusted hosts\" (luxarch --emit trusted-host)"; fail=1;; \
	  000) if [ "$$hcode" = 000 ]; then echo "FAIL  the forged-Host probe got no answer, and neither did $(SMOKE_HEALTH_PATH) with the real Host: nothing is answering at $(SMOKE_URL), so whether it refuses a forged Host is unknown"; fail=1; \
	    else echo "PASS  forged Host refused (connection rejected)"; fi;; \
	  502|503|504) if [ "$$hcode" = 200 ]; then echo "FAIL  the forged-Host probe got $${r%% *} while $(SMOKE_HEALTH_PATH) answers 200 through the same URL: a proxy in front of the stack reported a refusal it could not pass on (a closed connection: nginx's 444) as $${r%% *}, or the stack failed this request. Either way no refusal was seen. Behind another proxy, refuse with an answer: the app's trusted-host check answers 400 through any proxy, so let the forged Host reach it, or have the catch-all server return 400, not 444. If it already returns 400, the front proxy likely sends the client's Host as its upstream TLS SNI and the catch-all's ssl_reject_handshake drops the handshake: pin the proxy's upstream SNI to the real name, or drop ssl_reject_handshake behind a front proxy. Keep SMOKE_URL on the public address: FLEET-BUILD-DEPLOY-STANDARD, \"Trusted hosts\""; \
	    else echo "FAIL  the forged-Host probe got $${r%% *} from the proxy: the app did not answer, so whether it refuses a forged Host is unknown"; fi; fail=1;; \
	  *) echo "PASS  forged Host refused ($${r%% *})";; esac; \
	if [ -n "$(SMOKE_AUTH_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	  case "$${r%% *}" in 401|403) echo "PASS  $(SMOKE_AUTH_PATH) refuses a request with no credentials ($${r%% *})";; \
	    *) echo "FAIL  $(SMOKE_AUTH_PATH) answered a request with NO credentials ($$r): it is not protected"; fail=1;; esac; \
	  if [ -z "$${SMOKE_AUTH_TOKEN:-}" ]; then echo "FAIL  SMOKE_AUTH_PATH is set but SMOKE_AUTH_TOKEN is not in the environment"; fail=1; \
	  else r=$$(probe -H "Authorization: Bearer $${SMOKE_AUTH_TOKEN}" "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	    if [ "$${r%% *}" = 200 ]; then echo "PASS  authenticated request ($(SMOKE_AUTH_PATH))"; \
	    else echo "FAIL  authenticated $(SMOKE_AUTH_PATH): $$r"; fail=1; fi; fi; \
	else echo "NOT RUN  authenticated request: SMOKE_AUTH_PATH is empty"; fi; \
	fi; \
	settle=$(SMOKE_HEALTH_SETTLE); healthy=; \
	for c in $(SMOKE_CONTAINERS); do \
	  if ! rev=$$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$$c" 2>/dev/null); then echo "FAIL  container $$c is not on this docker host: the deploy did not bring it up"; fail=1; continue; fi; \
	  if [ -n "$$want" ]; then case "$$rev" in *"$$want"*) echo "PASS  $$c runs $$what ($$want)";; \
	    *) echo "FAIL  $$c runs revision '$$rev', not $$want: the stack is not running $$what (an image with no org.opencontainers.image.revision label cannot say which build it is)"; fail=1;; esac; fi; \
	  life=$$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' "$$c" 2>/dev/null || echo gone); \
	  t=0; while :; do \
	    st=$$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$$c" 2>/dev/null || echo "gone -"); \
	    case "$$st" in "running starting"|restarting*) [ "$$t" -lt $(SMOKE_HEALTH_WAIT) ] || break; sleep 5; t=$$((t + 5));; *) break;; esac; \
	  done; \
	  case "$$st" in "running healthy") \
	    iv=$$(docker inspect -f '{{if .Config.Healthcheck}}{{json .Config.Healthcheck.Interval}}{{end}}' "$$c" 2>/dev/null); \
	    case "$$iv" in ""|*[!0-9]*) iv=0;; esac; iv=$$((iv / 1000000000)); [ "$$iv" -gt 0 ] || iv=30; \
	    [ "$$iv" -le "$$settle" ] || settle=$$iv; healthy="$$healthy $$c=$$(printf '%s' "$$life" | tr ' ' '@')";; \
	    "running none") echo "FAIL  $$c declares no HEALTHCHECK, so nothing shows its process works: declare one in its Dockerfile or compose service"; fail=1;; \
	    "running starting") echo "FAIL  $$c is still starting after $(SMOKE_HEALTH_WAIT)s: its HEALTHCHECK never passed"; fail=1;; \
	    "running unhealthy") why=$$(docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' "$$c" 2>/dev/null | grep -v '^[[:space:]]*$$' | tail -n 1); echo "FAIL  $$c is unhealthy by its own HEALTHCHECK: $$why"; fail=1;; \
	    *) echo "FAIL  $$c is not running ($$st): the deployed process stopped or is crash-looping"; fail=1;; esac; \
	done; \
	if [ -n "$$healthy" ]; then sleep "$$settle"; for e in $$healthy; do \
	  c=$${e%%=*}; life=$$(printf '%s' "$${e#*=}" | tr '@' ' '); \
	  st=$$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$$c" 2>/dev/null || echo "gone -"); \
	  now=$$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' "$$c" 2>/dev/null || echo gone); \
	  if [ "$$now" != "$$life" ]; then echo "FAIL  $$c restarted during the probe (restart count and start time '$$life', then '$$now'): it crashed after its HEALTHCHECK passed"; fail=1; \
	  else case "$$st" in "running healthy") echo "PASS  $$c is healthy by its own HEALTHCHECK, and still is $${settle}s later with no restart";; \
	    "running unhealthy") why=$$(docker inspect -f '{{range .State.Health.Log}}{{.Output}}{{end}}' "$$c" 2>/dev/null | grep -v '^[[:space:]]*$$' | tail -n 1); echo "FAIL  $$c is unhealthy by its own HEALTHCHECK $${settle}s after it first passed: $$why"; fail=1;; \
	    *) echo "FAIL  $$c is not running ($$st) $${settle}s after its HEALTHCHECK passed: the deployed process stopped"; fail=1;; esac; fi; \
	done; fi; \
	n=0; mods=; \
	if [ -d "$(SMOKE_SCRIPTS_DIR)" ]; then for f in $$(find "$(SMOKE_SCRIPTS_DIR)" -name '*.py' ! -name '__init__.py' | sort); do \
	  rel=$$(realpath --relative-to="$(SMOKE_IMPORT_ROOT)" "$$f"); mods="$$mods $$(printf '%s' "$${rel%.py}" | tr / .)"; done; fi; \
	mods=$$(printf '%s\n' $$mods $(SMOKE_ENTRY_MODULES) | sort -u); \
	if [ -n "$$mods" ]; then \
	  envf=; [ -n "$(SMOKE_ENV_FILE)" ] && [ -f "$(SMOKE_ENV_FILE)" ] && envf="--env-file=$(SMOKE_ENV_FILE)"; \
	  if ! docker image inspect "$$img" >/dev/null 2>&1 && ! docker pull -q "$$img" >/dev/null 2>&1; then \
	    echo "FAIL  entry points: $$img is neither built here nor pullable, so no module could be imported (deploy this commit first)"; fail=1; n=-1; \
	  else for mod in $$mods; do \
	    n=$$((n + 1)); \
	    if err=$$(docker run --rm $$envf $(SMOKE_RUN_OPTS) --entrypoint python "$$img" -c "import $$mod" 2>&1 >/dev/null); then echo "PASS  $$mod imports on its own in the production image"; \
	    else fail=1; why=$$(printf '%s\n' "$$err" | grep -E '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit)\b' | tail -n 1); why=$${why:-$$(printf '%s\n' "$$err" | grep -v '^[[:space:]]*$$' | tail -n 1)}; \
	      case "$$err" in *ImportError*|*ModuleNotFoundError*|*"circular import"*) echo "FAIL  $$mod does not import on its own in $$img (a circular or missing import the app's own import order hides): $$why";; \
	        *) echo "FAIL  $$mod raised at import in $$img: $$why"; [ -n "$$envf" ] || echo "      no env file was passed ($(SMOKE_ENV_FILE) not found): set SMOKE_ENV_FILE to the file the stack runs with";; esac; fi; \
	  done; fi; \
	fi; \
	[ "$$n" -ne 0 ] || echo "NOT RUN  entry points: no module under $(SMOKE_SCRIPTS_DIR) and no SMOKE_ENTRY_MODULES"; \
	exit $$fail

docker-inspect: ## Inspect the released image's metadata
	@docker inspect $(VERSION_IMAGE) --format='Version: {{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || echo "Image not pulled"
	@docker inspect $(VERSION_IMAGE) --format='Built:   {{index .Config.Labels "org.opencontainers.image.created"}}' 2>/dev/null || true
	@docker inspect $(VERSION_IMAGE) --format='Commit:  {{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true

docker-clean: ## Remove local image tags (:sha-<commit>, :dev alias, :$(VERSION), test image)
	docker rmi $(SHA_IMAGE) $(DEV_ALIAS) $(VERSION_IMAGE) $(TEST_IMAGE) 2>/dev/null || true

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

##@ Prod — local stack (pulls the TAG= pinned in .env.prod)

prod-up: ## Pull the pinned TAG + start prod stack
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

prod-deploy: check-prod-node ## Pull the pinned TAG + recreate the collector on the node (run release first)
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

lint: ## luxlint (ruff + the canonical checks, mount-only). Needs LUXLINT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping lint"; \
	else docker run --rm -v $(CURDIR):/repo $(LUXLINT_IMAGE); fi

# The type leg is its OWN gate step (repo.makefile_canonical). Since luxlint 0.2x it is
# mount-only like ruff — the fleet's typed-dependency union is baked into the image, so
# there is no in-repo mypy tail to install and no second, drifting mypy pinned here.
mypy: ## mypy via luxlint (fleet stubs baked, mount-only). Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping mypy"; \
	else docker run --rm -v $(CURDIR):/repo $(LUXLINT_IMAGE) --mypy; fi

format: ## Apply the CANONICAL fixer in place: ruff check --fix + ruff format + markdown. Needs LUXLINT_REGISTRY.
	@if [ -z "$(LUXLINT_REGISTRY)" ]; then \
	  echo "luxlint: LUXLINT_REGISTRY unset (see Makefile.local.example) — skipping format"; \
	else docker run --rm --user $(REPO_UID):$(REPO_GID) -v $(CURDIR):/repo $(LUXLINT_IMAGE) --format; fi

# Test-block settings: the suite runs against a REAL InfluxDB (compose service `kidde_influxdb_test`,
# profile `test`), never a mock (repo.test_stack_parity). .env.e2e is committed and fills the
# `${TAG:?}`/`${ENV_FILE:?}` interpolation compose does for every service, even unstarted ones.
TEST_ENV_FILE := .env.e2e
TEST_SERVICES := kidde_influxdb_test
TEST_ENV := -e TEST_INFLUXDB_URL=http://kidde_influxdb_test:8086 -e TEST_INFLUXDB_TOKEN=kidde-test-token \
            -e TEST_INFLUXDB_ORG=kidde -e TEST_INFLUXDB_BUCKET=kidde

# luxarch:test-block asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-block`.
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
#     warnings are errors), mounted read-only. The source is mounted read-only and pytest writes no
#     cache into it.
#   - THE RUN'S FILES stay private (v2): the run's directory keeps mktemp's 700, so no other local
#     user can reach the config pytest loads (a config can load plugins, in a container holding the
#     repo and the suite's credentials). Inside it, `cfg/` is 711 and `out/` is 733: the image's
#     non-root user, whatever its uid, can open the config by name and write its coverage data, and
#     nothing more. Measured: the container reaches a bind mount without the host path above it, and
#     a narrower `out/` (711, 730, 755) fails coverage's write. v1 made the whole directory 777.
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
# A committed value narrows THE suite for everyone (`repo.test_block_wired` reds one). The ratchet
# still judges the whole floor, so a narrowed run measures only what it touched and fails it: a
# quick subset target of your own runs pytest without the ratchet, never through `make test` ----
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
	trap '$$dc down --volumes --remove-orphans >/dev/null 2>&1; rm -rf "$$D"' EXIT INT TERM; \
	mkdir "$$D/cfg" "$$D/out" && chmod 711 "$$D/cfg" && chmod 733 "$$D/out" || exit 2; \
	net=; if [ -n "$(TEST_SERVICES)" ]; then \
	  $$dc up -d --wait --wait-timeout 120 $(TEST_SERVICES) \
	    || { echo "FAIL  the test services did not become healthy: $(TEST_SERVICES)"; exit 1; }; \
	  cid=$$($$dc ps -q $(firstword $(TEST_SERVICES))); net=; \
	  for n in $$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}} {{end}}' $$cid); do \
	    [ "$$(docker network inspect -f '{{index .Labels "com.docker.compose.project"}}' $$n)" = "$$run" ] && { net=$$n; break; }; done; \
	  [ -n "$$net" ] || { echo "FAIL  $(firstword $(TEST_SERVICES)) joined no network of this run's own project ($$run)"; exit 1; }; \
	  net="--network $$net"; fi; \
	docker run --rm -v $(CURDIR):/repo $(LUXLINT) --emit-config pytest > "$$D/cfg/pytest.ini" || exit 2; \
	chmod 644 "$$D/cfg/pytest.ini"; \
	docker run --rm --init $$net $(TEST_ENV) -e PYTEST_ADDOPTS="$${PYTEST_ARGS:-}" \
	  -e COVERAGE_CORE=sysmon -e COVERAGE_FILE=/out/.coverage -e PYTHONDONTWRITEBYTECODE=1 \
	  -v $(CURDIR):/repo:ro -v "$$D/cfg":/cfg:ro -v "$$D/out":/out -w /repo/$(TEST_WORKDIR) $(TEST_IMAGE) \
	  sh -c 'python -m coverage run --branch --source=$(TEST_COV) -m pytest -c /cfg/pytest.ini --rootdir=. -p no:cacheprovider; s=$$?; python -m coverage report --show-missing > /out/coverage.txt; echo $$? > /out/coverage.rc; cat /out/coverage.txt; exit $$s'; \
	rc=$$?; \
	[ "$$rc" = 0 ] || { echo "FAIL  the suite failed (exit $$rc)"; exit 1; }; \
	[ "$$(cat "$$D/out/coverage.rc" 2>/dev/null)" = 0 ] || { echo "FAIL  coverage measured nothing (coverage report: $$(tail -n 1 "$$D/out/coverage.txt" 2>/dev/null)): check TEST_COV names the package the suite imports"; exit 1; }; \
	docker run --rm -i -v $(CURDIR):/repo $(LUXLINT) --coverage-ratchet < "$$D/out/coverage.txt" > "$$D/ratchet.txt"; crc=$$?; \
	sed -n '/coverage ratchet/,$$p' "$$D/ratchet.txt"; \
	[ "$$crc" = 0 ] || { echo "FAIL  the coverage ratchet failed (its verdict is above): add tests, never lower the floor"; exit 1; }

arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml). Needs LUXARCH_REGISTRY (Makefile.local).
	@if [ -z "$(LUXARCH_REGISTRY)" ]; then \
	  echo "luxarch: LUXARCH_REGISTRY unset (see Makefile.local.example) — skipping arch"; \
	else docker run --rm -v $(CURDIR):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION); fi

audit: ## Dependency-vuln scan via luxaudit (pinned; poetry.lock vs live OSV+PyPA feed). Needs LUXAUDIT_REGISTRY (Makefile.local).
	@if [ -z "$(LUXAUDIT_REGISTRY)" ]; then \
	  $(if $(filter $(PUBLISH_GOALS),$(MAKECMDGOALS)),echo "REFUSING: LUXAUDIT_REGISTRY unset (see Makefile.local.example) so the lock audit that gates a publish cannot run"; exit 1,echo "luxaudit: LUXAUDIT_REGISTRY unset (see Makefile.local.example) — skipping audit"); \
	else docker run --rm -v $(CURDIR):/repo $(LUXAUDIT_IMAGE); fi

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

# luxarch:guard-upgrade asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit guard-upgrade`.
# v2: the preview mounts `$(CURDIR)`, this makefile's directory (it edits ./Makefile there). v1 mounted
# `$(PWD)`, the caller's directory, so `make -C <repo> guard-upgrade` previewed ANOTHER repo's new reds.
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
	  [ "$$g" = luxarch ] && [ "$$old" != "$$latest" ] && docker run --rm -v "$(CURDIR)":/repo $(REGISTRY)/luxardolabs/luxarch:$$latest --new-rules --since $$old || true; \
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
	@docker run --rm -v $(CURDIR):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --assert-scans
	@docker run --rm -v $(CURDIR):/repo $(LUXLINT_IMAGE) --preflight

# THE fleet gate — byte-identical composition across every repo (repo.makefile_canonical).
# Order: pin drift -> honesty -> ruff -> types -> tests -> architecture -> dep CVEs -> secrets.
# It is a GATE, not a report: Make stops at the first failing step. For the full red board at
# once, run `make plan` (luxarch --plan) — reds phase-ordered and clustered by file.
check: guard-version-check honest lint mypy test arch audit gitleaks ## THE fleet gate — run before every commit

plan: ## The full luxarch red board at once — phase-ordered, clustered by file (the worklist)
	@if [ -z "$(LUXARCH_REGISTRY)" ]; then echo "luxarch: LUXARCH_REGISTRY unset — skipping"; exit 0; fi; \
	docker run --rm -v $(CURDIR):/repo $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --plan

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
	docker run --rm -v $(CURDIR):/repo $(LUXLINT_IMAGE) --json > "$$J" || true; $(STAMP) "$$J" .luxlint-status.json luxlint; \
	docker run --rm -v $(CURDIR):/repo -e LUXARCH_STATUS_WRITE=1 $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION) --json > "$$J" || true; $(STAMP) "$$J" .luxarch-status.json luxarch; \
	docker run --rm -v $(CURDIR):/repo $(LUXAUDIT_IMAGE) --json > "$$J" || true; $(STAMP) "$$J" .luxaudit-status.json luxaudit; \
	echo "wrote .lux*-status.json at $$SHA — commit them"

# The asset reads the luxlint image as $(LUXLINT); the gitleaks mirror lives beside it.
LUXLINT = $(LUXLINT_IMAGE)

# luxarch:gitleaks asset v14 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit gitleaks`.
# ── The privacy gate: EVERY surface a commit publishes ──────────────────────────────────────────
# Emitted by `luxarch --emit gitleaks`. Drop in verbatim.
#
# `gitleaks` scans DIFF CONTENT. A commit's author/committer address lives in the commit object
# HEADER and never appears in a patch, so no content rule can ever match it — it is a surface the
# scanner does not read. A repo reported `no leaks found` over 963 commits while 29 of them carried a
# personal address in both the author and committer fields, and it would have reported exactly the
# same thing after the scrub: identical output, opposite truth. Measured across the fleet, EIGHT
# repos carry a personal address in history and two of them are PUBLIC.
#
# v13: the commit MESSAGE is a third surface, and through v12 nothing read it. It is neither a patch
# nor the header, so a message naming an org-denylisted host passed the staged scan, the full-history
# scan and the push hook: seven commits in one public repo, found only when a file repeated the name.
# Every commit and annotated-tag message in history is now scanned with the same config as file
# content, and `gitleaks-commit-msg` scans the message being written (run by hooks/commit-msg).

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

# v13: `/repo` is `$(CURDIR)`, this makefile's directory. v1-v12 mounted `$(PWD)`, the CALLER's shell
# directory, so `make -C <repo> gitleaks` run from another repo scanned THAT repo's history with THAT
# repo's config while the identity pass read this one: two repos' answers, reported as this one's.
gitleaks: ## secret scan over FULL HISTORY + the commit-identity pass (the hooks cover commit/push)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v "$(CURDIR)":/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) git /repo -c /gl.toml --redact -v --ignore-gitleaks-allow
	@# v12: what `gitleaks git` never reads. It scans git's PATCHES, and git prints no patch for a file it
	@# treats as binary: a NUL byte, UTF-16 (a `Localizable.strings`), or a `binary` / `-diff` attribute.
	@# A token in any of them reached the remote with "no leaks found". So every path git ever showed as
	@# binary is re-read as text, NULs stripped, and scanned by path (the config's path allowlists hold).
	@set -e; C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
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
	@# v13: every commit and annotated-tag MESSAGE, scanned as a file with the same config (credential
	@# rules and the denylist). One `git log` and one `for-each-ref`, split on a header line carrying a
	@# per-run nonce, so a message cannot forge a header and steer its own lines out of the scan.
	@set -e; C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	N=$$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n'); [ $${#N} -eq 32 ]; \
	mkdir -p "$$D/t/commit-message" "$$D/t/tag-message" "$$D/none"; \
	git log --branches --tags HEAD --format="$$N commit-message/%H%n%B" > "$$D/raw"; \
	git for-each-ref refs/tags --format="$$N tag-message/%(objectname)%0a%(contents)" >> "$$D/raw"; \
	tr -d '\000' < "$$D/raw" > "$$D/msgs"; \
	LC_ALL=C awk -v n="$$N" -v d="$$D/t" \
	  '$$1 == n && NF == 2 { if (f != "") close(f); f = d "/" $$2; printf "" > f; next } \
	   f != "" { print > f }' "$$D/msgs"; \
	docker run --rm -v "$$D/t":/scan:ro -w /scan -v "$$D/none":/none:ro -v "$$C":/gl.toml:ro \
	  $(GITLEAKS_IMAGE) dir . -c /gl.toml --redact -v --ignore-gitleaks-allow \
	  --gitleaks-ignore-path /none || { \
	  echo "REFUSING: a commit or tag MESSAGE carries a finding (the File above names it:"; \
	  echo "  commit-message/<sha> or tag-message/<object>). A message is published with its commit"; \
	  echo "  forever. Reword it (git rebase -i, or git filter-repo --message-callback for older"; \
	  echo "  history), re-run, and ask the OWNER to approve the force-push. Never force-push unapproved."; \
	  exit 1; }
	@# v14: what a push publishes besides content and messages: every file PATH in history and every
	@# branch and tag NAME. A path is not a patch line, so `gitleaks git` never read one, and a file or
	@# a branch named for an internal host passed every gate into a public remote.
	@set -e; C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	mkdir -p "$$D/t" "$$D/none"; \
	git -c core.quotePath=false log --branches --tags HEAD --format= --name-only | sort -u > "$$D/t/paths"; \
	git for-each-ref --format='%(refname:short)' refs/heads refs/tags > "$$D/t/ref-names"; \
	docker run --rm -v "$$D/t":/scan:ro -w /scan -v "$$D/none":/none:ro -v "$$C":/gl.toml:ro \
	  $(GITLEAKS_IMAGE) dir . -c /gl.toml --redact -v --ignore-gitleaks-allow \
	  --gitleaks-ignore-path /none || { \
	  echo "REFUSING: a file PATH or a branch/tag NAME carries a finding (File: paths or ref-names above)."; \
	  echo "  A name is published with the history it is in. Rename it (git mv; git branch -m; for older"; \
	  echo "  history git filter-repo --path-rename), re-run, and ask the OWNER to approve the force-push."; \
	  exit 1; }
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
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
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

# v13: the message being WRITTEN, run by hooks/commit-msg (pre-commit cannot see it: git writes the
# message after that hook runs). The hook passes the file in `GITLEAKS_COMMIT_MSG`. The WHOLE file is
# scanned, comment lines included: `--cleanup=verbatim` publishes them, and a cut at the scissors line
# would let a verbatim message hide everything below one. No file to read is a refusal, never a pass.
gitleaks-commit-msg: ## secret + denylist scan of the commit MESSAGE being written (run by hooks/commit-msg)
	@set -e; M="$${GITLEAKS_COMMIT_MSG:-}"; \
	if [ -z "$$M" ] || [ ! -f "$$M" ] || [ ! -r "$$M" ]; then \
	  echo "REFUSING: no commit message to scan (GITLEAKS_COMMIT_MSG='$$M'). hooks/commit-msg" >&2; \
	  echo "  passes the file git hands it; re-emit the hooks with luxlint --emit-hooks." >&2; \
	  exit 1; \
	fi; \
	C=$$(mktemp); D=$$(mktemp -d); trap 'rm -rf "$$C" "$$D"' EXIT INT TERM; \
	docker run --rm -v "$(CURDIR)":/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	mkdir -p "$$D/t/commit-message" "$$D/none"; \
	tr -d '\000' < "$$M" > "$$D/t/commit-message/COMMIT_EDITMSG"; \
	docker run --rm -v "$$D/t":/scan:ro -w /scan -v "$$D/none":/none:ro -v "$$C":/gl.toml:ro \
	  $(GITLEAKS_IMAGE) dir . -c /gl.toml --redact -v --ignore-gitleaks-allow \
	  --gitleaks-ignore-path /none || { \
	  echo "REFUSING: this commit's MESSAGE carries a finding. A message is published with the" >&2; \
	  echo "  commit forever: reword it and commit again. Never --no-verify." >&2; \
	  exit 1; }

install-hooks: ## Activate the committed fleet secret hooks (core.hooksPath=hooks; pre-commit + pre-push)
	git config core.hooksPath hooks
	@echo "✓ secret hooks active (core.hooksPath=hooks: gitleaks-staged on commit, gitleaks on push)"

##@ Utilities

clean: ## Clean python/test caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ .coverage htmlcov/
	rm -f .luxlint.mypy.ini .ruff.local.toml

clean-all: clean docker-clean ## Clean caches + local docker image tags
