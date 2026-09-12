#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-auto.sh [--workspace <path>] [--agent <name>] [--mc-url <url>] [--install-cron <true|false>]

Installs Entity MC into an OpenClaw-compatible workspace and writes the default
Entity MC cron block automatically.

Defaults:
  --workspace    current working directory
  --agent        basename of workspace, title-cased when possible
  --install-cron true

Examples:
  bash skills/entity-mc/install-auto.sh
  bash skills/entity-mc/install-auto.sh --agent MyAgent --workspace /path/to/workspace
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(pwd)"
AGENT=""
MC_URL="${ENTITY_MC_MC_URL:-}"
INSTALL_CRON="true"
MODE="copy"
AUTO_PULL_SCHEDULE="*/10 * * * *"
STALL_CHECK_SCHEDULE="0 */2 * * *"
ENABLE_INTAKE="false"
EXEC_PATH="${ENTITY_MC_EXEC_PATH:-$PATH}"
RUNTIME="${ENTITY_MC_RUNTIME:-openclaw}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      WORKSPACE="$2"
      shift 2
      ;;
    --agent)
      AGENT="$2"
      shift 2
      ;;
    --mc-url)
      MC_URL="$2"
      shift 2
      ;;
    --install-cron)
      INSTALL_CRON="$2"
      shift 2
      ;;
    --mode)
      MODE="$2"
      shift 2
      ;;
    --auto-pull-schedule)
      AUTO_PULL_SCHEDULE="$2"
      shift 2
      ;;
    --stall-check-schedule)
      STALL_CHECK_SCHEDULE="$2"
      shift 2
      ;;
    --enable-intake)
      ENABLE_INTAKE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# Cron must use the same executable search path as this installation shell.
case "$RUNTIME" in
  hermes) RUNTIME_COMMAND="${ENTITY_MC_HERMES_BIN:-hermes}"; RUNTIME_KEY=ENTITY_MC_HERMES_BIN ;;
  openclaw) RUNTIME_COMMAND="${ENTITY_MC_OPENCLAW_BIN:-openclaw}"; RUNTIME_KEY=ENTITY_MC_OPENCLAW_BIN ;;
  codex) RUNTIME_COMMAND="${ENTITY_MC_OPENCLAW_BIN:-}"; RUNTIME_KEY=ENTITY_MC_OPENCLAW_BIN ;;
  *) echo "Unsupported runtime: $RUNTIME" >&2; exit 1 ;;
esac
RUNTIME_BIN="$(PATH="$EXEC_PATH" command -v "$RUNTIME_COMMAND" 2>/dev/null || true)"
if [[ "$INSTALL_CRON" == "true" && ( -z "$RUNTIME_BIN" || ! -x "$RUNTIME_BIN" ) ]]; then
  echo "Cannot enable cron without an executable $RUNTIME runtime; configure $RUNTIME_KEY or install with --install-cron false." >&2
  exit 1
fi

WORKSPACE="$(mkdir -p "$WORKSPACE" && cd "$WORKSPACE" && pwd)"
SKILLS_DIR="$WORKSPACE/skills"
TARGET_SKILL_DIR="$SKILLS_DIR/entity-mc"

if [[ -z "$AGENT" ]]; then
  base="$(basename "$WORKSPACE")"
  case "$base" in
openclaw|openclaw|workspace) AGENT="Ada" ;;
openclaw-*|clawd-*) AGENT="${base#*-}" ;;
    *) AGENT="$base" ;;
  esac
  AGENT="$(python3 - "$AGENT" <<'PY'
import sys
s=sys.argv[1].replace('-', ' ').replace('_', ' ').strip()
print(s.title().replace(' ', '') or 'Agent')
PY
)"
fi

mkdir -p "$SKILLS_DIR" "$WORKSPACE/scripts" "$WORKSPACE/.entity-mc/intake"

# If this script is being run from a checkout outside the target workspace, copy
# the bundle into the workspace skills dir first. If it is already there, do not
# recursively copy itself into itself. rsync is preferred for clean updates.
if [[ "$(cd "$SCRIPT_DIR" && pwd)" != "$(cd "$TARGET_SKILL_DIR" 2>/dev/null && pwd || true)" ]]; then
  rm -rf "$TARGET_SKILL_DIR"
  mkdir -p "$TARGET_SKILL_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$SCRIPT_DIR/" "$TARGET_SKILL_DIR/"
  else
    cp -R "$SCRIPT_DIR/." "$TARGET_SKILL_DIR/"
  fi
fi

MANIFEST_DIR="$TARGET_SKILL_DIR/manifests"
MANIFEST="$MANIFEST_DIR/auto.env"
mkdir -p "$MANIFEST_DIR"
cat > "$MANIFEST" <<EOF
ENTITY_MC_AGENT_NAME="$AGENT"
ENTITY_MC_TARGET_HOME="$WORKSPACE"
ENTITY_MC_TARGET_SCRIPTS_DIR="$WORKSPACE/scripts"
ENTITY_MC_STATE_DIR="$WORKSPACE/.entity-mc"
ENTITY_MC_DISPATCH_HOST="$(hostname)"
ENTITY_MC_MODE="$MODE"
ENTITY_MC_INSTALL_CRON="$INSTALL_CRON"
ENTITY_MC_ENABLE_AUTO_PULL="true"
ENTITY_MC_ENABLE_STALL_CHECK="true"
ENTITY_MC_ENABLE_INTAKE="$ENABLE_INTAKE"
ENTITY_MC_AUTO_PULL_SCHEDULE="$AUTO_PULL_SCHEDULE"
ENTITY_MC_STALL_CHECK_SCHEDULE="$STALL_CHECK_SCHEDULE"
ENTITY_MC_PROFILE_NAME="$(printf '%s' "$AGENT" | tr '[:upper:]' '[:lower:]')"
EOF

{
  printf 'ENTITY_MC_EXEC_PATH=%q\n' "$EXEC_PATH"
  printf 'ENTITY_MC_RUNTIME=%q\n' "$RUNTIME"
  [[ -z "$RUNTIME_BIN" ]] || printf '%s=%q\n' "$RUNTIME_KEY" "$RUNTIME_BIN"
  for setting in ENTITY_MC_PYTHON_BIN:python3 ENTITY_MC_NODE_BIN:node ENTITY_MC_BASH_BIN:bash; do
    key="${setting%%:*}"
    binary="$(PATH="$EXEC_PATH" command -v "${!key:-${setting#*:}}" 2>/dev/null || true)"
    [[ -z "$binary" ]] || printf '%s=%q\n' "$key" "$binary"
  done
} >> "$MANIFEST"

if [[ -n "$MC_URL" ]]; then
  printf 'ENTITY_MC_MC_URL="%s"\n' "$MC_URL" >> "$MANIFEST"
fi

bash "$TARGET_SKILL_DIR/install.sh" --manifest "$MANIFEST" --install-cron "$INSTALL_CRON"
bash "$TARGET_SKILL_DIR/verify.sh" --manifest "$MANIFEST"

cat <<EOF
AUTO_INSTALL_OK
workspace=$WORKSPACE
agent=$AGENT
manifest=$MANIFEST
cron=$INSTALL_CRON
EOF
