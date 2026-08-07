#!/usr/bin/env bash
# scrape-quota-openai.sh — Scrape OpenAI billing/usage from platform.openai.com
#
# WORKFLOW:
#   1. Login to Google (accounts.google.com) with $GOOGLE_EMAIL
#   2. Navigate to platform.openai.com → Continue with Google
#   3. Scrape billing overview (credit balance, auto-recharge)
#   4. Scrape credit grants (expiry dates)
#   5. Scrape usage page (period spend, tokens, requests)
#   6. Scrape limits page (tier, budget, monthly limit)
#
# NOTE: Unlike claude.ai, OpenAI platform renders fully in Camoufox!
#
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
SESSION_KEY="openai-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/openai-quota.json"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GOOGLE_EMAIL="${GOOGLE_EMAIL:-}"
GOOGLE_PASS="${GOOGLE_PASS:-}"

H=(-H "x-api-key: $API_KEY")
J=(-H "Content-Type: application/json")

cf() { curl -sf "$@"; }

cleanup_tab() {
  [[ -n "${1:-}" ]] && cf -X DELETE "${CAMOFOX_URL}/tabs/${1}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" "${H[@]}" >/dev/null 2>&1 || true
}

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$QUOTA_FILE"
  exit 1
}

snap() {
  cf "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" "${H[@]}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null
}

