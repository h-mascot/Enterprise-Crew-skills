#!/usr/bin/env bash
# scrape-quota-minimax.sh — Check MiniMax quota via API ping + dashboard plan info
# MiniMax doesn't show usage meters, so we:
# 1. Ping the API to check health (primary signal)
# 2. Scrape subscription page for plan info via Camofox (if available)
# Requires: Camofox on localhost:9377 for plan scraping, API key for health check
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-$(grep CAMOFOX_API_KEY ~/clawd/secrets/camofox.env 2>/dev/null | cut -d= -f2)}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
SESSION_KEY="minimax-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/minimax-quota.json"
TMP_SNAPSHOT="/tmp/minimax-snapshot-$$.txt"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cleanup() { rm -f "$TMP_SNAPSHOT"; }
trap cleanup EXIT

# Step 1: API ping (primary health check)
MINIMAX_KEY=$(python3 -c "import json; print(json.load(open('$HOME/.openclaw/openclaw.json'))['models']['providers']['minimax']['apiKey'])" 2>/dev/null || echo "")
API_STATUS="unknown"
if [[ -n "$MINIMAX_KEY" ]]; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
    -H "x-api-key: $MINIMAX_KEY" \
    -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"MiniMax-M2.5","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
    "https://api.minimax.io/anthropic/v1/messages" 2>/dev/null || echo "000")
  case "$HTTP_CODE" in
    200) API_STATUS="up" ;;
    429) API_STATUS="rate_limited" ;;
    *) API_STATUS="down" ;;
  esac
fi

# Step 2: Scrape plan info from dashboard (if Camofox available)
PLAN="unknown"
PLAN_LIMIT="unknown"
DASHBOARD_STATUS="skipped"

if curl -sf --max-time 3 "$CAMOFOX_URL/health" -H "x-api-key: $API_KEY" >/dev/null 2>&1; then
  TAB_ID=$(curl -sf -X POST "$CAMOFOX_URL/tabs" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{\"url\":\"https://platform.minimax.io/subscribe/coding-plan\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['tabId'])" 2>/dev/null)

  if [[ -n "$TAB_ID" ]]; then
    sleep 5
    SNAPSHOT=$(curl -s --max-time 15 "${CAMOFOX_URL}/tabs/${TAB_ID}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
      -H "x-api-key: $API_KEY" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null)
    curl -s -X DELETE "${CAMOFOX_URL}/tabs/${TAB_ID}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
      -H "x-api-key: $API_KEY" >/dev/null 2>&1

    if [[ -n "$SNAPSHOT" ]]; then
      echo "$SNAPSHOT" > "$TMP_SNAPSHOT"
      DASHBOARD_STATUS="scraped"

      # Parse plan info from snapshot
      PARSED=$(python3 - "$TMP_SNAPSHOT" << 'PYEOF'
import json, re, sys, os

tmp = sys.argv[1]
with open(tmp) as f:
    snapshot = f.read()

# Check login
if ("Sign in" in snapshot or "Sign Up" in snapshot) and "Plus" not in snapshot:
    print("session_expired|needs_login")
    sys.exit(0)

# Find current plan - look for plan with "Change" nearby (current plan has Change, others have Upgrade)
plan = "unknown"
limit = "unknown"

# Plans: Starter (free), Plus ($40), Max ($100), Ultra ($200)
for p in ["Ultra", "Max", "Plus", "Starter"]:
    if p in snapshot:
        # Check if this plan section has "Change" (indicating current)
        idx = snapshot.find(p)
        section = snapshot[max(0,idx-200):idx+500]
        if "Change" in section or "Current" in section:
            plan = p
            break

# Find prompt limits: "300 prompts / 5 hours" pattern
m = re.search(r'(\d+)\s*prompts?\s*/\s*(\d+)\s*hours?', snapshot)
if m:
    limit = f"{m.group(1)} prompts / {m.group(2)}h"

# If we found the limit but not the plan, default based on limit
if plan == "unknown" and limit != "unknown":
    limit_map = {"50": "Starter", "300": "Plus", "600": "Max", "1200": "Ultra"}
    m2 = re.match(r'(\d+)', limit)
    if m2:
        plan = limit_map.get(m2.group(1), "unknown")

print(f"{plan}|{limit}")
PYEOF
      )
      PLAN=$(echo "$PARSED" | cut -d'|' -f1)
      PLAN_LIMIT=$(echo "$PARSED" | cut -d'|' -f2)
    fi
  fi
fi

# Determine overall status
STATUS="$API_STATUS"
[[ "$PLAN" == "session_expired" ]] && DASHBOARD_STATUS="session_expired"

# Write result. Pass shell values as arguments so the quoted heredoc cannot
# accidentally write literal "$VARIABLE" placeholders.
python3 - \
  "$CAMOFOX_USER_ID" \
  "$SESSION_KEY" \
  "$API_STATUS" \
  "$PLAN" \
  "$PLAN_LIMIT" \
  "$DASHBOARD_STATUS" \
  "${MINIMAX_LOGIN_EMAIL:-operator@example.com}" \
  "$STATUS" \
  "$NOW_ISO" \
  "$QUOTA_FILE" << 'PYEOF'
import json, sys
(
    user_id,
    session_key,
    api_status,
    plan,
    plan_limit,
    dashboard_status,
    login_email,
    status,
    checked_at,
    quota_file,
) = sys.argv[1:]
result = {
    "provider": "minimax",
    "api_status": api_status,
    "plan": plan,
    "plan_limit": plan_limit,
    "dashboard_status": dashboard_status,
    "login": login_email,
    "camofox_session": f"userId={user_id}, sessionKey={session_key}",
    "dashboard_url": "https://platform.minimax.io/subscribe/coding-plan",
    "note": "MiniMax uses rolling prompt limits (no usage meter). API ping is primary health signal.",
    "status": status,
    "checked_at": checked_at,
}
json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
