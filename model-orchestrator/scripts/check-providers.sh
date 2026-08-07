#!/usr/bin/env bash
# Check provider health and output status JSON
# Used by the orchestrator cron which then distributes via cron tool
set -uo pipefail

STATE_DIR="$(cd "$(dirname "$0")" && pwd)/../state"
PROVIDER_STATUS="$STATE_DIR/provider-status.json"
PROVIDER_TRACKING="$STATE_DIR/provider-tracking.json"
TIMEOUT=15
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Test MiniMax
MINIMAX_KEY=$(python3 -c "import json; print(json.load(open('$HOME/.openclaw/openclaw.json'))['models']['providers']['minimax']['apiKey'])" 2>/dev/null || echo "")
if [[ -n "$MINIMAX_KEY" ]]; then
  MINIMAX_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
    -H "x-api-key: $MINIMAX_KEY" \
    -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"MiniMax-M2.1","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
    "https://api.minimax.io/anthropic/v1/messages" 2>/dev/null || echo "000")
  [[ "$MINIMAX_CODE" == "200" ]] && MINIMAX_S="up" || MINIMAX_S="down"
else
  MINIMAX_S="no_key"
fi

# Test Gemini Flash 3
GEMINI_KEY="${GEMINI_API_KEY:-}"
GEMINI_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key=$GEMINI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"ping"}]}]}' 2>/dev/null || echo "000")
[[ "$GEMINI_CODE" == "200" ]] && GEMINI_S="up" || { [[ "$GEMINI_CODE" == "429" ]] && GEMINI_S="rate_limited" || GEMINI_S="down"; }

# Test GLM (Z.ai) — use quota scraper if Camofox available, else API ping
GLM_QUOTA_FILE="$STATE_DIR/glm-quota.json"
GLM_S="unknown"
GLM_USAGE=""

# Check if Camofox has a browser connected (not just server running)
CAMOFOX_BROWSER_OK=false
CAMOFOX_HEALTH=$(curl -sf --max-time 3 "http://localhost:9377/health" 2>/dev/null || echo "{}")
if echo "$CAMOFOX_HEALTH" | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin).get('browserConnected') else 1)" 2>/dev/null; then
  CAMOFOX_BROWSER_OK=true
fi

GLM_SCRAPE_SCRIPT="$(dirname "$0")/scrape-quota-glm.sh"
GLM_SCRAPED=false
if $CAMOFOX_BROWSER_OK && [[ -x "$GLM_SCRAPE_SCRIPT" ]]; then
  timeout 20 bash "$GLM_SCRAPE_SCRIPT" >/dev/null 2>&1
  if [[ -f "$GLM_QUOTA_FILE" ]]; then
    GLM_HAS_ERROR=$(python3 -c "import json; d=json.load(open('$GLM_QUOTA_FILE')); print('yes' if d.get('error') else 'no')" 2>/dev/null)
    if [[ "$GLM_HAS_ERROR" != "yes" ]]; then
      GLM_STATUS=$(python3 -c "import json; d=json.load(open('$GLM_QUOTA_FILE')); print(d.get('five_hour_quota_status',d.get('status','unknown')))" 2>/dev/null)
      GLM_USAGE=$(python3 -c "import json; d=json.load(open('$GLM_QUOTA_FILE')); print(d.get('five_hour_quota_pct','?'))" 2>/dev/null)
      case "$GLM_STATUS" in
        healthy|moderate) GLM_S="up" ;;
        warning) GLM_S="up" ;;
        exhausted) GLM_S="rate_limited" ;;
        session_expired) GLM_S="session_expired" ;;
        *) ;; # fall through to API ping
      esac
      [[ "$GLM_S" != "unknown" ]] && GLM_SCRAPED=true
    fi
  fi
fi

