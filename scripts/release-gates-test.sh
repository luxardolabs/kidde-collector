#!/usr/bin/env bash
# Release-gate test: proves the publish targets REFUSE before any push in the cases an adversarial
# pass broke them, against a logging docker/gh stub, so nothing ever leaves this host.
#
#   scripts/release-gates-test.sh [<git ref>]     (default HEAD; driven by `make test-release-gates`)
#
# Each case runs in a fresh clone of <ref> and counts the push calls the stub saw. A control case
# must push (so a refusal is the gate's, not the harness's); every attack case must push nothing.
# Real docker is used only for the build-context case, which builds nothing from a registry.
set -u

REPO=$(cd "$(dirname "$0")/.." && pwd)
REF=${1:-HEAD}
REAL_DOCKER=$(command -v docker)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT INT TERM
mkdir -p "$T/bin" "$T/other"
fails=0

cat > "$T/bin/docker" <<'STUB'
#!/bin/sh
# stub docker: logs every call; nothing leaves this host
echo "docker $*" >> "$STUB_LOG"
case "$1 $2" in
  "manifest inspect") echo "no such manifest: $3" >&2; exit 1;;
  "buildx imagetools") case "$*" in *inspect*) echo "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";; esac; exit 0;;
  "buildx ls") echo "NAME"; echo "luxardo-builder"; exit 0;;
esac
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "gh $*" >> "$STUB_LOG"; case "$1 $2" in "release view") exit 1;; esac; exit 0
STUB
chmod +x "$T/bin/docker" "$T/bin/gh"

REG="LUXARCH_REGISTRY= LUXLINT_REGISTRY="

# The registries come from the clone's gitignored Makefile.local, as in a real checkout: the
# image block (v19) refuses a REGISTRY passed on the command line or from the shell.
fresh() {
  rm -rf "$T/repo"
  git clone -q "$REPO" "$T/repo" && git -C "$T/repo" checkout -q "$REF" || exit 2
  printf 'REGISTRY := fake.invalid:5000\nLUXAUDIT_REGISTRY := fake.invalid:5000\n' > "$T/repo/Makefile.local"
  : > "$T/stub.log"
}

# run <label> <make args...>: runs make in the clone with the stub first on PATH
run() {
  label=$1; shift
  (cd "$T/repo" && PATH="$T/bin:$PATH" STUB_LOG="$T/stub.log" make --no-print-directory "$@" > "$T/out.txt" 2>&1)
  rc=$?
  pushes=$(grep -cE 'buildx build .*--push|docker push|imagetools create' "$T/stub.log")
}

expect_pushed() {
  if [ "$pushes" -gt 0 ]; then echo "PASS  $label (control: pushed, $pushes call(s))"
  else echo "FAIL  $label: the control pushed nothing, so the harness proves nothing"; sed 's/^/      /' "$T/out.txt" | tail -5; fails=$((fails + 1)); fi
}

expect_refused() {
  want=$1
  if [ "$pushes" -eq 0 ] && [ "$rc" -ne 0 ] && grep -qF "$want" "$T/out.txt"; then echo "PASS  $label"
  else echo "FAIL  $label: rc=$rc, push calls=$pushes, expected a refusal naming '$want'"; sed 's/^/      /' "$T/out.txt" | tail -5; fails=$((fails + 1)); fi
}

echo "release gates at $(git -C "$REPO" rev-parse --short "$REF")"

fresh; run "clean tree publishes" publish-sha $REG; expect_pushed

fresh; printf 'KIDDE_COLLECTOR_KIDDE_PASSWORD=not-a-real-one\n' > "$T/repo/app/.env.local"
run "a gitignored file under app/ is refused" publish-sha $REG
expect_refused "gitignored files sit in the build context"
run "make -i cannot ignore a failing gate" -i publish-sha $REG
expect_refused "make -i ignores a failing release gate"
(export MAKEFLAGS=i; run "an inherited MAKEFLAGS=i cannot ignore a failing gate" publish-sha $REG
 expect_refused "make -i ignores a failing release gate"; exit $fails) || fails=$?

# .dockerignore: that same file never enters the build context (real docker, local output only).
mkdir -p "$T/ctx-out"
if printf 'FROM scratch\nCOPY app /app\n' | (cd "$T/repo" && "$REAL_DOCKER" build -q -f - --output "type=local,dest=$T/ctx-out" . >/dev/null 2>&1); then
  if [ -e "$T/ctx-out/app/.env.local" ]; then echo "FAIL  app/.env.local reached the build context"; fails=$((fails + 1))
  else echo "PASS  app/.env.local is excluded from the build context"; fi
else echo "NOT RUN  build-context check (docker build failed here)"; fi

# LUXAUDIT_REGISTRY= on the command line outranks one exported in the caller's shell.
fresh; printf 'REGISTRY := fake.invalid:5000\n' > "$T/repo/Makefile.local"
run "no audit registry refuses instead of skipping" publish-sha $REG LUXAUDIT_REGISTRY=
expect_refused "so the lock audit that gates a publish cannot run"

fresh; echo '# local edit' >> "$T/repo/app/core/config.py"; git -C "$T/repo" update-index --skip-worktree app/core/config.py
run "a skip-worktree edit is refused" publish-sha $REG
expect_refused "skip-worktree/assume-unchanged"

fresh; run "release-public without PUBLIC_REGISTRY is not a silent no-op" release-public $REG PUBLIC_REGISTRY=
if [ "$rc" -ne 0 ] && [ "$pushes" -eq 0 ]; then echo "PASS  $label"
else echo "FAIL  $label: rc=$rc, push calls=$pushes"; fails=$((fails + 1)); fi

fresh; (cd "$T/other" && PATH="$T/bin:$PATH" STUB_LOG="$T/stub.log" make --no-print-directory -C "$T/repo" audit $REG >/dev/null 2>&1)
if grep -qF -- "-v $T/repo:/repo" "$T/stub.log" && ! grep -qF -- "-v $T/other:/repo" "$T/stub.log"; then echo "PASS  make -C audits this repo, not the caller's directory"
else echo "FAIL  make -C audit mounted the caller's directory:"; grep -F 'luxaudit' "$T/stub.log" | sed 's/^/      /'; fails=$((fails + 1)); fi

fresh; label="BUILD_COMMIT is the commit the sha- tag names"
bc=$(cd "$T/repo" && make -n --no-print-directory build-local $REG 2>/dev/null | grep -oE 'BUILD_COMMIT=[^ ]+' | head -1 | cut -d= -f2)
tag=$(cd "$T/repo" && make --no-print-directory version $REG 2>/dev/null | grep -oE 'sha-[0-9a-z]+' | head -1 | cut -d- -f2)
if [ -n "$bc" ] && [ "$bc" = "$tag" ]; then echo "PASS  $label ($bc)"; else echo "FAIL  $label: BUILD_COMMIT=$bc, tag sha-$tag"; fails=$((fails + 1)); fi

if [ "$fails" -eq 0 ]; then echo "release gates: all passed"; else echo "release gates: $fails FAILED"; fi
exit $((fails > 0))
