#!/usr/bin/env bash
set -euo pipefail

ENTITY_MC_SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITY_MC_WORKSPACE="$(cd "$ENTITY_MC_SKILL_DIR/../.." && pwd)"
# Canonical source scripts are bundled inside the skill dir itself.
# NEVER source from $ENTITY_MC_WORKSPACE/scripts/ — on remote agents those
# are wrapper stubs from a prior install, causing infinite exec loops.
ENTITY_MC_SOURCE_SCRIPTS_DIR="$ENTITY_MC_SKILL_DIR/source-scripts"
ENTITY_MC_VERSION="$(cat "$ENTITY_MC_SKILL_DIR/VERSION")"
ENTITY_MC_MANIFEST_PATH=""

entity_mc_usage() {
  cat <<'EOF'
Usage:
  --manifest <path>         Manifest env file
  --mode <copy|symlink>     Install mode override
  --install-cron <bool>     Override cron install behavior
EOF
}

entity_mc_parse_common_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest)
        ENTITY_MC_MANIFEST_PATH="$2"
        shift 2
        ;;
      --mode)
        ENTITY_MC_MODE_OVERRIDE="$2"
        shift 2
        ;;
      --install-cron)
        ENTITY_MC_INSTALL_CRON_OVERRIDE="$2"
        shift 2
        ;;
      --help|-h)
        entity_mc_usage
        exit 0
        ;;
      *)
        echo "Unknown arg: $1" >&2
        entity_mc_usage >&2
        exit 1
        ;;
    esac
  done
}

entity_mc_load_manifest() {
  if [[ -z "${ENTITY_MC_MANIFEST_PATH:-}" ]]; then
    echo "Manifest required via --manifest" >&2
    exit 1
  fi

  if [[ ! -f "$ENTITY_MC_MANIFEST_PATH" ]]; then
    echo "Manifest not found: $ENTITY_MC_MANIFEST_PATH" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$ENTITY_MC_MANIFEST_PATH"

  : "${ENTITY_MC_AGENT_NAME:?ENTITY_MC_AGENT_NAME is required}"
  : "${ENTITY_MC_TARGET_HOME:?ENTITY_MC_TARGET_HOME is required}"

  ENTITY_MC_TARGET_SCRIPTS_DIR="${ENTITY_MC_TARGET_SCRIPTS_DIR:-$ENTITY_MC_TARGET_HOME/scripts}"
  ENTITY_MC_STATE_DIR="${ENTITY_MC_STATE_DIR:-$ENTITY_MC_TARGET_HOME/.entity-mc}"
  ENTITY_MC_RUNTIME_DIR="$ENTITY_MC_STATE_DIR/runtime"
  ENTITY_MC_RELEASES_DIR="$ENTITY_MC_STATE_DIR/releases"
  ENTITY_MC_BACKUP_DIR="$ENTITY_MC_STATE_DIR/backups"
  ENTITY_MC_CURRENT_LINK="$ENTITY_MC_STATE_DIR/current"
  ENTITY_MC_MODE="${ENTITY_MC_MODE_OVERRIDE:-${ENTITY_MC_MODE:-copy}}"
  ENTITY_MC_INSTALL_CRON="${ENTITY_MC_INSTALL_CRON_OVERRIDE:-${ENTITY_MC_INSTALL_CRON:-true}}"
  ENTITY_MC_BASH_BIN="${ENTITY_MC_BASH_BIN:-bash}"
  ENTITY_MC_ENABLE_AUTO_PULL="${ENTITY_MC_ENABLE_AUTO_PULL:-true}"
  ENTITY_MC_ENABLE_STALL_CHECK="${ENTITY_MC_ENABLE_STALL_CHECK:-true}"
  ENTITY_MC_AUTO_PULL_SCHEDULE="${ENTITY_MC_AUTO_PULL_SCHEDULE:-*/30 * * * *}"
  ENTITY_MC_STALL_CHECK_SCHEDULE="${ENTITY_MC_STALL_CHECK_SCHEDULE:-0 */2 * * *}"
  ENTITY_MC_PROFILE_NAME="${ENTITY_MC_PROFILE_NAME:-}"
  ENTITY_MC_MC_URL="${ENTITY_MC_MC_URL:-http://<REDACTED_IP>:<PORT>}"
  ENTITY_MC_CRON_TAG="${ENTITY_MC_CRON_TAG:-ENTITY_MC:${ENTITY_MC_AGENT_NAME}}"
  ENTITY_MC_RELEASE_DIR="$ENTITY_MC_RELEASES_DIR/$ENTITY_MC_VERSION"
}