# Fallback: simple API ping (if scraper didn't produce a definitive result)
if [[ "$GLM_S" == "unknown" ]]; then
  GLM_KEY=$(cat ~/clawd/secrets/zai.key 2>/dev/null || echo "")
  if [[ -n "$GLM_KEY" ]]; then
    GLM_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
      -H "Authorization: Bearer $GLM_KEY" \
      -H "Content-Type: application/json" \
      -d '{"model":"glm-4.7","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
      "https://api.z.ai/api/coding/paas/v4/chat/completions" 2>/dev/null || echo "000")
    [[ "$GLM_CODE" == "200" ]] && GLM_S="up" || GLM_S="down"
  else
    GLM_S="no_key"
  fi
fi

# Test Kimi — use quota scraper if Camofox available, else fallback to ping
KIMI_QUOTA_FILE="$STATE_DIR/kimi-quota.json"
KIMI_S="unknown"
KIMI_USAGE=""
KIMI_RESET=""

# Try scrape-quota first (most accurate)
SCRAPE_SCRIPT="$(dirname "$0")/scrape-quota.sh"
KIMI_SCRAPED=false
if $CAMOFOX_BROWSER_OK && [[ -x "$SCRAPE_SCRIPT" ]]; then
  timeout 20 bash "$SCRAPE_SCRIPT" >/dev/null 2>&1
  if [[ -f "$KIMI_QUOTA_FILE" ]]; then
    KIMI_HAS_ERROR=$(python3 -c "import json; d=json.load(open('$KIMI_QUOTA_FILE')); print('yes' if d.get('error') else 'no')" 2>/dev/null)
    if [[ "$KIMI_HAS_ERROR" != "yes" ]]; then
      KIMI_STATUS=$(python3 -c "import json; d=json.load(open('$KIMI_QUOTA_FILE')); print(d.get('status','unknown'))" 2>/dev/null)
      KIMI_USAGE=$(python3 -c "import json; d=json.load(open('$KIMI_QUOTA_FILE')); print(d.get('weekly_usage_pct','?'))" 2>/dev/null)
      KIMI_RESET=$(python3 -c "import json; d=json.load(open('$KIMI_QUOTA_FILE')); print(d.get('weekly_reset_in','?'))" 2>/dev/null)
      case "$KIMI_STATUS" in
        healthy|moderate) KIMI_S="up" ;;
        warning) KIMI_S="up" ;;
        exhausted) KIMI_S="rate_limited" ;;
        session_expired) KIMI_S="session_expired" ;;
        *) ;; # fall through to API ping
      esac
      [[ "$KIMI_S" != "unknown" ]] && KIMI_SCRAPED=true
    fi
  fi
fi

# Fallback: simple API ping (if scraper didn't produce a definitive result)
if [[ "$KIMI_S" == "unknown" ]]; then
  KIMI_KEY=$(cat ~/clawd/secrets/kimi.key 2>/dev/null || echo "")
  if [[ -n "$KIMI_KEY" ]]; then
    KIMI_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
      -H "Authorization: Bearer $KIMI_KEY" \
      -H "Content-Type: application/json" \
      -H "User-Agent: KimiCLI/0.77" \
      -d '{"model":"kimi-for-coding","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
      "https://api.kimi.com/coding/v1/chat/completions" 2>/dev/null || echo "000")
    [[ "$KIMI_CODE" == "200" ]] && KIMI_S="up" || KIMI_S="rate_limited"
  else
    KIMI_S="no_key"
  fi
fi

# Test Anthropic — API ping + quota scraper
ANTHROPIC_QUOTA_FILE="$STATE_DIR/anthropic-quota.json"
ANTHROPIC_S="unknown"
ANTHROPIC_USAGE=""

# API ping — try gateway's own provider check (most reliable since gateway manages auth)
# The gateway handles Anthropic auth (Max subscription, no raw API key in config)
# So we ping the Anthropic API status endpoint + check gateway cooldown state
ANTHROPIC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
  "https://api.anthropic.com/v1/messages" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: dummy" \
  -d '{"model":"claude-sonnet-4-5-20250514","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
  2>/dev/null || echo "000")
