#!/usr/bin/env bash
# scrape-quota-anthropic.sh — Scrape Anthropic (Claude Max) usage from claude.ai
#
# WORKFLOW:
#   1. Login to Google (accounts.google.com) with $GOOGLE_EMAIL
#   2. Navigate to claude.ai (auto-login via Google session)
#   3. Navigate to claude.ai/settings/usage
#   4. Scrape plan limits, extra usage, balance
#
# NOTE: The Usage page content doesn't render in Camoufox's accessibility tree,
#       but we can still extract data via screenshot + OCR or by hitting the
#       internal API. Falls back to cached data if scrape fails.
#
# CORRECT URL: claude.ai/settings/usage (NOT platform.claude.com)
#
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-}"
CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"
SESSION_KEY="anthropic-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/anthropic-quota.json"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GOOGLE_EMAIL="${GOOGLE_EMAIL:-}"
GOOGLE_PASS="${GOOGLE_PASS:-}"

cleanup_sensitive_temp_files() {
  [[ -n "${SCREENSHOT:-}" ]] && rm -f -- "$SCREENSHOT"
  [[ -n "${OCR_TMP:-}" ]] && rm -f -- "$OCR_TMP"
}
trap cleanup_sensitive_temp_files EXIT

cleanup_tab() {
  [[ -n "${1:-}" ]] && curl -sf -X DELETE "${CAMOFOX_URL}/tabs/${1}?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
    -H "x-api-key: $API_KEY" >/dev/null 2>&1 || true
}

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$QUOTA_FILE"
  exit 1
}

# Check Camofox
curl -sf --max-time 5 "$CAMOFOX_URL/health" -H "x-api-key: $API_KEY" >/dev/null 2>&1 || err "camofox_not_running"

# --- Step 1: Login to Google ---
TAB=$(curl -sf -X POST "$CAMOFOX_URL/tabs" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"url\":\"https://accounts.google.com/signin\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('tabId',''))" 2>/dev/null)
[[ -n "$TAB" ]] || err "tab_create_failed"

sleep 5

