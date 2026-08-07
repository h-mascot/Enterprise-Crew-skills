#!/usr/bin/env bash
# Model Orchestrator - Intelligent cron model distributor
# Usage: orchestrate.sh [check|distribute|crisis|status]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$SCRIPT_DIR/../state"
TIERS_FILE="$STATE_DIR/cron-tiers.json"
PROVIDER_STATUS="$STATE_DIR/provider-status.json"
SWITCH_LOG="$STATE_DIR/switches.log"
GATEWAY="${OPENCLAW_GATEWAY_URL:-http://localhost:18789}"
GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
TIMEOUT=15

# Models
MINIMAX="minimax/MiniMax-M2.1"
FLASH="google/gemini-3-flash-preview"
KIMI="kimi-code/kimi-for-coding"
SONNET="anthropic/claude-sonnet-4-5"
OPUS="anthropic/claude-opus-4-6"

NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Tier -> provider preference (fallback order)
# T1: MiniMax > Flash > Kimi > (pause)
# T2: Flash > Kimi > MiniMax > (pause)
# T3: Opus > Sonnet > Kimi > Flash
declare -A TIER1_PREFS=( [0]="$MINIMAX" [1]="$FLASH" [2]="$KIMI" )
declare -A TIER2_PREFS=( [0]="$FLASH" [1]="$KIMI" [2]="$MINIMAX" )
declare -A TIER3_PREFS=( [0]="$OPUS" [1]="$SONNET" [2]="$KIMI" [3]="$FLASH" )

log_switch() {
  echo "$NOW_ISO | $1 | $2 -> $3 | $4" >> "$SWITCH_LOG"
}

# ============ CHECK PROVIDERS ============
check_providers() {
  echo "=== Checking Provider Health ==="
  
  local results=()
  
  # Test MiniMax
  echo -n "MiniMax... "
  MINIMAX_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
    -H "x-api-key: *** ${MINIMAX_API_KEY:-}" \
    -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"MiniMax-M2.1","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
    "https://api.minimax.io/anthropic/v1/messages" 2>/dev/null || echo "000")
  if [[ "$MINIMAX_CODE" == "200" ]]; then
    echo "UP"
    results+=("minimax:up")
  else
    echo "DOWN ($MINIMAX_CODE)"
    results+=("minimax:down:$MINIMAX_CODE")
  fi
  
  # Test Gemini Flash 3
  echo -n "Gemini Flash 3... "
  GEMINI_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key=${GEMINI_API_KEY:-}" \
    -H "Content-Type: application/json" \
    -d '{"contents":[{"parts":[{"text":"ping"}]}]}' 2>/dev/null || echo "000")
  if [[ "$GEMINI_CODE" == "200" ]]; then
    echo "UP"
    results+=("gemini-flash:up")
  elif [[ "$GEMINI_CODE" == "429" ]]; then
    echo "RATE LIMITED"
    results+=("gemini-flash:rate_limited")
  else
    echo "DOWN ($GEMINI_CODE)"
    results+=("gemini-flash:down:$GEMINI_CODE")
  fi
  
  # Test Kimi
  echo -n "Kimi... "
  KIMI_KEY=$(cat ~/clawd/secrets/kimi.key 2>/dev/null || echo "")
  if [[ -n "$KIMI_KEY" ]]; then
    KIMI_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT \
      -H "Authorization: Bearer $KIMI_KEY" \
      -H "Content-Type: application/json" \
      -H "User-Agent: KimiCLI/0.77" \
      -d '{"model":"kimi-for-coding","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
      "https://api.kimi.com/coding/v1/chat/completions" 2>/dev/null || echo "000")
    if [[ "$KIMI_CODE" == "200" ]]; then
      echo "UP"
      results+=("kimi:up")
    elif [[ "$KIMI_CODE" == "429" || "$KIMI_CODE" == "403" ]]; then
      echo "RATE LIMITED ($KIMI_CODE)"
      results+=("kimi:rate_limited:$KIMI_CODE")
    else
      echo "DOWN ($KIMI_CODE)"
      results+=("kimi:down:$KIMI_CODE")
    fi
  else
    echo "NO KEY"
    results+=("kimi:no_key")
  fi
  
  # Save status
  local minimax_s=$(echo "${results[0]}" | cut -d: -f2)
  local gemini_s=$(echo "${results[1]}" | cut -d: -f2)
  local kimi_s=$(echo "${results[2]}" | cut -d: -f2)
  
  python3 -c "
