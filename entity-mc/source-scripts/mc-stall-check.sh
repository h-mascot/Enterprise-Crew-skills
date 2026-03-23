#!/bin/bash
# MC Stall Check — detect tasks stuck in "doing" or "review" too long
# Posts nag comments to Entity API for stalled doing items (>24h)
set -euo pipefail

MC_URL="http://<REDACTED_IP>:<PORT>"

TASKS=$(curl -s "$MC_URL/api/tasks" 2>/dev/null)
if [ -z "$TASKS" ]; then
  echo "ERROR: Could not reach MC at $MC_URL"
  exit 1
fi

NOW_MS=$(date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000)))

# Track state to avoid re-nagging within 2 hours
STATE_FILE="/tmp/mc-stall-nag-state.json"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

# Detect stalled doing items and post nag comments
RESULT=$(echo "$TASKS" | python3 -c "
import json, sys, os, time
from datetime import datetime

state_file = os.environ.get('STATE_FILE', '/tmp/mc-stall-nag-state.json')
now_ms = $NOW_MS
mc_url = '$MC_URL'

try:
    with open(state_file) as f:
        last_nagged = json.load(f)
except:
    last_nagged = {}

d = json.load(sys.stdin)
tasks = d.get('tasks', d) if isinstance(d, dict) else d
doing_stalled = []
review_stuck = []
to_nag = []

def ts_to_ms(ts_str):
    if not ts_str:
        return 0
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except:
        return 0

for t in tasks:
    updated_ms = t.get('updatedAtMs') or ts_to_ms(t.get('updated_at') or t.get('created_at'))
    if not updated_ms:
        updated_ms = t.get('createdAtMs', now_ms)
    hours = (now_ms - updated_ms) / 3600000
    tid = str(t['id'])

    if t.get('column') == 'doing' and hours > 12:
        assignee = t.get('assignee', '?')
        doing_stalled.append({'id': t['id'], 'name': t['name'], 'assignee': assignee, 'hours': hours})
        # Only nag if last nag was >2h ago
        last = last_nagged.get(tid, 0)
        if (now_ms - last) > 7200000:  # 2 hours
            to_nag.append(t)
    elif t.get('column') == 'review' and hours > 48:
        review_stuck.append({'id': t['id'], 'name': t['name'], 'hours': hours})

# Output nag targets as JSON
print(json.dumps({
    'doing_stalled': [{'id': t['id'], 'name': t['name'], 'assignee': t['assignee'], 'hours': round(t['hours'])} for t in doing_stalled],
    'review_stuck': [{'id': t['id'], 'name': t['name'], 'hours': round(t['hours'])} for t in review_stuck],
    'to_nag': [{'id': t['id'], 'name': t['name'], 'assignee': t.get('assignee', '?'), 'hours': round((now_ms - (t.get('updatedAtMs') or ts_to_ms(t.get('updated_at') or t.get('created_at')) or now_ms)) / 3600000)} for t in to_nag]
}))
" 2>/dev/null) || RESULT='{}'

# Parse results
DOING_STALLED=$(echo "$RESULT" | jq '.doing_stalled // []' 2>/dev/null)
REVIEW_STUCK=$(echo "$RESULT" | jq '.review_stuck // []' 2>/dev/null)
TO_NAG=$(echo "$RESULT" | jq '.to_nag // []' 2>/dev/null)

# Post nag comments for stalled doing items
NAGGED=0
echo "$TO_NAG" | jq -r '.[] | "\(.id)|\(.name)|\(.assignee)|\(.hours)"' 2>/dev/null | while IFS='|' read -r tid name assignee hours; do
  # Determine urgency message
  if [ "$hours" -gt 36 ]; then
    MSG="🔴 This task has been idle in Doing for ${hours}h (1.5+ days). Either finish it, move it to review with evidence, or archive it. Do not leave it rotting."
  elif [ "$hours" -gt 24 ]; then
    MSG="🟠 This task has been idle in Doing for ${hours}h. What's the blocker? If stuck, move to review with status or archive it."
  else
    MSG="🟡 This task has been idle in Doing for ${hours}h. Pick it up or move it to todo so someone else can."
  fi

  curl -s -X POST "$MC_URL/api/tasks/$tid/activity" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"task_comment\",\"action\":\"Doing-stall nag\",\"details\":\"$MSG\",\"agent_name\":\"Ada\",\"agent_emoji\":\"🔮\"}" > /dev/null 2>&1

  echo "  NAGGED #$tid: $name (idle ${hours}h)"
  NAGGED=$((NAGGED + 1))
done

# Update state file with nagged timestamps
echo "$TO_NAG" | jq -r '.[].id' 2>/dev/null | while read tid; do
  TS=$(echo "$RESULT" | jq -r ".to_nag[] | select(.id == $tid) | .hours" 2>/dev/null)
  # Use python to update JSON state
  python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        state = json.load(f)
except:
    state = {}
state['$tid'] = $NOW_MS
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f)
" 2>/dev/null
done

# Print summary
DOING_COUNT=$(echo "$DOING_STALLED" | jq 'length' 2>/dev/null || echo 0)
REVIEW_COUNT=$(echo "$REVIEW_STUCK" | jq 'length' 2>/dev/null || echo 0)

echo "=== MC STALL CHECK $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [ "$DOING_COUNT" -gt 0 ]; then
  echo "DOING STALLED ($DOING_COUNT):"
  echo "$DOING_STALLED" | jq -r '.[] | "  #\(.id): \(.name) [\(.assignee)] — \(.hours)h idle"' 2>/dev/null
fi

if [ "$REVIEW_COUNT" -gt 0 ]; then
  echo "REVIEW STUCK ($REVIEW_COUNT):"
  echo "$REVIEW_STUCK" | jq -r '.[] | "  #\(.id): \(.name) — \(.hours)h in review"' 2>/dev/null
fi

if [ "$DOING_COUNT" -eq 0 ] && [ "$REVIEW_COUNT" -eq 0 ]; then
  echo "OK: No stalled tasks"
fi

echo "=== END STALL CHECK ==="
