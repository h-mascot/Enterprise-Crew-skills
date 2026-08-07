#!/usr/bin/env bash
# scrape-quota.sh — Scrape Kimi quota from console via Camofox browser
# Part of model-orchestrator. Outputs JSON with quota info.
# Requires: Camofox running on localhost:9377, authenticated session (operator/kimi-quota)
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-$(cat ~/clawd/secrets/camofox.env 2>/dev/null | grep CAMOFOX_API_KEY | cut -d= -f2)}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
SESSION_KEY="kimi-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/kimi-quota.json"
TMP_SNAPSHOT="/tmp/kimi-snapshot-$$.json"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cleanup() { rm -f "$TMP_SNAPSHOT"; }
trap cleanup EXIT

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$QUOTA_FILE"
  exit 1
}

# Check Camofox health
curl -sf --max-time 5 "$CAMOFOX_URL/health" -H "x-api-key: $API_KEY" >/dev/null 2>&1 || err "camofox_not_running"

# Create tab → console page
TAB_ID=$(curl -sf -X POST "$CAMOFOX_URL/tabs" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"url\":\"https://www.kimi.com/code/console\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['tabId'])" 2>/dev/null) || err "tab_create_failed"

sleep 5

# Get snapshot → save to temp file (avoids shell quoting issues)
curl -s --max-time 15 "${CAMOFOX_URL}/tabs/${TAB_ID}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
  -H "x-api-key: $API_KEY" -o "$TMP_SNAPSHOT" 2>/dev/null
[[ -s "$TMP_SNAPSHOT" ]] || { curl -s -X DELETE "${CAMOFOX_URL}/tabs/${TAB_ID}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" -H "x-api-key: $API_KEY" >/dev/null 2>&1; err "snapshot_failed"; }

# Close tab
curl -s -X DELETE "${CAMOFOX_URL}/tabs/${TAB_ID}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" -H "x-api-key: $API_KEY" >/dev/null 2>&1

# Parse quota from snapshot file
python3 << PYEOF
import json, sys, re

now_iso = "$NOW_ISO"
quota_file = "$QUOTA_FILE"
tmp_file = "$TMP_SNAPSHOT"

with open(tmp_file) as f:
    data = json.load(f)
snapshot = data.get("snapshot", "")

# Check if logged in
if "Log in" in snapshot and "Moonwalker" not in snapshot:
    result = {"error": "session_expired", "message": "Camofox Kimi session expired. Re-login needed (SMS).", "checked_at": now_iso}
    json.dump(result, open(quota_file, "w"), indent=2)
    print(json.dumps(result, indent=2))
    sys.exit(1)

# Parse: "100% Resets in 18 hours" patterns
found_pcts = []
plan = None

for line in snapshot.split("\n"):
    clean = line.strip("- ").strip()
    m = re.search(r'(\d+)%\s*Resets?\s+in\s+(.+?)$', clean)
    if m:
        found_pcts.append((int(m.group(1)), m.group(2).strip()))
    for p in ["Moderato", "Allegretto", "Allegro", "Vivace"]:
        if p in clean and plan is None:
            plan = p

weekly_usage = weekly_reset = rate_limit = rate_reset = None
if len(found_pcts) >= 1:
    weekly_usage, weekly_reset = found_pcts[0]
if len(found_pcts) >= 2:
    rate_limit, rate_reset = found_pcts[1]

# Parse reset hours
reset_hours = None
if weekly_reset:
    m = re.search(r'(\d+)\s*hour', weekly_reset)
    if m: reset_hours = int(m.group(1))
    m = re.search(r'(\d+)\s*day', weekly_reset)
    if m: reset_hours = int(m.group(1)) * 24
    m = re.search(r'(\d+)\s*minute', weekly_reset)
    if m: reset_hours = round(int(m.group(1)) / 60, 1)

status = "unknown"
if weekly_usage is not None:
    if weekly_usage >= 100: status = "exhausted"
    elif weekly_usage >= 80: status = "warning"
    elif weekly_usage >= 50: status = "moderate"
    else: status = "healthy"

result = {
    "provider": "kimi",
    "plan": plan,
    "weekly_usage_pct": weekly_usage,
    "weekly_reset_in": weekly_reset,
    "weekly_reset_hours": reset_hours,
    "rate_limit_pct": rate_limit,
    "rate_limit_reset_in": rate_reset,
    "status": status,
    "checked_at": now_iso
}

json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