nav() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/navigate" "${J[@]}" "${H[@]}" \
    -d "{\"url\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

click_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

type_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/type" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"text\":\"$2\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

press_key() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/press" "${J[@]}" "${H[@]}" \
    -d "{\"key\":\"$1\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

# Check Camofox
cf --max-time 5 "$CAMOFOX_URL/health" "${H[@]}" >/dev/null 2>&1 || err "camofox_not_running"

# --- Create tab ---
TAB=$(cf -X POST "$CAMOFOX_URL/tabs" "${J[@]}" "${H[@]}" \
  -d "{\"url\":\"https://platform.openai.com/settings/organization/billing\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('tabId',''))" 2>/dev/null)
[[ -n "$TAB" ]] || err "tab_create_failed"
sleep 6

# --- Check if logged in ---
SNAP_TEXT=$(snap)
if echo "$SNAP_TEXT" | grep -q "Log in\|Authentication required"; then
  echo "Not logged in, starting Google OAuth flow..." >&2
  
  # Login to Google first
  nav "https://accounts.google.com/signin"
  sleep 5
  
  SNAP_TEXT=$(snap)
  if echo "$SNAP_TEXT" | grep -q "Email or phone"; then
    type_ref "e1" "${GOOGLE_EMAIL:-}"
    sleep 1
    press_key "Enter"
    sleep 5
    
    SNAP_TEXT=$(snap)
    if echo "$SNAP_TEXT" | grep -q "Enter your password"; then
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
  
  # Now navigate to OpenAI login
  nav "https://platform.openai.com/login"
  sleep 5
  
  SNAP_TEXT=$(snap)
  if echo "$SNAP_TEXT" | grep -q "Continue with Google"; then
    # Find the Google button ref
    GOOGLE_REF=$(echo "$SNAP_TEXT" | grep -o 'Continue with Google" \[e[0-9]*\]' | grep -o 'e[0-9]*')
    [[ -n "$GOOGLE_REF" ]] && click_ref "$GOOGLE_REF"
    sleep 8
  fi
  
  # Navigate to billing
  nav "https://platform.openai.com/settings/organization/billing"
  sleep 5
fi

# --- Scrape billing overview ---
echo "Scraping billing overview..." >&2
SNAP_TEXT=$(snap)

CREDIT_BALANCE=$(echo "$SNAP_TEXT" | grep -oP '\$[\d,]+\.\d+' | head -1 | tr -d '$,')
AUTO_RECHARGE=$(echo "$SNAP_TEXT" | grep -q "Auto recharge is off" && echo "false" || echo "true")
PLAN=$(echo "$SNAP_TEXT" | grep -oP 'Pay as you go|Free|Plus|Enterprise' | head -1)
PLAN="${PLAN:-Pay as you go}"

# --- Scrape credit grants ---
echo "Scraping credit grants..." >&2
nav "https://platform.openai.com/settings/organization/billing/credit-grants"
sleep 4
GRANTS_SNAP=$(snap)

CREDIT_TOTAL=$(echo "$GRANTS_SNAP" | grep -oP 'USD \$[\d,]+\.\d+ / \$[\d,]+\.\d+' | grep -oP '/ \$[\d,]+\.\d+' | tr -d '/ $,')
CREDIT_USED=$(echo "$GRANTS_SNAP" | grep -oP 'USD \$[\d,]+\.\d+' | head -1 | grep -oP '[\d,]+\.\d+' | tr -d ',')

# Get latest grant expiry
LATEST_EXPIRY=$(echo "$GRANTS_SNAP" | grep -oP '\w+ \d+, \d{4}' | tail -1)

# --- Scrape usage ---
echo "Scraping usage..." >&2
nav "https://platform.openai.com/settings/organization/usage"
sleep 5
USAGE_SNAP=$(snap)

PERIOD_SPEND=$(echo "$USAGE_SNAP" | grep -oP 'Total Spend \$[\d,]+\.\d+' | grep -oP '[\d,]+\.\d+' | tr -d ',')
PERIOD=$(echo "$USAGE_SNAP" | grep -oP '\d{2}/\d{2}/\d{2}-\d{2}/\d{2}/\d{2}' | head -1)
TOTAL_TOKENS=$(echo "$USAGE_SNAP" | grep -oP 'Total tokens [\d,]+' | grep -oP '[\d,]+' | tr -d ',')
TOTAL_REQUESTS=$(echo "$USAGE_SNAP" | grep -oP 'Total requests [\d,]+' | grep -oP '[\d,]+' | tr -d ',')

# --- Scrape limits ---
echo "Scraping limits..." >&2
nav "https://platform.openai.com/settings/organization/limits"
sleep 4
LIMITS_SNAP=$(snap)

USAGE_TIER=$(echo "$LIMITS_SNAP" | grep -oP 'Usage tier \d+' | grep -oP '\d+')
BUDGET_LINE=$(echo "$LIMITS_SNAP" | grep -oP 'budget \$[\d,]+\.\d+ / \$[\d,]+\.\d+' | head -1)
BUDGET_USED=$(echo "$BUDGET_LINE" | grep -oP '\$[\d,]+\.\d+' | head -1 | tr -d '$,')
BUDGET_LIMIT=$(echo "$BUDGET_LINE" | grep -oP '\$[\d,]+\.\d+' | tail -1 | tr -d '$,')
MONTHLY_LIMIT=$(echo "$LIMITS_SNAP" | grep -oP 'each month\. \$[\d,]+\.\d+' | grep -oP '[\d,]+\.\d+' | tr -d ',')

cleanup_tab "$TAB"

# --- Build JSON ---
python3 << PYEOF
import json

data = {
    "provider": "openai",
    "organization": "Curacel",
    "plan": "${PLAN}",
    "usage_tier": int("${USAGE_TIER:-0}" or "0"),
    "credit_balance": float("${CREDIT_BALANCE:-0}" or "0"),
    "credit_total": float("${CREDIT_TOTAL:-0}" or "0"),
    "credit_used": float("${CREDIT_USED:-0}" or "0"),
    "credit_expires": "${LATEST_EXPIRY}",
    "auto_recharge": ${AUTO_RECHARGE},
    "monthly_budget_used": float("${BUDGET_USED:-0}" or "0"),
    "monthly_budget_limit": float("${BUDGET_LIMIT:-0}" or "0"),
    "monthly_usage_limit": float("${MONTHLY_LIMIT:-0}" or "0"),
    "period_spend": float("${PERIOD_SPEND:-0}" or "0"),
    "period": "${PERIOD}",
    "total_tokens": int("${TOTAL_TOKENS:-0}" or "0"),
    "total_requests": int("${TOTAL_REQUESTS:-0}" or "0"),
    "currency": "USD",
    "login": "${GOOGLE_EMAIL:-} (Google OAuth)",
    "dashboard_url": "https://platform.openai.com/settings/organization/billing",
    "checked_at": "$NOW_ISO"
}

# Determine status
balance = data["credit_balance"]
budget_used = data["monthly_budget_used"]
budget_limit = data["monthly_budget_limit"]
reasons = []

if budget_limit > 0 and budget_used > budget_limit:
    reasons.append(f"Budget \${budget_used:,.0f}/\${budget_limit:,.0f} ({budget_used/budget_limit*100:.0f}% over)")
if not data["auto_recharge"]:
    reasons.append("Auto-recharge OFF")
if balance < 100:
    reasons.append(f"Low balance: \${balance:,.2f}")

data["status"] = "warning" if reasons else "healthy"
data["status_reason"] = ". ".join(reasons) if reasons else f"Tier {data['usage_tier']}, \${balance:,.2f} credits remaining"

json.dump(data, open("$QUOTA_FILE", "w"), indent=2)
print(json.dumps(data, indent=2))
PYEOF
