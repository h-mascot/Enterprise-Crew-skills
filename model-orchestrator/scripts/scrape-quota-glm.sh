#!/usr/bin/env bash
# scrape-quota-glm.sh — Scrape GLM/Z.ai quota from dashboard via Camofox browser
# Outputs JSON with 5h quota %, web search quota %, total tokens, plan info
# Requires: Camofox running on localhost:9377, authenticated session (operator/glm-quota)
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
SESSION_KEY="glm-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/glm-quota.json"
TMP_SNAPSHOT="/tmp/glm-snapshot-$$.txt"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GOOGLE_EMAIL="${GOOGLE_EMAIL:-}"
export NOW_ISO QUOTA_FILE TMP_SNAPSHOT

cleanup() { rm -f "$TMP_SNAPSHOT"; }
trap cleanup EXIT

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$QUOTA_FILE"
  exit 1
}

# Check Camofox health
curl -sf --max-time 5 "$CAMOFOX_URL/health" -H "x-api-key: $API_KEY" >/dev/null 2>&1 || err "camofox_not_running"

# Open Usage tab directly
TAB_ID=$(curl -sf -X POST "$CAMOFOX_URL/tabs" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"url\":\"https://z.ai/manage-apikey/subscription?tab=usage\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['tabId'])" 2>/dev/null) || err "tab_create_failed"

sleep 6

# Get snapshot
SNAPSHOT=$(curl -s --max-time 15 "${CAMOFOX_URL}/tabs/${TAB_ID}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
  -H "x-api-key: $API_KEY" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null)

# Close tab
curl -s -X DELETE "${CAMOFOX_URL}/tabs/${TAB_ID}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
  -H "x-api-key: $API_KEY" >/dev/null 2>&1

[[ -n "$SNAPSHOT" ]] || err "snapshot_empty"
echo "$SNAPSHOT" > "$TMP_SNAPSHOT"

# Parse quota from snapshot
python3 << 'PYEOF'
import json, sys, re, os

now_iso = os.environ.get("NOW_ISO", "")
quota_file = os.environ.get("QUOTA_FILE", "")
tmp_file = os.environ.get("TMP_SNAPSHOT", "")

if not now_iso:
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(tmp_file) as f:
    snapshot = f.read()

# Check if logged in (look for Login/Sign buttons or missing quota data)
if "Log in" in snapshot or "Sign in" in snapshot:
    if "Quota" not in snapshot and "Used" not in snapshot:
        result = {"error": "session_expired", "message": "Camofox GLM session expired. Re-login needed (Google OAuth).", "checked_at": now_iso}
        json.dump(result, open(quota_file, "w"), indent=2)
        print(json.dumps(result, indent=2))
        sys.exit(1)

# Parse 5h quota: "3 % Used" after "5 Hours Quota"
five_h_match = re.search(r'5 Hours Quota.*?(\d+)\s*%\s*Used', snapshot, re.DOTALL)
five_h_pct = int(five_h_match.group(1)) if five_h_match else None

# Parse web search quota: "0 % Used" after "Web Search"
web_match = re.search(r'Web Search.*?(\d+)\s*%\s*Used', snapshot, re.DOTALL)
web_pct = int(web_match.group(1)) if web_match else None

# Parse web search reset time
reset_match = re.search(r'Reset Time:\s*([\d-]+\s+[\d:]+)', snapshot)
web_reset = reset_match.group(1).strip() if reset_match else None

# Parse total tokens
tokens_match = re.search(r'Total Tokens\s+([\d,]+)', snapshot)
total_tokens = tokens_match.group(1) if tokens_match else None

# Parse last updated
updated_match = re.search(r'Last Updated:\s*([\d-]+\s+[\d:]+)', snapshot)
last_updated = updated_match.group(1).strip() if updated_match else None

# Parse plan name
plan_match = re.search(r'(GLM Coding \S+(?:-\S+)?)\s+Plan', snapshot)
plan = plan_match.group(1) if plan_match else "GLM Coding Max-Yearly"

# Parse auto-renew date
renew_match = re.search(r'Auto-renew on\s+([\d.]+)', snapshot)
auto_renew = renew_match.group(1).replace(".", "-") if renew_match else None

# Determine status
if five_h_pct is not None:
    if five_h_pct >= 95:
        status = "exhausted"
    elif five_h_pct >= 75:
        status = "warning"
    elif five_h_pct >= 50:
        status = "moderate"
    else:
        status = "healthy"
else:
    status = "unknown"

result = {
    "provider": "glm",
    "plan": plan + " Plan",
    "five_hour_quota_pct": five_h_pct,
    "five_hour_quota_status": status,
    "web_search_quota_pct": web_pct,
    "web_search_reset": web_reset,
    "total_tokens_7d": total_tokens,
    "last_updated": last_updated,
    "auto_renew": auto_renew,
    "login": "${GOOGLE_EMAIL:-} (Google OAuth)",
    "camofox_session": f"userId={os.environ.get(\"CAMOFOX_USER_ID\", \"operator\")}, sessionKey=glm-quota",
    "dashboard_url": "https://z.ai/manage-apikey/subscription",
    "status": status,
    "checked_at": now_iso
}

json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
