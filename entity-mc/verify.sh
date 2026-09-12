#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

ENTITY_MC_MODE_OVERRIDE=""
ENTITY_MC_INSTALL_CRON_OVERRIDE=""
entity_mc_parse_common_args "$@"
entity_mc_load_manifest

fail() {
  echo "VERIFY_FAIL: $*" >&2
  exit 1
}

[[ -d "$ENTITY_MC_STATE_DIR" ]] || fail "state dir missing: $ENTITY_MC_STATE_DIR"
[[ -d "$ENTITY_MC_RUNTIME_DIR" ]] || fail "runtime dir missing: $ENTITY_MC_RUNTIME_DIR"
[[ -d "$ENTITY_MC_TARGET_SCRIPTS_DIR" ]] || fail "scripts dir missing: $ENTITY_MC_TARGET_SCRIPTS_DIR"
[[ -f "$ENTITY_MC_STATE_DIR/current-version" ]] || fail "current-version missing"
CURRENT_VERSION="$(cat "$ENTITY_MC_STATE_DIR/current-version")"
[[ "$CURRENT_VERSION" == "$ENTITY_MC_VERSION" ]] || fail "version mismatch: expected $ENTITY_MC_VERSION got $CURRENT_VERSION"

while IFS= read -r file; do
  [[ -x "$ENTITY_MC_RUNTIME_DIR/$file" ]] || fail "runtime file missing/executable: $file"
  cmp -s "$ENTITY_MC_SOURCE_SCRIPTS_DIR/$file" "$ENTITY_MC_RUNTIME_DIR/$file" || fail "runtime content mismatch: $file"
done < <(entity_mc_runtime_files)

while IFS= read -r file; do
  cmp -s "$ENTITY_MC_SOURCE_CONTEXT_DIR/$file" "$ENTITY_MC_RUNTIME_DIR/context/$file" || fail "context content mismatch: $file"
  cmp -s "$ENTITY_MC_SOURCE_CONTEXT_DIR/$file" "$ENTITY_MC_TARGET_HOME/memory/entity-mc/$file" || fail "installed memory mismatch: $file"
  [[ "$(readlink "$ENTITY_MC_CONTEXT_DIR/$file")" == "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file" ]] || fail "context link mismatch: $file"
done < <(entity_mc_context_files)

while IFS= read -r file; do
  WRAPPER="$ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
  [[ -x "$WRAPPER" ]] || fail "wrapper missing/executable: $WRAPPER"
  if [[ "$ENTITY_MC_MODE" == "symlink" ]]; then
    [[ -L "$WRAPPER" ]] || fail "wrapper is not a symlink: $WRAPPER"
    EXPECTED_WRAPPER="$ENTITY_MC_STATE_DIR/launchers/$file"
    [[ "$(readlink "$WRAPPER")" == "$EXPECTED_WRAPPER" ]] || fail "wrapper symlink target mismatch: $file"
    entity_mc_render_wrapper "$file" | cmp -s - "$EXPECTED_WRAPPER" || fail "launcher content mismatch: $file"
  else
    [[ ! -L "$WRAPPER" ]] || fail "copy wrapper is unexpectedly a symlink: $WRAPPER"
    EXPECTED_WRAPPER="$(mktemp)"
    entity_mc_render_wrapper "$file" > "$EXPECTED_WRAPPER"
    if ! cmp -s "$EXPECTED_WRAPPER" "$WRAPPER"; then
      rm -f "$EXPECTED_WRAPPER"
      fail "wrapper content mismatch: $file"
    fi
    rm -f "$EXPECTED_WRAPPER"
  fi
done < <(entity_mc_entrypoints)

if [[ "$ENTITY_MC_INSTALL_CRON" == "true" ]]; then
  CRON_CONTENT="$(crontab -l 2>/dev/null || true)"
  echo "$CRON_CONTENT" | grep -q "# BEGIN ${ENTITY_MC_CRON_TAG}" || fail "cron begin marker missing"
  echo "$CRON_CONTENT" | grep -q "# END ${ENTITY_MC_CRON_TAG}" || fail "cron end marker missing"
  BEGIN_COUNT="$(echo "$CRON_CONTENT" | grep -c "# BEGIN ${ENTITY_MC_CRON_TAG}" || true)"
  [[ "$BEGIN_COUNT" == "1" ]] || fail "cron block duplicated: $BEGIN_COUNT"
  BLOCK_CONTENT="$(printf '%s\n' "$CRON_CONTENT" | awk -v start="# BEGIN ${ENTITY_MC_CRON_TAG}" -v end="# END ${ENTITY_MC_CRON_TAG}" '
    $0==start {inside=1; next}
    $0==end {inside=0; exit}
    inside && $0 !~ /^[[:space:]]*#/ {print}
  ')"
  AUTO_PULL_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-auto-pull.sh' || true)"
  REVIEW_PULL_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-review-pull.sh' || true)"
  STALL_CHECK_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-stall-check.sh' || true)"
  INTAKE_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-intake.sh' || true)"
  EXPECTED_AUTO_PULL_COUNT="$([[ "$ENTITY_MC_ENABLE_AUTO_PULL" == "true" ]] && echo 1 || echo 0)"
  EXPECTED_REVIEW_PULL_COUNT="$([[ "$ENTITY_MC_ENABLE_REVIEW_PULL" == "true" ]] && echo 1 || echo 0)"
  EXPECTED_STALL_CHECK_COUNT="$([[ "$ENTITY_MC_ENABLE_STALL_CHECK" == "true" ]] && echo 1 || echo 0)"
  EXPECTED_INTAKE_COUNT="$([[ "$ENTITY_MC_ENABLE_INTAKE" == "true" ]] && echo 1 || echo 0)"
  [[ "$AUTO_PULL_COUNT" == "$EXPECTED_AUTO_PULL_COUNT" ]] || fail "auto-pull cron count mismatch: expected $EXPECTED_AUTO_PULL_COUNT got $AUTO_PULL_COUNT"
  [[ "$REVIEW_PULL_COUNT" == "$EXPECTED_REVIEW_PULL_COUNT" ]] || fail "review-pull cron count mismatch: expected $EXPECTED_REVIEW_PULL_COUNT got $REVIEW_PULL_COUNT"
  [[ "$STALL_CHECK_COUNT" == "$EXPECTED_STALL_CHECK_COUNT" ]] || fail "stall-check cron count mismatch: expected $EXPECTED_STALL_CHECK_COUNT got $STALL_CHECK_COUNT"
  [[ "$INTAKE_COUNT" == "$EXPECTED_INTAKE_COUNT" ]] || fail "intake cron count mismatch: expected $EXPECTED_INTAKE_COUNT got $INTAKE_COUNT"
fi

printf '%s\n' 'VERIFY_OK'
entity_mc_status_json
