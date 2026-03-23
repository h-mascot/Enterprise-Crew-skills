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

entity_mc_ensure_dirs
ln -sfn "$PREVIOUS_RELEASE" "$ENTITY_MC_CURRENT_LINK"
rm -rf "$ENTITY_MC_RUNTIME_DIR"
mkdir -p "$ENTITY_MC_RUNTIME_DIR"
while IFS= read -r file; do
  ln -sfn "$PREVIOUS_RELEASE/$file" "$ENTITY_MC_RUNTIME_DIR/$file"
  if [[ "$ENTITY_MC_MODE" == "symlink" ]]; then
    ln -sfn "$ENTITY_MC_RUNTIME_DIR/$file" "$ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
  else
    cat > "$ENTITY_MC_TARGET_SCRIPTS_DIR/$file" <<EOF
#!/usr/bin/env bash
exec "$ENTITY_MC_RUNTIME_DIR/$file" "\$@"
EOF
    chmod 0755 "$ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
  fi
done < <(entity_mc_runtime_files)

PREVIOUS_VERSION="$(cat "$PREVIOUS_RELEASE/VERSION")"
printf '%s\n' "$PREVIOUS_VERSION" > "$ENTITY_MC_STATE_DIR/current-version"

cat <<EOF
ROLLBACK_OK
$(jq -n --arg agent "$ENTITY_MC_AGENT_NAME" --arg previous_release "$PREVIOUS_RELEASE" --arg version "$PREVIOUS_VERSION" '{agent:$agent, previous_release:$previous_release, version:$version}')
EOF
