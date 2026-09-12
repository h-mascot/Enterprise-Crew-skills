#!/usr/bin/env bash
set -euo pipefail

ENTITY_MC_SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITY_MC_WORKSPACE="$(cd "$ENTITY_MC_SKILL_DIR/../.." && pwd)"
# Canonical source scripts are bundled inside the skill dir itself.
# NEVER source from $ENTITY_MC_WORKSPACE/scripts/ — on remote agents those
# are wrapper stubs from a prior install, causing infinite exec loops.
ENTITY_MC_SOURCE_SCRIPTS_DIR="$ENTITY_MC_SKILL_DIR/source-scripts"
ENTITY_MC_SOURCE_CONTEXT_DIR="$ENTITY_MC_SKILL_DIR/context"
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
  ENTITY_MC_CONTEXT_DIR="$ENTITY_MC_STATE_DIR/context"
  ENTITY_MC_CURRENT_LINK="$ENTITY_MC_STATE_DIR/current"
  ENTITY_MC_MODE="${ENTITY_MC_MODE_OVERRIDE:-${ENTITY_MC_MODE:-copy}}"
  ENTITY_MC_INSTALL_CRON="${ENTITY_MC_INSTALL_CRON_OVERRIDE:-${ENTITY_MC_INSTALL_CRON:-true}}"
  ENTITY_MC_BASH_BIN="${ENTITY_MC_BASH_BIN:-bash}"
  ENTITY_MC_ENABLE_AUTO_PULL="${ENTITY_MC_ENABLE_AUTO_PULL:-true}"
  ENTITY_MC_ENABLE_REVIEW_PULL="${ENTITY_MC_ENABLE_REVIEW_PULL:-false}"
  ENTITY_MC_ENABLE_STALL_CHECK="${ENTITY_MC_ENABLE_STALL_CHECK:-true}"
  ENTITY_MC_ENABLE_INTAKE="${ENTITY_MC_ENABLE_INTAKE:-false}"
  ENTITY_MC_AUTO_PULL_SCHEDULE="${ENTITY_MC_AUTO_PULL_SCHEDULE:-*/30 * * * *}"
  ENTITY_MC_REVIEW_PULL_SCHEDULE="${ENTITY_MC_REVIEW_PULL_SCHEDULE:-*/15 * * * *}"
  ENTITY_MC_STALL_CHECK_SCHEDULE="${ENTITY_MC_STALL_CHECK_SCHEDULE:-0 */2 * * *}"
  ENTITY_MC_INTAKE_SCHEDULE="${ENTITY_MC_INTAKE_SCHEDULE:-*/15 * * * *}"
  ENTITY_MC_PROFILE_NAME="${ENTITY_MC_PROFILE_NAME:-}"
  ENTITY_MC_MC_URL="${ENTITY_MC_MC_URL:-http://localhost:3000}"
  ENTITY_MC_CRON_CWD="${ENTITY_MC_CRON_CWD:-$ENTITY_MC_TARGET_HOME}"
  ENTITY_MC_CRON_TAG="${ENTITY_MC_CRON_TAG:-ENTITY_MC:${ENTITY_MC_AGENT_NAME}}"
  ENTITY_MC_RELEASE_DIR="$ENTITY_MC_RELEASES_DIR/$ENTITY_MC_VERSION"
  ENTITY_MC_RELEASE_CONTEXT_DIR="$ENTITY_MC_RELEASE_DIR/context"
}

entity_mc_entrypoints() {
  printf '%s\n' \
    mc.sh \
    mc-auto-pull.sh \
    mc-review-pull.sh \
    mc-assign-model.sh \
    mc-build-context.sh \
    mc-stall-check.sh \
    mc-intake.sh \
    mc-health-check.sh
}