# 401 = API reachable (auth failed as expected with dummy key, but service is up)
# 429 = rate limited, 529 = overloaded, 000 = unreachable
case "$ANTHROPIC_CODE" in
  200|401) ANTHROPIC_S="up" ;;  # 401 means API is reachable, our gateway handles real auth
  429) ANTHROPIC_S="rate_limited" ;;
  529) ANTHROPIC_S="overloaded" ;;
  000) ANTHROPIC_S="unreachable" ;;
  *) ANTHROPIC_S="down" ;;
esac

# Also check gateway logs for recent cooldown state
GATEWAY_LOG="/tmp/clawdbot/clawdbot.log"
if [[ -f "$GATEWAY_LOG" ]]; then
  RECENT_COOLDOWN=$(tail -200 "$GATEWAY_LOG" 2>/dev/null | grep -c "anthropic.*cooldown\|anthropic.*rate_limit" 2>/dev/null || true)
  RECENT_COOLDOWN="${RECENT_COOLDOWN:-0}"
  if [[ "$RECENT_COOLDOWN" -gt 3 && "$ANTHROPIC_S" == "up" ]]; then
    ANTHROPIC_S="rate_limited"
  fi
fi

# Also run quota scraper if Camofox available (enriches with session/weekly usage)
ANTHROPIC_SCRAPE="$(dirname "$0")/scrape-quota-anthropic.sh"
if $CAMOFOX_BROWSER_OK && [[ -x "$ANTHROPIC_SCRAPE" ]]; then
  timeout 20 bash "$ANTHROPIC_SCRAPE" >/dev/null 2>&1
fi
if [[ -f "$ANTHROPIC_QUOTA_FILE" ]]; then
  ANTHROPIC_USAGE=$(python3 -c "import json; d=json.load(open('$ANTHROPIC_QUOTA_FILE')); pct=d.get('weekly_all_models_pct'); print(f'weekly {pct}%' if pct else d.get('status_reason',''))" 2>/dev/null)
fi

# Check OpenAI — API ping + Codex usage scraper (chatgpt.com/codex/settings/usage)
OPENAI_QUOTA_FILE="$STATE_DIR/openai-quota.json"
OPENAI_CODEX_QUOTA_FILE="$STATE_DIR/openai-codex-quota.json"
OPENAI_S="unknown"
OPENAI_BALANCE=""
OPENAI_TIER=""
OPENAI_CODEX_USAGE=""

# API ping (fast check — is OpenAI API reachable?)
OPENAI_KEY="${OPENAI_API_KEY:-}"
if [[ -n "$OPENAI_KEY" ]]; then
  OPENAI_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
    -H "Authorization: Bearer $OPENAI_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
    "https://api.openai.com/v1/chat/completions" 2>/dev/null || echo "000")
  case "$OPENAI_CODE" in
    200) OPENAI_S="up" ;;
    429) OPENAI_S="rate_limited" ;;
    *) OPENAI_S="down" ;;
  esac
else
  OPENAI_S="no_key"
fi

# Run Codex usage scraper (chatgpt.com/codex/settings/usage) if Camofox available
OPENAI_CODEX_SCRAPE="$(dirname "$0")/scrape-quota-openai-codex.sh"
if $CAMOFOX_BROWSER_OK && [[ -x "$OPENAI_CODEX_SCRAPE" ]]; then
  timeout 20 bash "$OPENAI_CODEX_SCRAPE" >/dev/null 2>&1
fi

