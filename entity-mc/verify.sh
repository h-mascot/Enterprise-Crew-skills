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
  [[ -e "$ENTITY_MC_TARGET_SCRIPTS_DIR/$file" ]] || fail "wrapper missing: $ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
done < <(entity_mc_runtime_files)

if [[ "$ENTITY_MC_INSTALL_CRON" == "true" ]]; then
  CRON_CONTENT="$(crontab -l 2>/dev/null || true)"
  echo "$CRON_CONTENT" | grep -q "# BEGIN ${ENTITY_MC_CRON_TAG}" || fail "cron begin marker missing"
  echo "$CRON_CONTENT" | grep -q "# END ${ENTITY_MC_CRON_TAG}" || fail "cron end marker missing"
  BEGIN_COUNT="$(echo "$CRON_CONTENT" | grep -c "# BEGIN ${ENTITY_MC_CRON_TAG}" || true)"
  [[ "$BEGIN_COUNT" == "1" ]] || fail "cron block duplicated: $BEGIN_COUNT"
  BLOCK_CONTENT="$(printf '%s\n' "$CRON_CONTENT" | awk -v start="# BEGIN ${ENTITY_MC_CRON_TAG}" -v end="# END ${ENTITY_MC_CRON_TAG}" '
    $0==start {inside=1; next}
    $0==end {inside=0; exit}
    inside {print}
  ')"
  AUTO_PULL_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-auto-pull.sh' || true)"
  STALL_CHECK_COUNT="$(printf '%s\n' "$BLOCK_CONTENT" | grep -c 'mc-stall-check.sh' || true)"
  if [[ "$ENTITY_MC_ENABLE_AUTO_PULL" == "true" ]]; then
    [[ "$AUTO_PULL_COUNT" == "1" ]] || fail "expected 1 auto-pull cron entry, got $AUTO_PULL_COUNT"
  fi
  if [[ "$ENTITY_MC_ENABLE_STALL_CHECK" == "true" ]]; then
    [[ "$STALL_CHECK_COUNT" == "1" ]] || fail "expected 1 stall-check cron entry, got $STALL_CHECK_COUNT"
  fi
fi

cat <<EOF
VERIFY_OK
$(entity_mc_status_json)
EOF