entity_mc_runtime_files() {
  entity_mc_entrypoints
  local module
  for module in "$ENTITY_MC_SOURCE_SCRIPTS_DIR"/*.py; do
    [[ -f "$module" ]] || continue
    basename "$module"
  done
}

entity_mc_release_inventory() {
  entity_mc_runtime_files
  while IFS= read -r file; do printf 'context/%s\n' "$file"; done <<< "$(entity_mc_context_files)"
  printf '%s\n' VERSION MANIFEST_PATH
}

entity_mc_context_files() {
  printf '%s\n' mc-operating-rules.md entity-mc-context.md mc-task-intake-policy.md mc-intake-setup.md task-closure-contract.md
}

entity_mc_validate_release_inventory() {
  local release_dir="$1" expected_file
  expected_file="$(mktemp)"
  entity_mc_release_inventory > "$expected_file"
  if ! "${ENTITY_MC_PYTHON_BIN:-python3}" -c '
import pathlib
import sys

release = pathlib.Path(sys.argv[1])
expected = set(pathlib.Path(sys.argv[2]).read_text().splitlines())
actual = {str(path.relative_to(release)) for path in release.rglob("*") if path.is_file()}
unexpected = sorted(actual - expected)
missing = sorted(expected - actual)
if unexpected:
    print(f"FATAL: release {release.name} contains unexpected file: {unexpected[0]}", file=sys.stderr)
    raise SystemExit(1)
if missing:
    print(f"FATAL: release {release.name} is missing expected file: {missing[0]}", file=sys.stderr)
    raise SystemExit(1)
' "$release_dir" "$expected_file"
  then
    rm -f "$expected_file"
    return 1
  fi
  rm -f "$expected_file"
}

entity_mc_validate_rollback_release() {
  local release_dir="$1"
  [[ -f "$release_dir/VERSION" ]] || {
    echo "FATAL: rollback release is missing VERSION" >&2
    return 1
  }
  [[ -f "$release_dir/mc.sh" ]] || { echo "FATAL: rollback release is missing mc.sh" >&2; return 1; }
}

entity_mc_log() {
  printf '[entity-mc] %s\n' "$*"
}

entity_mc_ensure_dirs() {
  mkdir -p "$ENTITY_MC_TARGET_SCRIPTS_DIR" "$ENTITY_MC_STATE_DIR" "$ENTITY_MC_RELEASES_DIR" "$ENTITY_MC_BACKUP_DIR"
}

entity_mc_stage_release() {
  local staged file runtime_files
  runtime_files="$(entity_mc_runtime_files)"
  if [[ -d "$ENTITY_MC_RELEASE_DIR" ]]; then
    entity_mc_validate_release_inventory "$ENTITY_MC_RELEASE_DIR" || return 1
    while IFS= read -r file; do
      if ! cmp -s "$ENTITY_MC_SOURCE_SCRIPTS_DIR/$file" "$ENTITY_MC_RELEASE_DIR/$file"; then
        echo "FATAL: release $ENTITY_MC_VERSION already exists with different content; bump VERSION." >&2
        return 1
      fi
    done <<< "$runtime_files"
    while IFS= read -r file; do
      if ! cmp -s "$ENTITY_MC_SOURCE_CONTEXT_DIR/$file" "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file"; then
        echo "FATAL: release $ENTITY_MC_VERSION already exists with different context; bump VERSION." >&2
        return 1
      fi
    done <<< "$(entity_mc_context_files)"
    return 0
  fi
  staged="$(mktemp -d "$ENTITY_MC_RELEASES_DIR/.staging.XXXXXX")"
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
    install -m 0755 "$ENTITY_MC_SOURCE_SCRIPTS_DIR/$file" "$staged/$file"
  done <<< "$runtime_files"
  mkdir -p "$staged/context"
  while IFS= read -r file; do install -m 0644 "$ENTITY_MC_SOURCE_CONTEXT_DIR/$file" "$staged/context/$file"; done <<< "$(entity_mc_context_files)"
  printf '%s\n' "$ENTITY_MC_VERSION" > "$staged/VERSION"
  printf '%s\n' "$ENTITY_MC_MANIFEST_PATH" > "$staged/MANIFEST_PATH"
  mv "$staged" "$ENTITY_MC_RELEASE_DIR"
}

entity_mc_snapshot_previous() {
  if [[ -L "$ENTITY_MC_CURRENT_LINK" || -d "$ENTITY_MC_RUNTIME_DIR" ]]; then
    local previous_target=""
    if [[ -L "$ENTITY_MC_CURRENT_LINK" ]]; then
      previous_target="$(readlink -f "$ENTITY_MC_CURRENT_LINK" 2>/dev/null || true)"
    elif [[ -d "$ENTITY_MC_RUNTIME_DIR" ]]; then
      previous_target="$ENTITY_MC_RUNTIME_DIR"
    fi
    if [[ -n "$previous_target" && -d "$previous_target" && "$previous_target" != "$ENTITY_MC_RELEASE_DIR" ]]; then
      printf '%s\n' "$previous_target" > "$ENTITY_MC_STATE_DIR/previous-release-path"
    fi
  fi
}

entity_mc_activate_release() {
  "${ENTITY_MC_PYTHON_BIN:-python3}" -c '
import os, sys
link, target = sys.argv[1:]
temporary = link + ".new-" + str(os.getpid())
os.symlink(target, temporary)
os.replace(temporary, link)
' "$ENTITY_MC_CURRENT_LINK" "$ENTITY_MC_RELEASE_DIR"
  # Preserve the old compatibility tree; existing wrappers follow runtime.
  if [[ -e "$ENTITY_MC_RUNTIME_DIR" || -L "$ENTITY_MC_RUNTIME_DIR" ]]; then
    local runtime_backup="$ENTITY_MC_BACKUP_DIR/runtime-$(date +%s)-$$"
    mv "$ENTITY_MC_RUNTIME_DIR" "$runtime_backup"
    if [[ -f "$ENTITY_MC_STATE_DIR/previous-release-path" ]] &&
       [[ "$(cat "$ENTITY_MC_STATE_DIR/previous-release-path")" == "$ENTITY_MC_RUNTIME_DIR" ]]; then
      printf '%s\n' "$runtime_backup" > "$ENTITY_MC_STATE_DIR/previous-release-path"
    fi
  fi
  ln -s "$ENTITY_MC_CURRENT_LINK" "$ENTITY_MC_RUNTIME_DIR"
  ENTITY_MC_RELEASE_CONTEXT_DIR="$ENTITY_MC_RELEASE_DIR/context"
  mkdir -p "$ENTITY_MC_CONTEXT_DIR"
  while IFS= read -r file; do
    if [[ -f "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file" ]]; then
      ln -sfn "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file" "$ENTITY_MC_CONTEXT_DIR/$file"
    else
      rm -f "$ENTITY_MC_CONTEXT_DIR/$file"
    fi
  done <<< "$(entity_mc_context_files)"
  printf '%s\n' "$ENTITY_MC_VERSION" > "$ENTITY_MC_STATE_DIR/current-version"
}

entity_mc_install_wrappers() {
  local entrypoints
  entrypoints="$(entity_mc_entrypoints)"
  while IFS= read -r file; do
    local target="$ENTITY_MC_TARGET_SCRIPTS_DIR/$file"
    if [[ ! -f "$ENTITY_MC_RELEASE_DIR/$file" ]]; then rm -f "$target"; continue; fi
    local wrapper_path="$target" staged_wrapper
    if [[ "$ENTITY_MC_MODE" == "symlink" ]]; then
      mkdir -p "$ENTITY_MC_STATE_DIR/launchers"
      wrapper_path="$ENTITY_MC_STATE_DIR/launchers/$file"
    fi
    staged_wrapper="$(mktemp "${wrapper_path}.new.XXXXXX")"
    entity_mc_render_wrapper "$file" > "$staged_wrapper"
    chmod 0755 "$staged_wrapper"
    mv -f "$staged_wrapper" "$wrapper_path"
    [[ "$ENTITY_MC_MODE" != "symlink" ]] || ln -sfn "$wrapper_path" "$target"
  done <<< "$entrypoints"
}

entity_mc_render_wrapper() {
  local file="$1" setting value
  printf '%s\n' '#!/usr/bin/env bash'
  printf 'export ENTITY_MC_AGENT_NAME=%q\n' "$ENTITY_MC_AGENT_NAME"
  printf 'export MC_USER="${MC_USER:-%s}"\n' "$ENTITY_MC_AGENT_NAME"
  printf 'export ENTITY_MC_TARGET_HOME=%q\n' "$ENTITY_MC_TARGET_HOME"
  printf 'export ENTITY_MC_TARGET_SCRIPTS_DIR=%q\n' "$ENTITY_MC_TARGET_SCRIPTS_DIR"
  printf 'export ENTITY_MC_STATE_DIR=%q\n' "$ENTITY_MC_STATE_DIR"
  printf 'export ENTITY_MC_MC_URL=%q\n' "$ENTITY_MC_MC_URL"
  printf 'export ENTITY_MC_RUNTIME=%q\n' "${ENTITY_MC_RUNTIME:-openclaw}"
  printf 'export ENTITY_MC_OPENCLAW_BIN=%q\n' "${ENTITY_MC_OPENCLAW_BIN:-}"
  printf 'export ENTITY_MC_HERMES_BIN=%q\n' "${ENTITY_MC_HERMES_BIN:-}"
  printf 'export ENTITY_MC_EXEC_LOG="${ENTITY_MC_EXEC_LOG:-%s/exec.log}"\n' "$ENTITY_MC_STATE_DIR"
  for setting in ENTITY_MC_DOCS_SOURCE_ID ENTITY_MC_REVIEW_EXEC_LOG ENTITY_MC_PYTHON_BIN ENTITY_MC_HEALTH_INVENTORY ENTITY_MC_HEALTH_NO_NOTIFY ENTITY_MC_MAX_ATTEMPTS ENTITY_MC_RETRY_BACKOFF_SECS ENTITY_MC_REVIEW_MAX_ATTEMPTS ENTITY_MC_REVIEW_BACKOFF_SECS ENTITY_MC_REVIEW_STARTUP_GRACE_SECS ENTITY_MC_REVIEW_MAX_RUNTIME_SECS ENTITY_MC_REVIEW_PULL_LIMIT ENTITY_MC_DISPATCH_HOST ENTITY_MC_DEFAULT_REVIEWER ENTITY_MC_HUMAN_REVIEWERS ENTITY_MC_MODEL_CONFIG ENTITY_MC_DEFAULT_MODEL; do
    value="${!setting:-}"
    [[ -z "$value" ]] || printf 'export %s=%q\n' "$setting" "$value"
  done
  [[ -z "${ENTITY_MC_EXEC_PATH:-}" ]] || printf 'export PATH=%q\n' "$ENTITY_MC_EXEC_PATH"
  [[ -z "${ENTITY_MC_HERMES_HOME:-}" ]] || printf 'export HERMES_HOME=%q\n' "$ENTITY_MC_HERMES_HOME"
  [[ -z "${ENTITY_MC_OPENCLAW_STATE_DIR:-}" ]] || printf 'export OPENCLAW_STATE_DIR=%q\n' "$ENTITY_MC_OPENCLAW_STATE_DIR"
  [[ -z "${ENTITY_MC_OPENCLAW_CONFIG_PATH:-}" ]] || printf 'export OPENCLAW_CONFIG_PATH=%q\n' "$ENTITY_MC_OPENCLAW_CONFIG_PATH"
  printf 'exec %q %q "$@"\n' "$ENTITY_MC_BASH_BIN" "$ENTITY_MC_CURRENT_LINK/$file"
}

entity_mc_install_memory() {
  local memory_dir="$ENTITY_MC_TARGET_HOME/memory/entity-mc" file
  mkdir -p "$memory_dir"
  while IFS= read -r file; do
    [[ ! -f "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file" ]] || install -m 0644 "$ENTITY_MC_RELEASE_CONTEXT_DIR/$file" "$memory_dir/$file"
  done <<< "$(entity_mc_context_files)"
}

entity_mc_patch_agents_md() {
  local agents="$ENTITY_MC_TARGET_HOME/AGENTS.md" marker='<!-- ENTITY_MC_MEMORY_START -->'
  [[ -f "$agents" ]] || : > "$agents"
  grep -q "$marker" "$agents" 2>/dev/null || printf '\n%s\nRead `memory/entity-mc/` for Mission Control operating rules.\n<!-- ENTITY_MC_MEMORY_END -->\n' "$marker" >> "$agents"
}

entity_mc_render_cron_block() {
  # Each installed launcher already carries the manifest environment.
  printf '# BEGIN %s\n' "$ENTITY_MC_CRON_TAG"
  if [[ "$ENTITY_MC_ENABLE_AUTO_PULL" == "true" ]]; then
    printf '%s cd %q && MC_USER=%q %q %q %q >> %q 2>&1\n' \
      "$ENTITY_MC_AUTO_PULL_SCHEDULE" \
      "$ENTITY_MC_CRON_CWD" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-auto-pull.sh" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_STATE_DIR/cron.log"
  fi
  if [[ "$ENTITY_MC_ENABLE_REVIEW_PULL" == "true" ]]; then
    printf '%s cd %q && MC_USER=%q %q %q %q >> %q 2>&1\n' \
      "$ENTITY_MC_REVIEW_PULL_SCHEDULE" \
      "$ENTITY_MC_CRON_CWD" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-review-pull.sh" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_STATE_DIR/cron.log"
  fi
  if [[ "$ENTITY_MC_ENABLE_STALL_CHECK" == "true" ]]; then
    printf '%s cd %q && ENTITY_MC_MC_URL=%q MC_USER=%q %q %q >> %q 2>&1\n' \
      "$ENTITY_MC_STALL_CHECK_SCHEDULE" \
      "$ENTITY_MC_CRON_CWD" \
      "$ENTITY_MC_MC_URL" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-stall-check.sh" \
      "$ENTITY_MC_STATE_DIR/cron.log"
  fi
  if [[ "$ENTITY_MC_ENABLE_INTAKE" == "true" ]]; then
    printf '%s cd %q && ENTITY_MC_MC_URL=%q MC_USER=%q %q %q scan-file %q >> %q 2>&1\n' \
      "$ENTITY_MC_INTAKE_SCHEDULE" \
      "$ENTITY_MC_CRON_CWD" \
      "$ENTITY_MC_MC_URL" \
      "$ENTITY_MC_AGENT_NAME" \
      "$ENTITY_MC_BASH_BIN" \
      "$ENTITY_MC_TARGET_SCRIPTS_DIR/mc-intake.sh" \
      "$ENTITY_MC_STATE_DIR/intake/inbox.jsonl" \
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