# Read Codex quota (primary — this is what matters for our usage)
if [[ -f "$OPENAI_CODEX_QUOTA_FILE" ]]; then
  OPENAI_CODEX_USAGE=$(python3 -c "
import json
d=json.load(open('$OPENAI_CODEX_QUOTA_FILE'))
parts=[]
h5=d.get('five_hour_remaining_pct')
wk=d.get('weekly_remaining_pct')
if h5 is not None: parts.append(f'5h: {h5}% left')
if wk is not None: parts.append(f'weekly: {wk}% left')
print(', '.join(parts) if parts else d.get('status_reason',''))
" 2>/dev/null)
  CODEX_STATUS=$(python3 -c "import json; print(json.load(open('$OPENAI_CODEX_QUOTA_FILE')).get('status','unknown'))" 2>/dev/null)
  # Override API status if Codex quota is exhausted
  if [[ "$CODEX_STATUS" == "exhausted" ]]; then
    OPENAI_S="rate_limited"
  fi
fi

# Also read platform billing (secondary — for credit balance info)
OPENAI_SCRAPE="$(dirname "$0")/scrape-quota-openai.sh"
if $CAMOFOX_BROWSER_OK && [[ -x "$OPENAI_SCRAPE" ]]; then
  timeout 20 bash "$OPENAI_SCRAPE" >/dev/null 2>&1
fi
if [[ -f "$OPENAI_QUOTA_FILE" ]]; then
  OPENAI_BALANCE=$(python3 -c "import json; d=json.load(open('$OPENAI_QUOTA_FILE')); print(f\"\${d.get('credit_balance',0):,.2f}\")" 2>/dev/null)
  OPENAI_TIER=$(python3 -c "import json; print(json.load(open('$OPENAI_QUOTA_FILE')).get('usage_tier','?'))" 2>/dev/null)
fi

# Count available (all 6 providers)
AVAILABLE=0
[[ "$MINIMAX_S" == "up" ]] && ((AVAILABLE++))
[[ "$GEMINI_S" == "up" ]] && ((AVAILABLE++))
[[ "$KIMI_S" == "up" ]] && ((AVAILABLE++))
[[ "$GLM_S" == "up" ]] && ((AVAILABLE++))
[[ "$ANTHROPIC_S" == "up" ]] && ((AVAILABLE++))
[[ "$OPENAI_S" == "up" ]] && ((AVAILABLE++))

# Determine best model for each tier
T1="" T2="" T3=""

# T1 (Simple/cheap): MiniMax > GLM > Kimi > Flash
if [[ "$MINIMAX_S" == "up" ]]; then T1="minimax/MiniMax-M2.5"
elif [[ "$GLM_S" == "up" ]]; then T1="zai/glm-4.7"
elif [[ "$KIMI_S" == "up" ]]; then T1="kimi-code/kimi-for-coding"
elif [[ "$GEMINI_S" == "up" ]]; then T1="google/gemini-3-flash-preview"
else T1="NONE"; fi

# T2 (Medium): GLM > Kimi > MiniMax > Flash
if [[ "$GLM_S" == "up" ]]; then T2="zai/glm-4.7"
elif [[ "$KIMI_S" == "up" ]]; then T2="kimi-code/kimi-for-coding"
elif [[ "$MINIMAX_S" == "up" ]]; then T2="minimax/MiniMax-M2.5"
elif [[ "$GEMINI_S" == "up" ]]; then T2="google/gemini-3-flash-preview"
else T2="NONE"; fi

# T3 (Complex): Anthropic > OpenAI > Gemini Flash (fallback)
if [[ "$ANTHROPIC_S" == "up" ]]; then T3="anthropic/claude-opus-4-6"
elif [[ "$OPENAI_S" == "up" ]]; then T3="openai/gpt-4o"
elif [[ "$GEMINI_S" == "up" ]]; then T3="google/gemini-3-flash-preview"
else T3="NONE"; fi

# Save status + detailed tracking registry
python3 - "${STATE_DIR:-}" "${NOW_ISO:-}" "${AVAILABLE:-}" "${MINIMAX_S:-}" "${GEMINI_S:-}" "${GLM_S:-}" "${KIMI_S:-}" "${ANTHROPIC_S:-}" "${OPENAI_S:-}" "${OPENAI_CODEX_USAGE:-}" "${T1:-}" "${T2:-}" "${T3:-}" "${MINIMAX_CODE:-}" "${GEMINI_CODE:-}" "${GLM_CODE:-}" "${GLM_USAGE:-}" "${KIMI_CODE:-}" "${KIMI_USAGE:-}" "${KIMI_RESET:-}" "${ANTHROPIC_CODE:-}" "${RECENT_COOLDOWN:-}" "${ANTHROPIC_USAGE:-}" "${OPENAI_CODE:-}" "${OPENAI_BALANCE:-}" "${OPENAI_TIER:-}" "${PROVIDER_STATUS:-}" "${PROVIDER_TRACKING:-}" << 'PYEOF'
import json
import sys
from pathlib import Path

(state_dir, now_iso, available, minimax_s, gemini_s, glm_s, kimi_s, anthropic_s, openai_s, openai_codex_usage, t1, t2, t3, minimax_code, gemini_code, glm_code, glm_usage, kimi_code, kimi_usage, kimi_reset, anthropic_code, recent_cooldown, anthropic_usage, openai_code, openai_balance, openai_tier, provider_status, provider_tracking) = sys.argv[1:]
available = int(available)
recent_cooldown = int(recent_cooldown or "0")

state_dir = Path(state_dir)
mode = "crisis" if available <= 2 else "degraded" if available <= 3 else "healthy"


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def classify_signal(status: str) -> str:
    if status == "up":
        return "live_check"
    if status in {"rate_limited", "overloaded"}:
        return "quota_or_provider_limit"
    if status in {"session_expired", "no_key"}:
        return "auth_or_config"
    if status in {"down", "unreachable"}:
        return "connectivity_or_provider_error"
    return "unknown"


def normalize_pct(value):
    if value in (None, "", "?", "unknown"):
        return None
    try:
        return float(str(value).replace('%', '').strip())
    except Exception:
        return None


def make_provider_entry(name, status, primary_model, tier_hint, quota_file=None, checks=None, notes=None, usage_summary=None):
    quota = load_json(quota_file) if quota_file else {}
    entry = {
        "provider": name,
        "status": status,
        "signal": classify_signal(status),
        "primary_model": primary_model,
        "tier_hint": tier_hint,
        "checked_at": now_iso,
        "stale_after_minutes": 180,
        "source": {
            "type": "check-providers.sh",
            "quota_file": str(quota_file) if quota_file else None,
        },
        "checks": checks or {},
        "quota": quota,
        "usage_summary": usage_summary,
        "notes": notes or [],
    }
    if status == "up":
        entry["last_success_at"] = now_iso
    else:
        entry["last_failure_at"] = now_iso
        entry["failure_reason"] = status
    return entry

status = {
    "minimax": minimax_s,
    "gemini_flash": gemini_s,
    "glm": glm_s,
    "kimi": kimi_s,
    "anthropic": anthropic_s,
    "openai": openai_s,
    "openai_codex_usage": openai_codex_usage,
    "available_count": available,
    "tier1_model": t1,
    "tier2_model": t2,
    "tier3_model": t3,
    "checked_at": now_iso,
    "mode": mode
}

tracking = {
    "schema_version": "2026-03-14.provider-tracking.v1",
    "checked_at": now_iso,
    "mode": mode,
    "available_count": available,
    "tier_assignments": {
        "t1": t1,
        "t2": t2,
        "t3": t3,
    },
    "providers": {
        "minimax": make_provider_entry(
            "minimax", minimax_s, "minimax/MiniMax-M2.5", "t1",
            quota_file=state_dir / "minimax-quota.json",
            checks={"http_status": minimax_code, "endpoint": "https://api.minimax.io/anthropic/v1/messages"},
            usage_summary=None,
        ),
        "gemini_flash": make_provider_entry(
            "gemini_flash", gemini_s, "google/gemini-3-flash-preview", "t1_t2",
            quota_file=state_dir / "gemini-quota.json",
            checks={"http_status": gemini_code, "endpoint": "generativelanguage.googleapis.com"},
            usage_summary=None,
        ),
        "glm": make_provider_entry(
            "glm", glm_s, "zai/glm-5", "t1_t2",
            quota_file=state_dir / "glm-quota.json",
            checks={"http_status": glm_code, "quota_pct_used": normalize_pct(glm_usage)},
            usage_summary=(f"5h quota: {glm_usage.strip()}% used" if glm_usage and glm_usage != "?" else None),
        ),
        "kimi": make_provider_entry(
            "kimi", kimi_s, "kimi-coding/kimi-for-coding", "t1_t2",
            quota_file=state_dir / "kimi-quota.json",
            checks={"http_status": kimi_code, "weekly_usage_pct": normalize_pct(kimi_usage), "reset_hint": kimi_reset},
            usage_summary=(f"weekly usage: {kimi_usage.strip()}%" if kimi_usage and kimi_usage != "?" else None),
        ),
        "anthropic": make_provider_entry(
            "anthropic", anthropic_s, "anthropic/claude-opus-4-6", "t3",
            quota_file=state_dir / "anthropic-quota.json",
            checks={"http_status": anthropic_code, "recent_cooldown_hits": recent_cooldown},
            usage_summary=(anthropic_usage or None),
        ),
        "openai": make_provider_entry(
            "openai", openai_s, "openai-codex/gpt-5.4", "t3",
            quota_file=state_dir / "openai-quota.json",
            checks={"http_status": openai_code, "api_balance": openai_balance, "usage_tier": openai_tier},
            usage_summary=(openai_codex_usage or None),
            notes=["Codex quota details mirrored in openai_codex entry"],
        ),
        "openai_codex": make_provider_entry(
            "openai_codex", openai_s, "openai-codex/gpt-5.4", "t3",
            quota_file=state_dir / "openai-codex-quota.json",
            checks={"http_status": openai_code},
            usage_summary=(openai_codex_usage or None),
        ),
        "hunter": {
            "provider": "hunter",
            "status": "configured",
            "signal": "manual_assignment_only",
            "primary_model": "hunter",
            "tier_hint": "t1_t2",
            "checked_at": now_iso,
            "stale_after_minutes": 180,
            "source": {"type": "manual_registry"},
            "notes": ["Temp model alias. Health currently inferred from cron performance, not direct provider check."],
        },
        "healer": {
            "provider": "healer",
            "status": "configured",
            "signal": "manual_assignment_only",
            "primary_model": "healer",
            "tier_hint": "t1_t2",
            "checked_at": now_iso,
            "stale_after_minutes": 180,
            "source": {"type": "manual_registry"},
            "notes": ["Temp model alias. Health currently inferred from cron performance, not direct provider check."],
        },
        "nemo": {
            "provider": "nemo",
            "status": "standby",
            "signal": "manual_assignment_only",
            "primary_model": "nemo",
            "tier_hint": "emergency_only",
            "checked_at": now_iso,
            "stale_after_minutes": 180,
            "source": {"type": "manual_registry"},
            "notes": ["Lowest-priority emergency fallback. Keep benched unless explicitly needed."],
        },
    },
}

with open(provider_status, "w") as f:
    json.dump(status, f, indent=2)
with open(provider_tracking, "w") as f:
    json.dump(tracking, f, indent=2)
PYEOF

# Output for cron agent to read
echo "PROVIDER_STATUS:"
echo "  Anthropic: $ANTHROPIC_S${ANTHROPIC_USAGE:+ (${ANTHROPIC_USAGE})}"
echo "  OpenAI: $OPENAI_S${OPENAI_CODEX_USAGE:+ (${OPENAI_CODEX_USAGE})}${OPENAI_BALANCE:+ [API balance: ${OPENAI_BALANCE}]}"
echo "  MiniMax: $MINIMAX_S"
echo "  Gemini Flash: $GEMINI_S"
echo "  GLM 4.7: $GLM_S${GLM_USAGE:+ (5h quota: ${GLM_USAGE}% used)}"
echo "  Kimi: $KIMI_S${KIMI_USAGE:+ (${KIMI_USAGE}% used, resets in ${KIMI_RESET})}"
echo "  Available: $AVAILABLE/6"
echo "  Mode: $(python3 -c "import json; print(json.load(open('$PROVIDER_STATUS'))['mode'])")"
echo "TIER_ASSIGNMENTS:"
echo "  T1 (Simple): $T1"
echo "  T2 (Medium): $T2"
echo "  T3 (Complex): $T3"
echo "TRACKING_FILE: $PROVIDER_TRACKING"
echo "ACTION: DISTRIBUTE"
