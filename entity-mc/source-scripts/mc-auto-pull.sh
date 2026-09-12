#!/bin/bash
# Cron-safe entry: retain the explicit wrapper PATH; never source user dotfiles.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_SOURCE" ]; do
  SCRIPT_DIR=$(cd -P -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd)
  SCRIPT_SOURCE=$(readlink "$SCRIPT_SOURCE")
  case "$SCRIPT_SOURCE" in
    /*) ;;
    *) SCRIPT_SOURCE="$SCRIPT_DIR/$SCRIPT_SOURCE" ;;
  esac
done
SCRIPT_DIR=$(cd -P -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd)
exec "${ENTITY_MC_PYTHON_BIN:-python3}" -B "$SCRIPT_DIR/mc_auto_pull.py" "$@"