# Check if already logged into Google
SNAP=$(curl -s "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
  -H "x-api-key: $API_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null)

if echo "$SNAP" | grep -q "myaccount.google.com\|Google Account"; then
  echo "Already logged into Google" >&2
elif echo "$SNAP" | grep -q "Email or phone"; then
  echo "Logging into Google..." >&2
  # Type email
  curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/type" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{\"ref\":\"e1\",\"text\":\"${GOOGLE_EMAIL:-}\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
  sleep 1
  # Click Next
  curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{\"ref\":\"e4\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
  sleep 5

  # Check for password page
  SNAP2=$(curl -s "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
    -H "x-api-key: $API_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null)

  if echo "$SNAP2" | grep -q "Enter your password"; then
    # Type password
    curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/type" \
      -H "Content-Type: application/json" \
      -H "x-api-key: $API_KEY" \
      -d "{\"ref\":\"e2\",\"text\":\"${GOOGLE_PASS}\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
    sleep 1
    curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" \
      -H "Content-Type: application/json" \
      -H "x-api-key: $API_KEY" \
      -d "{\"ref\":\"e4\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
    sleep 8

    # Check if 2FA is needed
    SNAP3=$(curl -s "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
      -H "x-api-key: $API_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null)
    if echo "$SNAP3" | grep -qi "2-step\|verify\|confirm.*identity"; then
      cleanup_tab "$TAB"
      err "google_2fa_required"
    fi
  elif echo "$SNAP2" | grep -qi "2-step\|verify\|confirm.*identity"; then
    cleanup_tab "$TAB"
    err "google_2fa_required"
  fi
fi

# --- Step 2: Navigate to claude.ai ---
curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/navigate" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"url\":\"https://claude.ai/settings/usage\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
sleep 6

# Check if we're logged into Claude
URL=$(curl -s "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}" \
  -H "x-api-key: $API_KEY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null)

if echo "$URL" | grep -q "login"; then
  # Need to login — click Google on claude.ai login page
  # Since Google session is active, this should auto-complete
  curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{\"ref\":\"e10\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
  sleep 8

  # Re-navigate to usage
  curl -sf -X POST "${CAMOFOX_URL}/tabs/${TAB}/navigate" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{\"url\":\"https://claude.ai/settings/usage\",\"userId\":\"$CAMOFOX_USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
  sleep 5
fi

# --- Step 3: Take screenshot and OCR the usage data ---
SCREENSHOT="/tmp/anthropic-usage-$$.png"
curl -sf "${CAMOFOX_URL}/tabs/${TAB}/screenshot?userId=${CAMOFOX_USER_ID}&sessionKey=${SESSION_KEY}&format=png&fullPage=true" \
  -H "x-api-key: $API_KEY" -o "$SCREENSHOT" 2>/dev/null

cleanup_tab "$TAB"

# Check we got a screenshot
[[ -s "$SCREENSHOT" ]] || err "screenshot_failed"

# --- Step 4: Parse screenshot via OCR ---
# Use tesseract if available, otherwise skip to cached
if command -v tesseract &>/dev/null; then
  OCR_TEXT=$(tesseract "$SCREENSHOT" - 2>/dev/null || echo "")
else
  OCR_TEXT=""
fi

# Write OCR text to a temp file so the Python parser reads it safely
# instead of interpolating untrusted OCR output into a heredoc.
OCR_TMP="$(mktemp)"
printf '%s' "$OCR_TEXT" > "$OCR_TMP"

# Parse OCR text or fall back to snapshot accessibility data
# Quoted delimiter 'PYEOF' prevents shell expansion inside the heredoc;
# all dynamic values are passed as positional arguments.
python3 - "$NOW_ISO" "$QUOTA_FILE" "$SCREENSHOT" "$OCR_TMP" << 'PYEOF'
import json, re, sys, os

now_iso = sys.argv[1]
quota_file = sys.argv[2]
screenshot = sys.argv[3]
ocr_tmp = sys.argv[4]
with open(ocr_tmp, "r") as _f:
    ocr_text = _f.read()

# Try to parse from OCR text
session_pct = None
weekly_all_pct = None
weekly_sonnet_pct = None
extra_spent = None
extra_limit = None
extra_pct = None
balance = None
auto_reload = None

if ocr_text:
    # Session usage: "12% used" near "Current session"
    m = re.search(r'Current session.*?(\d+)%\s*used', ocr_text, re.DOTALL | re.I)
    if m: session_pct = int(m.group(1))

    # Weekly all models
    m = re.search(r'All models.*?(\d+)%\s*used', ocr_text, re.DOTALL | re.I)
    if m: weekly_all_pct = int(m.group(1))

    # Sonnet only
    m = re.search(r'Sonnet only.*?(\d+)%\s*used', ocr_text, re.DOTALL | re.I)
    if m: weekly_sonnet_pct = int(m.group(1))

    # Extra usage spent
    m = re.search(r'[£$]([\d.]+)\s*spent', ocr_text, re.I)
    if m: extra_spent = float(m.group(1))

    # Extra usage pct
    m = re.search(r'(\d+)%\s*used.*?(?:Resets|Mar|Apr|May)', ocr_text, re.DOTALL | re.I)
    # Get the last percentage before "Resets Mar"
    pcts = re.findall(r'(\d+)%\s*used', ocr_text)
    if len(pcts) >= 4: extra_pct = int(pcts[3])
    elif len(pcts) >= 3: extra_pct = int(pcts[2])

    # Balance
    m = re.search(r'[£$]([\d.]+)\s*(?:Current balance|balance)', ocr_text, re.I)
    if m: balance = float(m.group(1))

    # Monthly limit
    m = re.search(r'[£$](\d+)\s*.*Monthly spending limit', ocr_text, re.I)
    if m: extra_limit = float(m.group(1))

    # Auto-reload
    auto_reload = "Auto-reload off" not in ocr_text if "Auto-reload" in ocr_text else None

# Determine status
if extra_pct and extra_pct >= 100:
    status = "warning"
    reason = f"Extra usage at {extra_pct}%, balance low"
elif weekly_all_pct and weekly_all_pct >= 80:
    status = "warning"
    reason = f"Weekly usage at {weekly_all_pct}%"
elif session_pct is not None:
    status = "healthy"
    reason = f"Session {session_pct}%, weekly {weekly_all_pct}%"
else:
    status = "unknown"
    reason = "Could not parse usage data from screenshot"

# If we got data, save it
if session_pct is not None or weekly_all_pct is not None:
    result = {
        "provider": "anthropic",
        "plan": "Max",
        "organization": "Curacel",
        "session_usage_pct": session_pct,
        "weekly_all_models_pct": weekly_all_pct,
        "weekly_sonnet_only_pct": weekly_sonnet_pct,
        "extra_usage_spent": extra_spent,
        "extra_usage_limit": extra_limit,
        "extra_usage_pct": extra_pct,
        "current_balance": balance,
        "auto_reload": auto_reload,
        "currency": "GBP",
        "login": "${GOOGLE_EMAIL:-} (Google OAuth via claude.ai)",
        "dashboard_url": "https://claude.ai/settings/usage",
        "status": status,
        "status_reason": reason,
        "checked_at": now_iso
    }
else:
    # Fall back to cached if available
    if os.path.exists(quota_file):
        result = json.load(open(quota_file))
        result["checked_at"] = now_iso
        result["note"] = "OCR failed, using cached data"
    else:
        result = {
            "provider": "anthropic",
            "error": "ocr_parse_failed",
            "note": "Usage page renders but OCR could not extract data. Install tesseract or update manually.",
            "dashboard_url": "https://claude.ai/settings/usage",
            "checked_at": now_iso
        }

json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