import json
status = {
  'minimax': {'status': '$minimax_s', 'model': '$MINIMAX'},
  'gemini-flash': {'status': '$gemini_s', 'model': '$FLASH'},
  'kimi': {'status': '$kimi_s', 'model': '$KIMI'},
  'opus': {'status': 'assumed_up', 'model': '$OPUS'},
  'sonnet': {'status': 'assumed_up', 'model': '$SONNET'},
  'lastCheck': '$NOW_ISO',
  'availableCount': sum(1 for s in ['$minimax_s','$gemini_s','$kimi_s'] if s == 'up')
}
json.dump(status, open('$PROVIDER_STATUS', 'w'), indent=2)
print(json.dumps(status, indent=2))
"
  
  # Count available
  local available=0
  [[ "$minimax_s" == "up" ]] && ((available++))
  [[ "$gemini_s" == "up" ]] && ((available++))
  [[ "$kimi_s" == "up" ]] && ((available++))
  
  echo ""
  echo "Available cheap providers: $available/3"
  
  if [[ $available -le 1 ]]; then
    echo "⚠️  CRISIS MODE — only $available cheap provider(s) available"
    return 0
  elif [[ $available -eq 2 ]]; then
    echo "🟡 DEGRADED — 1 provider down"
    return 0
  else
    echo "🟢 ALL HEALTHY"
    return 0
  fi
}

# ============ DISTRIBUTE CRONS ============
distribute() {
  echo "=== Distributing Crons Across Providers ==="
  
  # Read provider status
  if [[ ! -f "$PROVIDER_STATUS" ]]; then
    echo "No provider status. Run 'check' first."
    exit 1
  fi
  
  local minimax_up=$(python3 -c "import json; d=json.load(open('$PROVIDER_STATUS')); print(d['minimax']['status'])")
  local gemini_up=$(python3 -c "import json; d=json.load(open('$PROVIDER_STATUS')); print(d['gemini-flash']['status'])")
  local kimi_up=$(python3 -c "import json; d=json.load(open('$PROVIDER_STATUS')); print(d['kimi']['status'])")
  
  # Build available model list per tier
  local t1_model="" t2_model="" t3_model=""
  
  # T1: MiniMax > Flash > Kimi
  if [[ "$minimax_up" == "up" ]]; then t1_model="$MINIMAX"
  elif [[ "$gemini_up" == "up" ]]; then t1_model="$FLASH"
  elif [[ "$kimi_up" == "up" ]]; then t1_model="$KIMI"
  fi
  
  # T2: Flash > Kimi > MiniMax  
  if [[ "$gemini_up" == "up" ]]; then t2_model="$FLASH"
  elif [[ "$kimi_up" == "up" ]]; then t2_model="$KIMI"
  elif [[ "$minimax_up" == "up" ]]; then t2_model="$MINIMAX"
  fi
  
  # T3: keep Opus/Sonnet (always assumed available via Anthropic), fallback to Kimi > Flash
  t3_model="$OPUS"
  
  echo "T1 (Simple)  → $t1_model"
  echo "T2 (Medium)  → $t2_model"
  echo "T3 (Complex) → $t3_model"
  echo ""
  
  # Read cron tiers and update each one
  local updated=0
  local paused=0
  local cron_ids=$(python3 -c "import json; d=json.load(open('$TIERS_FILE')); print(' '.join(d['crons'].keys()))")
  
  for cron_id in $cron_ids; do
    local tier=$(python3 -c "import json; d=json.load(open('$TIERS_FILE')); print(d['crons']['$cron_id']['tier'])")
    local name=$(python3 -c "import json; d=json.load(open('$TIERS_FILE')); print(d['crons']['$cron_id']['name'])")
    local critical=$(python3 -c "import json; d=json.load(open('$TIERS_FILE')); print(d['crons']['$cron_id']['critical'])")
    
    local target_model=""
    case $tier in
      1) target_model="$t1_model" ;;
      2) target_model="$t2_model" ;;
      3) target_model="$t3_model" ;;
    esac
    
    # If no model available for this tier
    if [[ -z "$target_model" ]]; then
      if [[ "$critical" == "True" ]]; then
        # Critical crons get whatever is available
        if [[ "$minimax_up" == "up" ]]; then target_model="$MINIMAX"
        elif [[ "$gemini_up" == "up" ]]; then target_model="$FLASH"
        elif [[ "$kimi_up" == "up" ]]; then target_model="$KIMI"
        else target_model="$SONNET"  # last resort
        fi
        echo "⚠️  CRITICAL $name (T$tier) → forced to $target_model"
      else
        echo "⏸️  PAUSING $name (T$tier) — no suitable provider"
        # Disable the cron
        curl -s -X PATCH "$GATEWAY/api/cron/$cron_id" \
          -H "Authorization: Bearer $GATEWAY_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"enabled": false}' > /dev/null 2>&1 || true
        log_switch "$name" "enabled" "disabled" "No provider available for T$tier"
        ((paused++))
        continue
      fi
    fi
    
    # Get current model from the cron
    local current_model=$(curl -s "$GATEWAY/api/cron/$cron_id" \
      -H "Authorization: Bearer $GATEWAY_TOKEN" 2>/dev/null | \
      python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('payload',{}).get('model','unknown'))" 2>/dev/null || echo "unknown")
    
    if [[ "$current_model" != "$target_model" ]]; then
      echo "🔄 $name (T$tier): $current_model → $target_model"
      
      # Update the cron model via API
      local payload=$(curl -s "$GATEWAY/api/cron/$cron_id" \
        -H "Authorization: Bearer $GATEWAY_TOKEN" 2>/dev/null | \
        python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get('payload', {})
p['model'] = '$target_model'
print(json.dumps({'payload': p}))
" 2>/dev/null)
      
      if [[ -n "$payload" && "$payload" != "null" ]]; then
        curl -s -X PATCH "$GATEWAY/api/cron/$cron_id" \
          -H "Authorization: Bearer $GATEWAY_TOKEN" \
          -H "Content-Type: application/json" \
          -d "$payload" > /dev/null 2>&1 || echo "  ⚠️ Failed to update $name"
        log_switch "$name" "$current_model" "$target_model" "Orchestrator distribute"
        ((updated++))
      fi
    else
      echo "✅ $name (T$tier): already on $target_model"
    fi
  done
  
  echo ""
  echo "=== Distribution Complete ==="
  echo "Updated: $updated | Paused: $paused"
}

