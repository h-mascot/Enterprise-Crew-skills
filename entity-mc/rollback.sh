#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

ENTITY_MC_MODE_OVERRIDE=""
ENTITY_MC_INSTALL_CRON_OVERRIDE=""
entity_mc_parse_common_args "$@"
entity_mc_load_manifest

PREVIOUS_FILE="$ENTITY_MC_STATE_DIR/previous-release-path"
if [[ ! -f "$PREVIOUS_FILE" ]]; then
  echo "ROLLBACK_FAIL: no previous release metadata found" >&2
  exit 1
fi

PREVIOUS_RELEASE="$(cat "$PREVIOUS_FILE")"
if [[ ! -d "$PREVIOUS_RELEASE" ]]; then
  echo "ROLLBACK_FAIL: previous release missing: $PREVIOUS_RELEASE" >&2
  exit 1
fi
entity_mc_validate_rollback_release "$PREVIOUS_RELEASE" || {
  echo "ROLLBACK_FAIL: previous release is invalid" >&2
  exit 1
}

# Use the same atomic activation and environment-preserving wrappers as install.
ENTITY_MC_RELEASE_DIR="$PREVIOUS_RELEASE"
ENTITY_MC_VERSION="$(cat "$PREVIOUS_RELEASE/VERSION")"
entity_mc_ensure_dirs
entity_mc_snapshot_previous
entity_mc_activate_release
entity_mc_install_wrappers
entity_mc_install_memory
if [[ "$ENTITY_MC_INSTALL_CRON" == "true" ]]; then
  MISSING_ENTRIES="$(mktemp)"
  OLD_CRON="$(mktemp)"
  NEW_CRON="$(mktemp)"
  while IFS= read -r file; do
    [[ -f "$ENTITY_MC_RELEASE_DIR/$file" ]] || printf '%s\n' "$file" >> "$MISSING_ENTRIES"
  done < <(entity_mc_entrypoints)
  crontab -l > "$OLD_CRON" 2>/dev/null || true
  awk -v start="# BEGIN ${ENTITY_MC_CRON_TAG}" -v end="# END ${ENTITY_MC_CRON_TAG}" '
    FILENAME==ARGV[1] {missing[$0]=1; next}
    $0==start {inside=1}
    inside && $0 !~ /^[[:space:]]*#/ {
      for (file in missing) if (index($0,file)>0) {
        print "# ENTITY_MC_ROLLBACK_MISSING_ENTRYPOINT " $0
        next
      }
    }
    {print}
    $0==end {inside=0}
  ' "$MISSING_ENTRIES" "$OLD_CRON" > "$NEW_CRON"
  if ! cmp -s "$OLD_CRON" "$NEW_CRON"; then
    cp "$OLD_CRON" "$ENTITY_MC_BACKUP_DIR/crontab-before-rollback-$(date +%s)-$$"
    crontab "$NEW_CRON"
  fi
  rm -f "$MISSING_ENTRIES" "$OLD_CRON" "$NEW_CRON"
fi
PREVIOUS_VERSION="$ENTITY_MC_VERSION"

printf '%s\n' 'ROLLBACK_OK'
jq -n --arg agent "$ENTITY_MC_AGENT_NAME" --arg previous_release "$PREVIOUS_RELEASE" --arg version "$PREVIOUS_VERSION" '{agent:$agent, previous_release:$previous_release, version:$version}'
