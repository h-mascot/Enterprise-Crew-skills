#!/usr/bin/env bash
# mc-assign-model.sh — Scan todo tasks without a model/skill, assign both based on benchmark-backed rules
# Called by Task Master cron or manually. Does NOT move tasks — only sets model + skill fields.
# Uses the decision tree from memory/task-model-guide.md
# Skill defaults from memory/task-skill-defaults.md
#
# Usage: bash mc-assign-model.sh [--dry-run]

set -euo pipefail

MC_API="http://<REDACTED_IP>:<PORT>/api/tasks"
DRY_RUN="${1:-}"

# Model aliases → full IDs
declare -A MODELS=(
  [opus]="anthropic/claude-opus-4-6"
  [codex]="openai-codex/gpt-5.4"
  [sonnet]="anthropic/claude-sonnet-4-6"
  [glm]="zai/glm-5-turbo"
  [minimax]="minimax/MiniMax-M2.7"
  [flash]="google/gemini-3-flash-preview"
  [hunter]="openrouter/openrouter/hunter-alpha"
)

# Fetch all todo tasks without a model OR without a skill in metadata
TASKS=$(curl -sf "$MC_API?limit=200" | jq -c '[.tasks[] | select(.column == "todo" and ((.model == null or .model == "") or (.metadata == null or .metadata == "" or ((.metadata | fromjson? // {}).skill // "") == "")))]')
COUNT=$(echo "$TASKS" | jq 'length')

if [ "$COUNT" -eq 0 ]; then
  echo '{"action":"no_unmodeled_tasks","count":0}'
  exit 0
fi

ASSIGNED=0
RESULTS="[]"