# ============ STATUS ============
show_status() {
  echo "=== Model Orchestrator Status ==="
  echo ""
  
  if [[ -f "$PROVIDER_STATUS" ]]; then
    echo "Provider Health:"
    python3 -c "
import json
d = json.load(open('$PROVIDER_STATUS'))
for k, v in d.items():
    if isinstance(v, dict) and 'status' in v:
        icon = '🟢' if v['status'] == 'up' or v['status'] == 'assumed_up' else '🟡' if v['status'] == 'rate_limited' else '🔴'
        print(f'  {icon} {k}: {v[\"status\"]}')
print(f'  Last check: {d.get(\"lastCheck\", \"never\")}')
"
  else
    echo "  No status yet. Run: orchestrate.sh check"
  fi
  
  echo ""
  echo "Cron Distribution:"
  python3 -c "
import json
d = json.load(open('$TIERS_FILE'))
tiers = {1: [], 2: [], 3: []}
for cid, info in d['crons'].items():
    tiers[info['tier']].append(info['name'])
for t in [1,2,3]:
    label = {1:'Simple',2:'Medium',3:'Complex'}[t]
    print(f'  T{t} ({label}): {len(tiers[t])} crons')
    for n in tiers[t][:5]:
        print(f'    - {n}')
    if len(tiers[t]) > 5:
        print(f'    ... and {len(tiers[t])-5} more')
"
  
  echo ""
  if [[ -f "$SWITCH_LOG" ]]; then
    echo "Recent switches (last 10):"
    tail -10 "$SWITCH_LOG" | while read line; do
      echo "  $line"
    done
  fi
}

# ============ MAIN ============
ACTION="${1:-status}"

case "$ACTION" in
  check)
    check_providers
    ;;
  distribute)
    check_providers
    echo ""
    distribute
    ;;
  crisis)
    echo "=== CRISIS MODE ==="
    check_providers
    echo ""
    distribute
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: orchestrate.sh [check|distribute|crisis|status]"
    exit 1
    ;;
esac
