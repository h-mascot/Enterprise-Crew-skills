#!/usr/bin/env bash
# scrape-quota-gemini.sh — Scrape Gemini API rate limits from aistudio.google.com
#
# WORKFLOW:
#   1. Google session already exists ($GOOGLE_EMAIL)
#   2. Navigate to aistudio.google.com/rate-limit?timeRange=last-28-days
#   3. Accept cookies if prompted
#   4. Toggle "All models" view
#   5. Parse model rows: Model | RPM peak/limit | TPM peak/limit | RPD peak/limit
#
set -uo pipefail

CAMOFOX_URL="${CAMOFOX_URL:-http://localhost:9377}"
API_KEY="${CAMOFOX_API_KEY:-}"
USER_ID="ada"
SESSION_KEY="gemini-quota"
STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
QUOTA_FILE="$STATE_DIR/gemini-quota.json"
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GOOGLE_EMAIL="${GOOGLE_EMAIL:-}"
GOOGLE_PASS="${GOOGLE_PASS:-}"
SNAP_FILE="/tmp/gemini-snap-$$.txt"

H=(-H "x-api-key: $API_KEY")
J=(-H "Content-Type: application/json")

cf() { curl -sf "$@"; }

cleanup_tab() {
  [[ -n "${1:-}" ]] && cf -X DELETE "${CAMOFOX_URL}/tabs/${1}?userId=${USER_ID}&sessionKey=${SESSION_KEY}" "${H[@]}" >/dev/null 2>&1 || true
}

cleanup() { rm -f "$SNAP_FILE"; cleanup_tab "${TAB:-}"; }
trap cleanup EXIT

err() {
  echo "{\"error\":\"$1\",\"checked_at\":\"$NOW_ISO\"}" | tee "$QUOTA_FILE"
  exit 1
}

snap() {
  cf "${CAMOFOX_URL}/tabs/${TAB}/snapshot?userId=${USER_ID}&sessionKey=${SESSION_KEY}" "${H[@]}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('snapshot',''))" 2>/dev/null
}