for i in $(seq 0 $((COUNT - 1))); do
  TASK=$(echo "$TASKS" | jq -c ".[$i]")
  ID=$(echo "$TASK" | jq -r '.id')
  NAME=$(echo "$TASK" | jq -r '.name // ""' | tr '[:upper:]' '[:lower:]')
  DESC=$(echo "$TASK" | jq -r '.description // ""' | tr '[:upper:]' '[:lower:]')
  PRIORITY=$(echo "$TASK" | jq -r '.priority // "P2"')
  ESTIMATE=$(echo "$TASK" | jq -r '.estimate_hours // 0')
  ASSIGNEE=$(echo "$TASK" | jq -r '.assignee // "Unassigned"')
  COMBINED="$NAME $DESC"

  MODEL=""
  SKILL=""
  REASON=""

  # Check if task already has model and skill
  EXISTING_MODEL=$(echo "$TASK" | jq -r '.model // ""')
  EXISTING_METADATA=$(echo "$TASK" | jq -r '.metadata // ""')
  EXISTING_SKILL=""
  if [ -n "$EXISTING_METADATA" ] && [ "$EXISTING_METADATA" != "null" ]; then
    EXISTING_SKILL=$(echo "$EXISTING_METADATA" | jq -r '.skill // ""' 2>/dev/null || true)
  fi

  # --- MODEL ASSIGNMENT ---
  if [ -n "$EXISTING_MODEL" ] && [ "$EXISTING_MODEL" != "null" ]; then
    MODEL="$EXISTING_MODEL"  # already has model, keep it
    REASON="existing"
  # 1. Explicit tag override in name/desc
  elif echo "$COMBINED" | grep -qiE '\[opus\]'; then
    MODEL="opus"; REASON="explicit_tag"
  elif echo "$COMBINED" | grep -qiE '\[codex\]|\[gpt\]'; then
    MODEL="codex"; REASON="explicit_tag"
  elif echo "$COMBINED" | grep -qiE '\[sonnet\]'; then
    MODEL="sonnet"; REASON="explicit_tag"
  elif echo "$COMBINED" | grep -qiE '\[glm\]'; then
    MODEL="glm"; REASON="explicit_tag"
  elif echo "$COMBINED" | grep -qiE '\[flash\]'; then
    MODEL="flash"; REASON="explicit_tag"
  elif echo "$COMBINED" | grep -qiE '\[minimax\]'; then
    MODEL="minimax"; REASON="explicit_tag"

  # 2. P1 or high-estimate → opus
  elif [ "$PRIORITY" = "P1" ] || [ "$(echo "$ESTIMATE" | awk '{print ($1 > 4)}')" = "1" ]; then
    MODEL="opus"; REASON="p1_or_high_estimate"

  # 3. Evaluate/benchmark/compare tasks → sonnet (research, not coding)
  elif echo "$COMBINED" | grep -qiE '^evaluate|^benchmark|^compare|^assess|^review.*alternative|^analyze.*competitor|deep dive|market scan|competitive analysis|audit.*vendor'; then
    MODEL="sonnet"; REASON="research_evaluation"

  # 4. Code tasks → codex + geordi-build-pipeline skill
  elif echo "$COMBINED" | grep -qiE 'build |fix bug|pull request|deploy |refactor|implement |pr review|feature |endpoint|api route|migration|test.*code|code.*review|merge conflict|coding|add.*route|add.*endpoint|create.*script|write.*script'; then
    MODEL="codex"; REASON="coding_task"

  # 5. Config/safety/infra → glm (safe, cheap, 95.0 suite v2)
  elif echo "$COMBINED" | grep -qiE 'config|gateway|cron |secret|security|firewall|ssl|cert|dns |infra |monitor setup|allowlist|permission|restore.*connect|set up.*node'; then
    MODEL="glm"; REASON="config_safety_task"

  # 6. Research/analysis/synthesis → sonnet
  elif echo "$COMBINED" | grep -qiE 'research|evaluat|benchmark|compare|analys|audit|investigate|review.*tool|review.*platform|assess'; then
    MODEL="sonnet"; REASON="research_analysis"

  # 7. Writing/content/blog/comms → sonnet
  elif echo "$COMBINED" | grep -qiE 'write |blog|article|post |publish|draft|copy |content |email.*draft|newsletter|document '; then
    MODEL="sonnet"; REASON="writing_content"

  # 8. Simple check/verify/list/cleanup/notify → glm (cheap all-rounder, not flash)
  elif echo "$COMBINED" | grep -qiE 'check |verify|list |count|cleanup|clean up|notify|reminder|ping |status |simple|dedupe|archive|delete.*old|remove.*stale'; then
    MODEL="glm"; REASON="simple_task"

  # 9. Default → glm (best value all-rounder per benchmarks, 95.0 suite v2)
  else
    MODEL="glm"; REASON="default_value_pick"
  fi

  # --- SKILL ASSIGNMENT ---
  # Mid-to-complex coding → always geordi-build-pipeline (Geordi pipeline)
  if [ -n "$EXISTING_SKILL" ] && [ "$EXISTING_SKILL" != "null" ]; then
    SKILL="$EXISTING_SKILL"  # keep existing
  elif echo "$COMBINED" | grep -qiE 'build |fix bug|pull request|deploy |refactor|implement |pr review|feature |endpoint|api route|migration|code.*review|merge conflict|coding|add.*route|add.*endpoint|create.*script|write.*script'; then
    SKILL="geordi-build-pipeline"
  elif echo "$COMBINED" | grep -qiE 'research|evaluat|benchmark|compare|analys|deep dive|market scan|competitive|investigate'; then
    SKILL="deep-research"
  elif echo "$COMBINED" | grep -qiE 'blog.*superada|publish.*superada|superada.*article|superada.*post'; then
    SKILL="superada-blog-publisher"
  elif echo "$COMBINED" | grep -qiE 'blog.*henry|publish.*henry|henrymascot.*article|henrymascot.*post'; then
    SKILL="henrymascot-blog-publisher"
  elif echo "$COMBINED" | grep -qiE 'github|pull request|pr |issue.*github|ci.*run'; then
    SKILL="github"
  elif echo "$COMBINED" | grep -qiE 'vercel|deploy.*vercel'; then
    SKILL="vercel-deploy"
  elif echo "$COMBINED" | grep -qiE 'spreadsheet|excel|csv|xlsx'; then
    SKILL="spreadsheet"
  elif echo "$COMBINED" | grep -qiE 'cloudflare|dns.*record|worker.*deploy'; then
    SKILL="cloudflare-api"
  elif echo "$COMBINED" | grep -qiE 'gmail|calendar|google doc|google sheet|drive '; then
    SKILL="gog"
  fi

  FULL_MODEL="${MODELS[$MODEL]:-$MODEL}"
  TASK_NAME=$(echo "$TASK" | jq -r '.name')

  if [ "$DRY_RUN" = "--dry-run" ]; then
    RESULTS=$(echo "$RESULTS" | jq --arg id "$ID" --arg name "$TASK_NAME" --arg model "$MODEL" --arg full "$FULL_MODEL" --arg reason "$REASON" --arg skill "$SKILL" \
      '. + [{"id": $id, "name": $name, "model": $model, "full_model": $full, "reason": $reason, "skill": $skill, "applied": false}]')
  else
    # Build patch payload — model + skill in metadata
    PATCH="{\"model\": \"$MODEL\""
    if [ -n "$SKILL" ]; then
      # Merge skill into existing metadata
      if [ -n "$EXISTING_METADATA" ] && [ "$EXISTING_METADATA" != "null" ] && [ "$EXISTING_METADATA" != "" ]; then
        NEW_META=$(echo "$EXISTING_METADATA" | jq -c --arg s "$SKILL" '.skill = $s' 2>/dev/null || echo "{\"skill\": \"$SKILL\"}")
      else
        NEW_META="{\"skill\": \"$SKILL\"}"
      fi
      ESCAPED_META=$(echo "$NEW_META" | sed 's/"/\\"/g')
      PATCH="$PATCH, \"metadata\": \"$ESCAPED_META\""
    fi
    PATCH="$PATCH}"

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X PATCH "$MC_API/$ID" \
      -H 'Content-Type: application/json' \
      -d "$PATCH")

    APPLIED=false
    if [ "$HTTP_CODE" = "200" ]; then
      APPLIED=true
      ASSIGNED=$((ASSIGNED + 1))
    fi

    RESULTS=$(echo "$RESULTS" | jq --arg id "$ID" --arg name "$TASK_NAME" --arg model "$MODEL" --arg full "$FULL_MODEL" --arg reason "$REASON" --arg skill "$SKILL" --argjson applied "$APPLIED" \
      '. + [{"id": $id, "name": $name, "model": $model, "full_model": $full, "reason": $reason, "skill": $skill, "applied": $applied}]')
  fi
done

# Output
jq -n --argjson count "$COUNT" --argjson assigned "$ASSIGNED" --argjson results "$RESULTS" --arg dry_run "$DRY_RUN" \
  '{"action":"model_assignment","unmodeled_tasks": $count, "assigned": $assigned, "dry_run": ($dry_run == "--dry-run"), "assignments": $results}'
