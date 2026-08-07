#!/usr/bin/env bash
# scrape-quota-openai-codex.sh — Scrape OpenAI Codex usage from chatgpt.com
#
# WORKFLOW:
#   1. Login to Google (accounts.google.com) with ${GOOGLE_LOGIN_EMAIL:-operator@example.com}
#   2. Login to platform.openai.com via Google OAuth (same-tab redirect works)
#   3. Navigate to chatgpt.com/codex/settings/usage (session shared)
#   4. Parse accessibility snapshot for usage percentages
#
# NOTE: chatgpt.com Google OAuth uses popups (broken in Camoufox),
#       but platform.openai.com uses same-tab redirect. Login via platform
#       first, then session carries over to chatgpt.com.
#
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-$(grep CAMOFOX_API_KEY ~/clawd/secrets/camofox.env 2>/dev/null | cut -d= -f2)}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
CAMOFOX_SESSION_KEY="${CAMOFOX_SESSION_KEY:-openai-codex-quota}"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
CODEX_QUOTA_FILE="${CODEX_QUOTA_FILE:-$STATE_DIR/openai-codex-quota.json}"
mkdir -p "$(dirname "$CODEX_QUOTA_FILE")"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GOOGLE_PASS="${GOOGLE_PASS:-}"

H=(-H "x-api-key: $API_KEY")
J=(-H "Content-Type: application/json")

cf() { curl -sf "$@"; }

cleanup_tab() {
  [[ -n "${1:-}" ]] && cf -X DELETE "${CAMOFOX_URL}/tabs/${1}?userId=${CAMOFOX_USER_ID}&sessionKey=${CAMOFOX_SESSION_KEY}" "${H[@]}" >/dev/null 2>&1 || true
}

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$CODEX_QUOTA_FILE"
  exit 1
}

snap() {
  cf "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${CAMOFOX_SESSION_KEY}" "${H[@]}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null
}

nav() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/navigate" "${J[@]}" "${H[@]}" \
    -d "{\"url\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$CAMOFOX_SESSION_KEY\"}" >/dev/null
}

click_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$CAMOFOX_SESSION_KEY\"}" >/dev/null
}

type_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/type" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"text\":\"$2\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$CAMOFOX_SESSION_KEY\"}" >/dev/null
}

press_key() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/press" "${J[@]}" "${H[@]}" \
    -d "{\"key\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$CAMOFOX_SESSION_KEY\"}" >/dev/null
}

# Check Camofox
cf --max-time 5 "$CAMOFOX_URL/health" "${H[@]}" >/dev/null 2>&1 || err "camofox_not_running"

# --- Create tab ---
TAB=$(cf -X POST "$CAMOFOX_URL/tabs" "${J[@]}" "${H[@]}" \
  -d "{\"url\":\"https://chatgpt.com/codex/settings/usage\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$CAMOFOX_SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('tabId',''))" 2>/dev/null)
[[ -n "$TAB" ]] || err "tab_create_failed"
sleep 6

# --- Check if logged in ---
SNAP_TEXT=$(snap)
if echo "$SNAP_TEXT" | grep -q "Log in\|Sign up for free\|Welcome back"; then
  echo "Not logged in, starting login flow..." >&2

  # Step 1: Login to Google
  nav "https://accounts.google.com/signin"
  sleep 5
  SNAP_TEXT=$(snap)

  if echo "$SNAP_TEXT" | grep -q "Email or phone"; then
    type_ref "e1" "${GOOGLE_LOGIN_EMAIL:-operator@example.com}"
    sleep 1
    press_key "Enter"
    sleep 5
    SNAP_TEXT=$(snap)
    if echo "$SNAP_TEXT" | grep -q "Enter your password"; then
      [[ -n "$GOOGLE_PASS" ]] || err "google_password_required_or_login_manually"
      type_ref "e2" "$GOOGLE_PASS"
      sleep 1
      press_key "Enter"
      sleep 8
      SNAP_TEXT=$(snap)
      if echo "$SNAP_TEXT" | grep -qi "2-step\|verify\|confirm.*identity"; then
        cleanup_tab "$TAB"
        err "google_2fa_required"
      fi
    fi
  fi

  # Step 2: Login to platform.openai.com (same-tab Google OAuth works here)
  nav "https://platform.openai.com/login"
  sleep 5
  SNAP_TEXT=$(snap)

  if echo "$SNAP_TEXT" | grep -q "Continue with Google"; then
    GOOGLE_REF=$(echo "$SNAP_TEXT" | grep -o 'Continue with Google" \[e[0-9]*\]' | grep -o 'e[0-9]*')
    [[ -n "$GOOGLE_REF" ]] && click_ref "$GOOGLE_REF"
    sleep 12
  fi

  # Step 3: Navigate to Codex usage (session shared across OpenAI domains)
  nav "https://chatgpt.com/codex/settings/usage"
  sleep 6
fi

# --- Scrape usage data ---
SNAP_TEXT=$(snap)

# Check if we're on the right page
if ! echo "$SNAP_TEXT" | grep -q "Usage dashboard\|5 hour usage\|Weekly usage"; then
  cleanup_tab "$TAB"
  err "not_on_usage_page"
fi

echo "Scraping Codex usage..." >&2

cleanup_tab "$TAB"

# --- Parse snapshot ---
python3 << PYEOF
import json, re, sys

snap = """$(echo "$SNAP_TEXT" | sed "s/\"/\\\\\\\\\\\\&/g")"""
now_iso = "$NOW_ISO"
quota_file = "$CODEX_QUOTA_FILE"

# Parse 5 hour usage
m = re.search(r'5 hour usage limit.*?(\d+)% remaining.*?Resets (.+?)$', snap, re.M | re.S)
five_hour_pct = int(m.group(1)) if m else None
five_hour_resets = m.group(2).strip() if m else None

# Parse weekly usage
m = re.search(r'Weekly usage limit.*?(\d+)% remaining.*?Resets (.+?)$', snap, re.M | re.S)
weekly_pct = int(m.group(1)) if m else None
weekly_resets = m.group(2).strip() if m else None

# Parse code review
m = re.search(r'Code review.*?(\d+)% remaining', snap, re.M | re.S)
code_review_pct = int(m.group(1)) if m else None

# Determine status
if five_hour_pct is not None:
    if five_hour_pct < 10:
        status = "exhausted"
        reason = f"5h limit nearly depleted: {five_hour_pct}% remaining"
    elif five_hour_pct < 30:
        status = "warning"
        reason = f"5h limit low: {five_hour_pct}% remaining"
    elif weekly_pct is not None and weekly_pct < 20:
        status = "warning"
        reason = f"Weekly limit low: {weekly_pct}% remaining"
    else:
        status = "healthy"
        reason = f"5h: {five_hour_pct}% remaining, weekly: {weekly_pct}% remaining"
else:
    status = "unknown"
    reason = "Could not parse usage data"

result = {
    "provider": "openai-codex",
    "dashboard_url": "https://chatgpt.com/codex/settings/usage",
    "five_hour_remaining_pct": five_hour_pct,
    "five_hour_resets": five_hour_resets,
    "weekly_remaining_pct": weekly_pct,
    "weekly_resets": weekly_resets,
    "code_review_remaining_pct": code_review_pct,
    "login": "${GOOGLE_LOGIN_EMAIL:-operator@example.com} (Google OAuth via platform.openai.com)",
    "status": status,
    "status_reason": reason,
    "checked_at": now_iso
}

json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
