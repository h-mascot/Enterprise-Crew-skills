#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

ENTITY_MC_MODE_OVERRIDE=""
ENTITY_MC_INSTALL_CRON_OVERRIDE=""
entity_mc_parse_common_args "$@"
entity_mc_load_manifest
entity_mc_ensure_dirs
entity_mc_snapshot_previous
entity_mc_stage_release
entity_mc_activate_release
entity_mc_install_wrappers
entity_mc_install_cron_block

cat <<EOF
INSTALL_OK
$(entity_mc_status_json)
EOF