nav() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/navigate" "${J[@]}" "${H[@]}" \
    -d "{\"url\":\"$1\",\"userId\":\"$USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

click_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/click" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"userId\":\"$USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

type_ref() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/type" "${J[@]}" "${H[@]}" \
    -d "{\"ref\":\"$1\",\"text\":\"$2\",\"userId\":\"$USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

press_key() {
  cf -X POST "${CAMOFOX_URL}/tabs/${TAB}/press" "${J[@]}" "${H[@]}" \
    -d "{\"key\":\"$1\",\"userId\":\"$USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" >/dev/null
}

# Check Camofox
cf --max-time 5 "$CAMOFOX_URL/health" "${H[@]}" >/dev/null 2>&1 || err "camofox_not_running"

# --- Create tab ---
TAB=$(cf -X POST "$CAMOFOX_URL/tabs" "${J[@]}" "${H[@]}" \
  -d "{\"url\":\"https://aistudio.google.com/rate-limit?timeRange=last-28-days\",\"userId\":\"$USER_ID\",\"sessionKey\":\"$SESSION_KEY\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('tabId',''))" 2>/dev/null)
[[ -n "$TAB" ]] || err "tab_create_failed"
sleep 6

SNAP_TEXT=$(snap)

# --- Accept cookies if prompted ---
if echo "$SNAP_TEXT" | grep -q "uses cookies"; then
  AGREE_REF=$(echo "$SNAP_TEXT" | grep -o '"Agree" \[e[0-9]*\]' | grep -o 'e[0-9]*')
  [[ -n "$AGREE_REF" ]] && click_ref "$AGREE_REF"
  sleep 3
  SNAP_TEXT=$(snap)
fi

# --- Check if logged in ---
if echo "$SNAP_TEXT" | grep -q "Sign in\|Create account" && ! echo "$SNAP_TEXT" | grep -q "Rate Limit\|Rate limits"; then
  echo "Not logged in, logging into Google..." >&2
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
        err "google_2fa_required"
      fi
    fi
  fi

  nav "https://aistudio.google.com/rate-limit?timeRange=last-28-days"
  sleep 6
  SNAP_TEXT=$(snap)

  if echo "$SNAP_TEXT" | grep -q "uses cookies"; then
    AGREE_REF=$(echo "$SNAP_TEXT" | grep -o '"Agree" \[e[0-9]*\]' | grep -o 'e[0-9]*')
    [[ -n "$AGREE_REF" ]] && click_ref "$AGREE_REF"
    sleep 3
    SNAP_TEXT=$(snap)
  fi
fi

# --- Toggle "All models" ---
ALL_MODELS_REF=$(echo "$SNAP_TEXT" | grep -o 'Toggle view all models" \[e[0-9]*\]' | grep -o 'e[0-9]*')
if [[ -n "$ALL_MODELS_REF" ]]; then
  click_ref "$ALL_MODELS_REF"
  sleep 3
  SNAP_TEXT=$(snap)
fi

# --- Verify ---
if ! echo "$SNAP_TEXT" | grep -q "Rate limits by model\|RPM\|TPM"; then
  err "not_on_rate_limit_page"
fi

echo "Scraping Gemini rate limits..." >&2

# Save snapshot to file for Python to read
echo "$SNAP_TEXT" > "$SNAP_FILE"

# --- Parse ---
python3 - "$SNAP_FILE" "$NOW_ISO" "$QUOTA_FILE" << 'PYEOF'
import json, re, sys

snap_file, now_iso, quota_file = sys.argv[1], sys.argv[2], sys.argv[3]
snap = open(snap_file).read()

# Extract tier
tier_m = re.search(r'(Free|Paid) tier (\d+)', snap)
tier = f"{tier_m.group(1)} tier {tier_m.group(2)}" if tier_m else "unknown"

# Extract project
proj_m = re.search(r'combobox "Project": (.+?)$', snap, re.M)
project = proj_m.group(1).strip() if proj_m else "unknown"

# Extract model rows
rows = re.findall(
    r'row "([\w\s.]+?)\s+(Text-out models|Multi-modal generative models|Other models)\s+'
    r'([\d.]+K?)\s*/\s*([\d.]+K?|Unlimited)\s+'
    r'([\d.]+K?|N/A)\s*/\s*([\d.]+[KM]?|Unlimited|N/A)\s+'
    r'([\d.]+K?)\s*/\s*([\d.]+K?|Unlimited)\s+View in charts"',
    snap
)

def parse_num(s):
    if s in ('Unlimited', 'N/A'):
        return s
    s = s.replace(',', '')
    if s.endswith('M'):
        return int(float(s[:-1]) * 1_000_000)
    if s.endswith('K'):
        return int(float(s[:-1]) * 1_000)
    return int(float(s))

models = {}
for name, category, peak_rpm, limit_rpm, peak_tpm, limit_tpm, peak_rpd, limit_rpd in rows:
    name = name.strip()
    key = name.lower().replace(' ', '-')
    models[key] = {
        "name": name,
        "category": category,
        "peak_rpm": parse_num(peak_rpm),
        "limit_rpm": parse_num(limit_rpm),
        "peak_tpm": parse_num(peak_tpm),
        "limit_tpm": parse_num(limit_tpm),
        "peak_rpd": parse_num(peak_rpd),
        "limit_rpd": parse_num(limit_rpd),
        "rpm_usage": f"{peak_rpm}/{limit_rpm}",
        "tpm_usage": f"{peak_tpm}/{limit_tpm}",
        "rpd_usage": f"{peak_rpd}/{limit_rpd}"
    }

warnings = []
for key, m in models.items():
    if isinstance(m['limit_rpm'], int) and isinstance(m['peak_rpm'], int) and m['limit_rpm'] > 0:
        if m['peak_rpm'] / m['limit_rpm'] > 0.8:
            warnings.append(f"{m['name']} RPM at {m['peak_rpm']}/{m['limit_rpm']}")
    if isinstance(m['limit_rpd'], int) and isinstance(m['peak_rpd'], int) and m['limit_rpd'] > 0:
        if m['peak_rpd'] / m['limit_rpd'] > 0.8:
            warnings.append(f"{m['name']} RPD at {m['peak_rpd']}/{m['limit_rpd']}")

status = "warning" if warnings else "healthy"
reason = "; ".join(warnings) if warnings else f"{tier}. {len(models)} models tracked. Peak usage well within limits."

result = {
    "provider": "gemini",
    "tier": tier,
    "project": project,
    "account": "${GOOGLE_EMAIL:-}",
    "dashboard_url": "https://aistudio.google.com/rate-limit?timeRange=last-28-days",
    "models": models,
    "model_count": len(models),
    "warnings": warnings,
    "status": status,
    "status_reason": reason,
    "checked_at": now_iso
}

json.dump(result, open(quota_file, "w"), indent=2)
print(json.dumps(result, indent=2))
PYEOF