entity_mc_runtime_files() {
  cat <<'EOF'
mc.sh
mc-auto-pull.sh
mc-assign-model.sh
mc-build-context.sh
mc-stall-check.sh
EOF
}

entity_mc_log() {
  printf '[entity-mc] %s\n' "$*"
}

entity_mc_ensure_dirs() {
  mkdir -p "$ENTITY_MC_TARGET_SCRIPTS_DIR" "$ENTITY_MC_STATE_DIR" "$ENTITY_MC_RELEASES_DIR" "$ENTITY_MC_BACKUP_DIR"
}

entity_mc_stage_release() {
  mkdir -p "$ENTITY_MC_RELEASE_DIR"
  # Guard: verify source scripts are real scripts, not wrapper stubs.
  # If the workspace scripts/ already contains wrappers (from a prior install on
  # the same machine), refuse to stage them — that creates an infinite exec loop.
  local _sample="$ENTITY_MC_SOURCE_SCRIPTS_DIR/mc-auto-pull.sh"
  if [[ -f "$_sample" ]] && head -3 "$_sample" | grep -q 'exec.*\.entity-mc/runtime'; then
    echo "FATAL: source scripts dir contains wrapper stubs, not real scripts." >&2
    echo "       $ENTITY_MC_SOURCE_SCRIPTS_DIR/mc-auto-pull.sh is a wrapper, not the canonical script." >&2
    echo "       Re-run from a workspace where scripts/ has the real MC scripts (e.g. <your-gateway> ~/agent-workspace)." >&2
    exit 1
  fi
  while IFS= read -r file; do
    install -m 0755 "$ENTITY_MC_SOURCE_SCRIPTS_DIR/$file" "$ENTITY_MC_RELEASE_DIR/$file"
  done < <(entity_mc_runtime_files)
  printf '%s\n' "$ENTITY_MC_VERSION" > "$ENTITY_MC_RELEASE_DIR/VERSION"
  printf '%s\n' "$ENTITY_MC_MANIFEST_PATH" > "$ENTITY_MC_RELEASE_DIR/MANIFEST_PATH"
}

entity_mc_snapshot_previous() {
  if [[ -L "$ENTITY_MC_CURRENT_LINK" || -d "$ENTITY_MC_RUNTIME_DIR" ]]; then
    local previous_target=""
    if [[ -L "$ENTITY_MC_CURRENT_LINK" ]]; then
      previous_target="$(readlink -f "$ENTITY_MC_CURRENT_LINK")"
    elif [[ -d "$ENTITY_MC_RUNTIME_DIR" ]]; then
      previous_target="$ENTITY_MC_RUNTIME_DIR"
    fi
    if [[ -n "$previous_target" && -d "$previous_target" ]]; then
      printf '%s\n' "$previous_target" > "$ENTITY_MC_STATE_DIR/previous-release-path"
    fi
  fi
}

entity_mc_activate_release() {
  ln -sfn "$ENTITY_MC_RELEASE_DIR" "$ENTITY_MC_CURRENT_LINK"
  rm -rf "$ENTITY_MC_RUNTIME_DIR"
  mkdir -p "$ENTITY_MC_RUNTIME_DIR"
  while IFS= read -r file; do
    ln -sfn "$ENTITY_MC_RELEASE_DIR/$file" "$ENTITY_MC_RUNTIME_DIR/$file"
  done < <(entity_mc_runtime_files)
  printf '%s\n' "$ENTITY_MC_VERSION" > "$ENTITY_MC_STATE_DIR/current-version"
}

entity_mc_install_wrappers() {
  while IFS= read -r file; do
    local target="$ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
    if [[ "$ENTITY_MC_MODE" == "symlink" ]]; then
      ln -sfn "$ENTITY_MC_RUNTIME_DIR/$file" "$target"
    else
      cat > "$target" <<EOF
#!/usr/bin/env bash
exec "$ENTITY_MC_BASH_BIN" "$ENTITY_MC_RUNTIME_DIR/$file" "\$@"
EOF
      chmod 0755 "$target"
    fi
  done < <(entity_mc_runtime_files)
}

