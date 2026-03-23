#!/bin/bash
# MC Agent Health Check — monitors all agents, alerts Discord when one goes dark
# Usage: mc-health-check.sh
# Run via cron every 30 min: */30 * * * * bash ~/agent-workspace/scripts/mc-health-check.sh >> ~/.entity-mc/health.log 2>&1
set -euo pipefail

MC_URL="http://<REDACTED_IP>:<PORT>"
DISCORD_WEBHOOK="${MC_ESCALATOR_WEBHOOK:-}"
STATE_DIR="${HOME}/.entity-mc"
HEALTH_STATE="$STATE_DIR/health-state.json"

# Agent SSH targets
declare -A AGENT_HOSTS=(
  [Ada]="localhost"
  [Scotty]="jamify@<REDACTED_IP>"
  [Spock]="henrymascot@<REDACTED_IP>"
  [Book]="henrymascot@<REDACTED_IP>"
  [Zora]="henrymascot@<REDACTED_IP>"
)

declare -A AGENT_CRON_LOGS=(
  [Ada]="$HOME/agent-workspace/.entity-mc/cron.log"
  [Scotty]="/home/user/agent-workspace/.entity-mc/cron.log"
  [Spock]="/home/user/agent-workspace/.entity-mc/cron.log"
  [Book]="/Users/user/.hermes/.entity-mc/cron.log"
  [Zora]="/Users/user/.openclaw-zora/.entity-mc/cron.log"
)

mkdir -p "$STATE_DIR"
[ -f "$HEALTH_STATE" ] || echo '{}' > "$HEALTH_STATE"

NOW=$(date +%s)
ALERTS=""

for agent in Ada Scotty Spock Book Zora; do
  host="${AGENT_HOSTS[$agent]}"
  cron_log="${AGENT_CRON_LOGS[$agent]}"
  status="unknown"

  if [ "$host" = "localhost" ]; then
    # Local agent — check cron log freshness
    if [ -f "$cron_log" ]; then
      last_mod=$(stat -c%Y "$cron_log" 2>/dev/null || stat -f%m "$cron_log" 2>/dev/null || echo 0)
      age=$(( NOW - last_mod ))
      if [ "$age" -lt 900 ]; then
        status="healthy"
      else
        status="stale_cron_${age}s"
      fi
    else
      status="no_cron_log"
    fi
  else
    # Remote agent — SSH ping with tight timeout
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" "test -f $cron_log && stat -c%Y $cron_log 2>/dev/null || stat -f%m $cron_log 2>/dev/null" 2>/dev/null; then
      remote_mod=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" "stat -c%Y $cron_log 2>/dev/null || stat -f%m $cron_log 2>/dev/null" 2>/dev/null || echo 0)
      age=$(( NOW - remote_mod ))
      if [ "$age" -lt 900 ]; then
        status="healthy"
      else
        status="stale_cron_${age}s"
      fi
    else
      status="unreachable"
    fi
  fi

  # Check previous state
  prev_status=$(jq -r --arg a "$agent" '.[$a] // "unknown"' "$HEALTH_STATE" 2>/dev/null)

  # Alert on state transitions to unhealthy
  if [ "$status" != "healthy" ] && [ "$prev_status" = "healthy" ]; then
    ALERTS="${ALERTS}⚠️ **${agent}** went from healthy → ${status}\n"
  elif [ "$status" = "unreachable" ] && [ "$prev_status" = "unreachable" ]; then
    # Persistent unreachable — alert every 2 hours (4 checks)
    last_alert=$(jq -r --arg a "${agent}_last_alert" '.[$a] // 0' "$HEALTH_STATE" 2>/dev/null)
    if [ $(( NOW - last_alert )) -gt 7200 ]; then
      ALERTS="${ALERTS}🔴 **${agent}** still unreachable (persistent)\n"
      jq --arg a "${agent}_last_alert" --argjson t "$NOW" '.[$a] = $t' "$HEALTH_STATE" > "${HEALTH_STATE}.tmp" && mv "${HEALTH_STATE}.tmp" "$HEALTH_STATE"
    fi
  fi

  # Update state
  jq --arg a "$agent" --arg s "$status" '.[$a] = $s' "$HEALTH_STATE" > "${HEALTH_STATE}.tmp" && mv "${HEALTH_STATE}.tmp" "$HEALTH_STATE"

  echo "{\"agent\":\"$agent\",\"status\":\"$status\",\"ts\":\"$(date -Iseconds)\"}"
done

# Also check: board-level health
doing_count=$(curl -s --max-time 10 "$MC_URL/api/tasks?column=doing" 2>/dev/null | jq '.tasks | length' 2>/dev/null || echo -1)
todo_count=$(curl -s --max-time 10 "$MC_URL/api/tasks?column=todo" 2>/dev/null | jq '.tasks | length' 2>/dev/null || echo -1)

if [ "$todo_count" -eq 0 ] && [ "$doing_count" -eq 0 ]; then
  ALERTS="${ALERTS}📭 Conveyor belt empty: 0 todo, 0 doing. Agents have nothing to work on.\n"
fi

# Send Discord alert if anything needs attention
if [ -n "$ALERTS" ] && [ -n "$DISCORD_WEBHOOK" ]; then
  payload=$(jq -n --arg content "🏥 **Entity MC Health Check**\n\n${ALERTS}" '{content: $content}')
  curl -s -X POST "$DISCORD_WEBHOOK" -H "Content-Type: application/json" -d "$payload" >/dev/null 2>&1
  echo "{\"health_alert_sent\":true}"
elif [ -n "$ALERTS" ]; then
  echo "ALERTS (no webhook configured):"
  echo -e "$ALERTS"
fi