entity_mc_render_cron_block() {
  # Build env prefix for runtime/binary overrides
  local _env_prefix=""
  [ -n "${ENTITY_MC_RUNTIME:-}" ] && _env_prefix="${_env_prefix}ENTITY_MC_RUNTIME=${ENTITY_MC_RUNTIME} "
  [ -n "${ENTITY_MC_OPENCLAW_BIN:-}" ] && _env_prefix="${_env_prefix}ENTITY_MC_OPENCLAW_BIN=${ENTITY_MC_OPENCLAW_BIN} "
  [ -n "${ENTITY_MC_HERMES_BIN:-}" ] && _env_prefix="${_env_prefix}ENTITY_MC_HERMES_BIN=${ENTITY_MC_HERMES_BIN} "
  [ -n "${ENTITY_MC_STATE_DIR:-}" ] && _env_prefix="${_env_prefix}ENTITY_MC_EXEC_LOG=${ENTITY_MC_STATE_DIR}/exec.log "

  printf '# BEGIN %s\n' "$ENTITY_MC_CRON_TAG"
  if [[ "$ENTITY_MC_ENABLE_AUTO_PULL" == "true" ]]; then
    printf '%s cd %q && %sMC_USER=%q %q %q %q >> %q 2>&1\n' \
      "$ENTITY_MC_AUTO_PULL_SCHEDULE" \
      "$ENTITY_MC_WORKSPACE" \
      "$_env_prefix" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-auto-pull.sh" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_STATE_DIR/cron.log"
  fi
  if [[ "$ENTITY_MC_ENABLE_STALL_CHECK" == "true" ]]; then
    printf '%s cd %q && MC_USER=%q %q %q >> %q 2>&1\n' \
      "$ENTITY_MC_STALL_CHECK_SCHEDULE" \
      "$ENTITY_MC_WORKSPACE" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-stall-check.sh" \
      "$ENTITY_MC_STATE_DIR/cron.log"
  fi
  printf '# END %s\n' "$ENTITY_MC_CRON_TAG"
}

entity_mc_install_cron_block() {
  [[ "$ENTITY_MC_INSTALL_CRON" == "true" ]] || return 0
  local tmp current
  tmp="$(mktemp)"
  current="$(mktemp)"
  crontab -l 2>/dev/null > "$current" || true
  awk -v start="# BEGIN ${ENTITY_MC_CRON_TAG}" -v end="# END ${ENTITY_MC_CRON_TAG}" '
    $0==start {skip=1; next}
    $0==end {skip=0; next}
    !skip {print}
  ' "$current" > "$tmp"
  printf '\n' >> "$tmp"
  entity_mc_render_cron_block >> "$tmp"
  crontab "$tmp"
  rm -f "$tmp" "$current"
}

entity_mc_remove_cron_block() {
  local tmp current
  tmp="$(mktemp)"
  current="$(mktemp)"
  crontab -l 2>/dev/null > "$current" || true
  awk -v start="# BEGIN ${ENTITY_MC_CRON_TAG}" -v end="# END ${ENTITY_MC_CRON_TAG}" '
    $0==start {skip=1; next}
    $0==end {skip=0; next}
    !skip {print}
  ' "$current" > "$tmp"
  crontab "$tmp"
  rm -f "$tmp" "$current"
}

entity_mc_status_json() {
  jq -n \
    --arg agent "$ENTITY_MC_AGENT_NAME" \
    --arg version "$ENTITY_MC_VERSION" \
    --arg mode "$ENTITY_MC_MODE" \
    --arg target_home "$ENTITY_MC_TARGET_HOME" \
    --arg scripts_dir "$ENTITY_MC_TARGET_SCRIPTS_DIR" \
    --arg state_dir "$ENTITY_MC_STATE_DIR" \
    --arg profile_name "$ENTITY_MC_PROFILE_NAME" \
    '{agent:$agent, version:$version, mode:$mode, target_home:$target_home, scripts_dir:$scripts_dir, state_dir:$state_dir, profile_name:$profile_name}'
}
