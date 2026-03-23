#!/bin/bash
# MC Auto-Pull — generic task puller for any agent
# Usage: mc-auto-pull.sh [AGENT_NAME]
# Outputs JSON: pulled task info, skip reason, or error
# Includes tier-based model fallback per agent capability
set -euo pipefail

AGENT="${1:-Ada}"
MC_URL="http://<REDACTED_IP>:<PORT>"
MAX_DOING=10
CURL_MAX_TIME="${MC_CURL_MAX_TIME:-20}"
LOCK_DIR="${TMPDIR:-/tmp}"
AGENT_LOCK_KEY=$(printf '%s' "$AGENT" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
LOCK_FILE="$LOCK_DIR/mc-auto-pull-${AGENT_LOCK_KEY}.lock"

# Portable lock: use flock if available, fall back to mkdir
_lock_acquired=0
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    jq -n --arg agent "$AGENT" '{"action":"skip","reason":"already_running","agent":$agent}'
    exit 0
  fi
  _lock_acquired=1
else
  # mkdir is atomic on all POSIX systems
  LOCK_MKDIR="$LOCK_DIR/mc-auto-pull-${AGENT_LOCK_KEY}.lck"
  if ! mkdir "$LOCK_MKDIR" 2>/dev/null; then
    # Check for stale lock (>10 min old)
    if [ -d "$LOCK_MKDIR" ]; then
      _lock_age=$(( $(date +%s) - $(stat -f%m "$LOCK_MKDIR" 2>/dev/null || stat -c%Y "$LOCK_MKDIR" 2>/dev/null || echo 0) ))
      if [ "$_lock_age" -gt 600 ]; then
        rm -rf "$LOCK_MKDIR"
        mkdir "$LOCK_MKDIR" 2>/dev/null || true
      else
        jq -n --arg agent "$AGENT" '{"action":"skip","reason":"already_running","agent":$agent}'
        exit 0
      fi
    fi
  fi
  _lock_acquired=1
  trap 'rm -rf "$LOCK_MKDIR" 2>/dev/null' EXIT
fi

# ── Agent model inventories ──
# What each agent's gateway actually has available.
# Update when allowlists change. Source of truth: each gateway's openclaw.json
declare -A AGENT_MODELS
AGENT_MODELS[ada]="opus sonnet codex flash glm minimax hunter healer nemo open"
AGENT_MODELS[spock]="opus sonnet codex flash glm minimax hunter healer nemo open"
AGENT_MODELS[scotty]="opus sonnet codex flash glm minimax hunter healer nemo open"
AGENT_MODELS[zora]="flash glm hunter healer nemo open"
AGENT_MODELS[geordi]="codex opus sonnet flash glm"

# ── Tier-based fallback chains ──
# Same-capability tier: if primary unavailable, try next in chain
# Based on benchmark results (Suite v2 + messaging)
declare -A FALLBACK
FALLBACK[opus]="sonnet glm codex"                  # reasoning tier
FALLBACK[codex]="opus sonnet glm"                   # coding tier
FALLBACK[sonnet]="glm opus codex"                   # writing/research tier
FALLBACK[glm]="sonnet flash hunter"                 # value tier
FALLBACK[flash]="glm hunter open"                   # cheap tier
FALLBACK[minimax]="sonnet glm opus"                 # synthesis tier (never config)
FALLBACK[hunter]="healer nemo open flash glm"       # budget tier
FALLBACK[healer]="hunter nemo open flash glm"       # budget tier
FALLBACK[nemo]="hunter healer open flash glm"       # budget tier
FALLBACK[open]="hunter healer nemo flash glm"       # budget tier

# ── Alias resolution ──
resolve_alias() {
  case "$1" in
    opus)       echo "anthropic/claude-opus-4-6" ;;
    sonnet)     echo "anthropic/claude-sonnet-4-6" ;;
    flash)      echo "google/gemini-3-flash-preview" ;;
    codex|gpt)  echo "openai-codex/gpt-5.4" ;;
    glm)        echo "zai/glm-5-turbo" ;;
    minimax)    echo "minimax/MiniMax-M2.7" ;;
    hunter)     echo "openrouter/openrouter/hunter-alpha" ;;
    healer)     echo "openrouter/openrouter/healer-alpha" ;;
    nemo)       echo "openrouter/nvidia/nemotron-3-super-120b-a12b:free" ;;
    open)       echo "openrouter/openrouter/free" ;;
    *)          echo "$1" ;;
  esac
}

# ── Check if agent has a model ──
agent_has_model() {
  local agent_lower=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  local model_alias="$2"
  local available="${AGENT_MODELS[$agent_lower]:-}"
  
  # If agent not in inventory, assume it has everything (safe default)
  if [ -z "$available" ]; then
    return 0
  fi
  
  echo " $available " | grep -q " $model_alias "
}

# ── Pick best available model for agent ──
pick_model_for_agent() {
  local agent="$1"
  local preferred_alias="$2"
  
  # Try preferred first
  if agent_has_model "$agent" "$preferred_alias"; then
    echo "$preferred_alias"
    return
  fi
  
  # Try fallback chain
  local chain="${FALLBACK[$preferred_alias]:-}"
  for fallback in $chain; do
    if agent_has_model "$agent" "$fallback"; then
      echo "$fallback"
      return
    fi
  done
  
  # Last resort: glm (most agents have it)
  if agent_has_model "$agent" "glm"; then
    echo "glm"
    return
  fi
  
  # Absolute last resort: return preferred and let runtime handle it
  echo "$preferred_alias"
}

# ── Shared functions ──
EXEC_LOG="${ENTITY_MC_EXEC_LOG:-/tmp/mc-auto-exec-${AGENT_LOCK_KEY}.log}"
EXEC_RUNTIME="${ENTITY_MC_RUNTIME:-openclaw}"  # openclaw or hermes
SMALL_TASK_HOURS=1  # threshold for "small task" — pull another if under this

# Source NVM/profile once for node-based CLIs
for rc in "$HOME/.nvm/nvm.sh" "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  [ -f "$rc" ] && source "$rc" 2>/dev/null && break
done

select_model() {
  local task_model="$1" task_name="$2" task_desc="$3" task_priority="$4" task_estimate="$5"
  local PREFERRED=""

  if [ -n "$task_model" ] && [ "$task_model" != "null" ]; then
    case "$task_model" in
      anthropic/claude-opus-4-6)   PREFERRED="opus" ;;
      anthropic/claude-sonnet-4-6) PREFERRED="sonnet" ;;
      google/gemini-3-flash-preview) PREFERRED="flash" ;;
      openai-codex/gpt-5.4)       PREFERRED="codex" ;;
      zai/glm-5-turbo)            PREFERRED="glm" ;;
      minimax/MiniMax-M2.7)       PREFERRED="minimax" ;;
      opus|sonnet|flash|codex|gpt|glm|minimax|hunter|healer|nemo|open)
        PREFERRED="$task_model" ;;
      *)  PREFERRED="$task_model" ;;
    esac
  else
    PREFERRED="sonnet"
    local COMBINED="$task_name $task_desc"
    if [ "$task_priority" = "P1" ] || [ "${task_estimate:-0}" -gt 4 ] 2>/dev/null; then
      PREFERRED="opus"
    elif echo "$COMBINED" | grep -qiE 'build|implement|refactor|deploy|code|fix bug|PR|pull request'; then
      PREFERRED="codex"
    elif echo "$COMBINED" | grep -qiE 'check|monitor|verify|list|count|simple|cleanup|notify'; then
      PREFERRED="glm"
    fi
  fi

  local ACTUAL_ALIAS=$(pick_model_for_agent "$AGENT" "$PREFERRED")
  local MODEL=$(resolve_alias "$ACTUAL_ALIAS")
  local ORIGINAL_MODEL=$(resolve_alias "$PREFERRED")
  local FALLBACK_USED="false"
  [ "$ACTUAL_ALIAS" != "$PREFERRED" ] && FALLBACK_USED="true"

  echo "$MODEL|$ORIGINAL_MODEL|$FALLBACK_USED"
}

exec_task() {
  local task_id="$1" task_name="$2" task_desc="$3" task_priority="$4" task_estimate="$5" model="$6" task_skill="$7" context_block="${8:-}"

  if [ "${ENTITY_MC_NO_EXEC:-0}" = "1" ]; then
    return
  fi

  local SESSION_ID="mc-auto-${AGENT_LOCK_KEY}-${task_id}"
  local SCRIPTS_DIR="${ENTITY_MC_TARGET_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  local EXEC_PROMPT="## CRITICAL — Read this first
Your FINAL action before exiting MUST be one of:
  bash ${SCRIPTS_DIR}/mc.sh review ${task_id} \"<substantive output with file paths/evidence>\"
  bash ${SCRIPTS_DIR}/mc.sh note ${task_id} \"BLOCKED: <reason>\"
If you do neither, this task rots in 'doing' forever. There is no safety net. You MUST close the loop.
MC API: ${MC_URL}

---

You have been assigned Mission Control task #${task_id}: ${task_name}

Description:
${task_desc}

Priority: ${task_priority}
Estimated hours: ${task_estimate}
Model: ${model}"

  # Append rich context if available (memory, user model, rules, skills, project context)
  if [ -n "$context_block" ]; then
    EXEC_PROMPT+="

## Loaded Context
${context_block}"
  fi

  # ── Per-agent spawn prompt (loaded from state dir or manifest path) ──
  local AGENT_PROMPT_FILE="${ENTITY_MC_SPAWN_PROMPT:-}"
  # Default location: <state_dir>/spawn-prompt.md
  if [ -z "$AGENT_PROMPT_FILE" ]; then
    local STATE_DIR="${ENTITY_MC_STATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
    AGENT_PROMPT_FILE="${STATE_DIR}/spawn-prompt.md"
  fi
  if [ -f "$AGENT_PROMPT_FILE" ]; then
    EXEC_PROMPT+="

## Agent Instructions
$(cat "$AGENT_PROMPT_FILE")"
  fi

  # ── Per-task prompt (from task metadata.prompt field) ──
  local TASK_PROMPT="${9:-}"
  if [ -n "$TASK_PROMPT" ] && [ "$TASK_PROMPT" != "null" ]; then
    EXEC_PROMPT+="

## Task-Specific Instructions
${TASK_PROMPT}"
  fi

  EXEC_PROMPT+="

Instructions:
1. Read the task carefully and plan your approach.
2. If a skill is specified (${task_skill:-none}), load and follow it.
3. Do the work. Build, research, fix, or whatever the task requires.
4. When complete, move the task to review with substantive output using:
   bash ${SCRIPTS_DIR}/mc.sh review ${task_id} \"<your output summary with file paths/evidence>\"
5. If blocked, add a note: bash ${SCRIPTS_DIR}/mc.sh note ${task_id} \"BLOCKED: <reason>\"

Do NOT just describe what you would do. Actually do it.

## BLOCKER PROTOCOL
If you hit a blocker (missing credentials, access denied, missing dependencies, unclear requirements):
1. Search ALL memory files first — grep/read everything under memory/ (rules.md, tools-setup.md, tools-reference.md, TOOLS.md, agents-reference.md, user-model.md, learnings.md, and any other .md files). Also check secrets/ and .env files for credentials.
2. Check if a workaround exists before giving up
3. If the blocker is real and unresolvable:
   a. Post to Discord #mc-escalator (channel 1484312951510007949) with:
      - Task # and name
      - What you tried
      - Exact blocker
      - What you need from Henry to unblock
   b. Mark the task: bash ${SCRIPTS_DIR}/mc.sh note ${task_id} \"BLOCKED: <reason>\"
   c. Move task back to todo so it doesn't rot in doing
Do NOT silently fail. Do NOT leave a task in doing if you can't finish it.

## REMINDER — Your exit contract
Before you finish, you MUST run one of:
  bash ${SCRIPTS_DIR}/mc.sh review ${task_id} \"<output>\"
  bash ${SCRIPTS_DIR}/mc.sh note ${task_id} \"BLOCKED: <reason>\"
No exceptions. If you skip this, the task is orphaned."

  # Tracker dir for watchdog
  local WD_STATE_DIR="${ENTITY_MC_STATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
  local TRACKER_DIR="${WD_STATE_DIR}/exec-tracking"
  mkdir -p "$TRACKER_DIR" 2>/dev/null || true

  _write_tracker() {
    local _pid="$1"
    echo "{\"task_id\":\"${task_id}\",\"task_name\":\"${task_name}\",\"pid\":${_pid},\"session_id\":\"${SESSION_ID}\",\"started_epoch\":$(date +%s),\"runtime\":\"${EXEC_RUNTIME}\"}" \
      > "${TRACKER_DIR}/task-${task_id}.json" 2>/dev/null || true
  }

  if [ "$EXEC_RUNTIME" = "hermes" ]; then
    local HERMES_BIN="${ENTITY_MC_HERMES_BIN:-hermes}"
    if ! command -v "$HERMES_BIN" >/dev/null 2>&1; then
      for hpath in "$HOME/.local/bin/hermes" "/opt/homebrew/bin/hermes" "/usr/local/bin/hermes"; do
        [ -x "$hpath" ] && HERMES_BIN="$hpath" && break
      done
    fi
    if command -v "$HERMES_BIN" >/dev/null 2>&1 || [ -x "$HERMES_BIN" ]; then
      nohup "$HERMES_BIN" chat -q "$EXEC_PROMPT" --yolo >> "$EXEC_LOG" 2>&1 &
      _write_tracker $!
      echo "{\"exec\":\"spawned\",\"runtime\":\"hermes\",\"pid\":$!,\"session_id\":\"$SESSION_ID\"}"
    else
      echo '{"exec":"skipped","reason":"hermes_not_found"}'
    fi
  else
    local OPENCLAW_BIN="${ENTITY_MC_OPENCLAW_BIN:-openclaw}"
    if ! command -v "$OPENCLAW_BIN" >/dev/null 2>&1; then
      for opath in "/opt/homebrew/bin/openclaw" "/usr/local/bin/openclaw" "$HOME/.local/bin/openclaw" "$HOME/.local/share/pnpm/openclaw"; do
        [ -x "$opath" ] && OPENCLAW_BIN="$opath" && break
      done
    fi
    if command -v "$OPENCLAW_BIN" >/dev/null 2>&1 || [ -x "$OPENCLAW_BIN" ]; then
      nohup "$OPENCLAW_BIN" agent -m "$EXEC_PROMPT" --session-id "$SESSION_ID" --timeout 1800 --json >> "$EXEC_LOG" 2>&1 &
      _write_tracker $!
      echo "{\"exec\":\"spawned\",\"runtime\":\"openclaw\",\"pid\":$!,\"session_id\":\"$SESSION_ID\"}"
    else
      echo '{"exec":"skipped","reason":"openclaw_not_found"}'
    fi
  fi
}

pull_and_exec() {
  # $1 = skip count (how many oldest todos to skip, for multi-pull)
  local skip="${1:-0}"

  local NEXT_TASK=$(echo "$TASKS" | jq -c --arg agent "$AGENT" --argjson skip "$skip" \
    '[.tasks[]? // .[]? | select(.column == "todo" and (.assignee | ascii_downcase) == ($agent | ascii_downcase))] | sort_by(.created_at) | .[$skip] // empty' 2>/dev/null)

  if [ -z "$NEXT_TASK" ] || [ "$NEXT_TASK" = "null" ]; then
    return 1
  fi

  local TASK_ID=$(echo "$NEXT_TASK" | jq -r '.id')
  local TASK_NAME=$(echo "$NEXT_TASK" | jq -r '.name')
  local TASK_DESC=$(echo "$NEXT_TASK" | jq -r '.description // "No description"')
  local TASK_PRIORITY=$(echo "$NEXT_TASK" | jq -r '.priority // "P3"')
  local TASK_ESTIMATE=$(echo "$NEXT_TASK" | jq -r '.estimate_hours // 0')
  local TASK_MODEL=$(echo "$NEXT_TASK" | jq -r '.model // ""')
  local TASK_METADATA=$(echo "$NEXT_TASK" | jq -r '.metadata // ""')
  local TASK_SKILL="" TASK_CONTEXT="" TASK_PROMPT=""
  if [ -n "$TASK_METADATA" ] && [ "$TASK_METADATA" != "null" ] && [ "$TASK_METADATA" != "" ]; then
    TASK_SKILL=$(echo "$TASK_METADATA" | jq -r '.skill // ""' 2>/dev/null || true)
    TASK_CONTEXT=$(echo "$TASK_METADATA" | jq -r '(.context // []) | join(",")' 2>/dev/null || true)
    TASK_PROMPT=$(echo "$TASK_METADATA" | jq -r '.prompt // ""' 2>/dev/null || true)
  fi

  # Move to doing
  curl -s --max-time "$CURL_MAX_TIME" -X PATCH "$MC_URL/api/tasks/$TASK_ID" \
    -H "Content-Type: application/json" \
    -H "X-Agent-Name: $AGENT" \
    -d '{"column": "doing", "actor": "'"$AGENT"'"}' > /dev/null 2>&1

  # Select model
  local MODEL_INFO=$(select_model "$TASK_MODEL" "$TASK_NAME" "$TASK_DESC" "$TASK_PRIORITY" "$TASK_ESTIMATE")
  local MODEL=$(echo "$MODEL_INFO" | cut -d'|' -f1)
  local ORIGINAL_MODEL=$(echo "$MODEL_INFO" | cut -d'|' -f2)
  local FALLBACK_USED=$(echo "$MODEL_INFO" | cut -d'|' -f3)

  local PULL_JSON=$(jq -n --arg id "$TASK_ID" --arg name "$TASK_NAME" --arg desc "$TASK_DESC" \
    --arg agent "$AGENT" --arg priority "$TASK_PRIORITY" --arg estimate "$TASK_ESTIMATE" \
    --arg model "$MODEL" --arg preferred "$ORIGINAL_MODEL" --argjson fallback "$FALLBACK_USED" \
    --arg skill "$TASK_SKILL" --arg context "$TASK_CONTEXT" \
    '{"action":"pulled","task_id":$id,"task_name":$name,"task_description":$desc,"agent":$agent,"priority":$priority,"estimate_hours":$estimate,"model":$model,"preferred_model":$preferred,"fallback_used":$fallback,"skill":$skill,"context":$context,"status":"doing"}')
  echo "$PULL_JSON"

  # Build rich context via mc-build-context.sh (memory, user model, rules, skills, project context)
  local SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  local BUILD_CTX=""
  for candidate in "${SCRIPT_DIR}/mc-build-context.sh" "$(dirname "$0")/mc-build-context.sh"; do
    [ -f "$candidate" ] && BUILD_CTX="$candidate" && break
  done

  local CONTEXT_BLOCK=""
  if [ -n "$BUILD_CTX" ] && [ -f "$BUILD_CTX" ]; then
    CONTEXT_BLOCK=$(echo "$PULL_JSON" | bash "$BUILD_CTX" 2>/dev/null || true)
  fi

  # Execute with enriched context + per-task prompt
  exec_task "$TASK_ID" "$TASK_NAME" "$TASK_DESC" "$TASK_PRIORITY" "$TASK_ESTIMATE" "$MODEL" "$TASK_SKILL" "$CONTEXT_BLOCK" "$TASK_PROMPT"
  return 0
}

# ── Execution watchdog ──
# Before pulling new work, check if previous spawned executions died without closing the loop.
# Hard timeout: spend at most 30 seconds on watchdog, then move on to pulls.
WATCHDOG_MAX_SECS=30
WATCHDOG_MAX_AGE_SECS=3600  # only check processes that finished >60min ago as truly dead

watchdog_check() {
  local _wd_start=$(date +%s)
  local _wd_count=0

  # Find our exec tracking files
  local STATE_DIR="${ENTITY_MC_STATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
  local EXEC_TRACKER_DIR="${STATE_DIR}/exec-tracking"
  [ -d "$EXEC_TRACKER_DIR" ] || return 0

  for tracker in "$EXEC_TRACKER_DIR"/task-*.json; do
    [ -f "$tracker" ] || continue

    # Hard timeout: bail if watchdog has run too long
    local _wd_now=$(date +%s)
    if [ $((_wd_now - _wd_start)) -ge $WATCHDOG_MAX_SECS ]; then
      echo "{\"watchdog\":\"timeout\",\"checked\":$_wd_count}"
      return 0
    fi

    local t_id=$(jq -r '.task_id // empty' "$tracker" 2>/dev/null) || continue
    local t_pid=$(jq -r '.pid // empty' "$tracker" 2>/dev/null) || continue
    local t_started=$(jq -r '.started_epoch // 0' "$tracker" 2>/dev/null) || continue
    [ -z "$t_id" ] || [ -z "$t_pid" ] && continue

    _wd_count=$((_wd_count + 1))

    # Is the process still running?
    if kill -0 "$t_pid" 2>/dev/null; then
      # Still alive — check if it's been running too long (>45 min = zombie)
      local age=$(( $(date +%s) - t_started ))
      if [ "$age" -gt 2700 ]; then
        # Kill it, note the task
        kill "$t_pid" 2>/dev/null || true
        sleep 1
        kill -9 "$t_pid" 2>/dev/null || true
        curl -s --max-time 10 -X POST "$MC_URL/api/tasks/$t_id/activity" \
          -H "Content-Type: application/json" \
          -d "{\"type\":\"task_comment\",\"content\":\"[Watchdog] Execution killed after ${age}s — agent session exceeded 45min timeout. Task left in doing for manual review.\",\"actor\":\"$AGENT\"}" >/dev/null 2>&1
        echo "{\"watchdog\":\"killed_zombie\",\"task_id\":\"$t_id\",\"pid\":\"$t_pid\",\"age_secs\":$age}"
        rm -f "$tracker"
      fi
      continue
    fi

    # Process is dead — check if it forgot to move the task
    local task_status=$(curl -s --max-time 10 "$MC_URL/api/tasks/$t_id" 2>/dev/null | jq -r '.column // "unknown"' 2>/dev/null)

    if [ "$task_status" = "doing" ]; then
      # Dead process, task still in doing = orphaned
      curl -s --max-time 10 -X POST "$MC_URL/api/tasks/$t_id/activity" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"task_comment\",\"content\":\"[Watchdog] Agent execution exited without moving task to review. Process $t_pid is dead. Task needs manual completion or re-pull.\",\"actor\":\"$AGENT\"}" >/dev/null 2>&1
      # Move back to todo so it can be re-pulled
      curl -s --max-time 10 -X PATCH "$MC_URL/api/tasks/$t_id" \
        -H "Content-Type: application/json" \
        -d "{\"column\":\"todo\",\"actor\":\"$AGENT\"}" >/dev/null 2>&1
      echo "{\"watchdog\":\"orphan_recycled\",\"task_id\":\"$t_id\",\"pid\":\"$t_pid\",\"moved_to\":\"todo\"}"
    fi
    rm -f "$tracker"
  done

  [ $_wd_count -gt 0 ] && echo "{\"watchdog\":\"checked\",\"count\":$_wd_count}" || true
}

watchdog_check

# ── Board-level sweep (catches tasks without exec-tracking files) ──
# The tracker-based watchdog only sees tasks it spawned. Tasks pulled before
# exec-tracking existed, or whose tracker got lost, become invisible zombies
# clogging the doing column. This sweep queries the API directly.
board_sweep() {
  local _bs_start=$(date +%s)
  local _bs_swept=0
  local STATE_DIR="${ENTITY_MC_STATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"

  local doing_json=$(curl -s --max-time 15 "$MC_URL/api/tasks?column=doing" 2>/dev/null)
  [ -z "$doing_json" ] && return 0

  local agent_tasks=$(echo "$doing_json" | jq -r --arg agent "$AGENT" \
    '.tasks[] | select((.assignee | ascii_downcase) == ($agent | ascii_downcase)) | "\(.id)|\(.updated_at)"' 2>/dev/null)
  [ -z "$agent_tasks" ] && return 0

  while IFS='|' read -r task_id updated_at; do
    [ -z "$task_id" ] && continue

    # Skip if there's an active tracker (watchdog handles those)
    [ -f "${STATE_DIR}/exec-tracking/task-${task_id}.json" ] && continue

    # Skip if a process is still running for this task
    if pgrep -f "mc-auto-$(echo "$AGENT" | tr '[:upper:]' '[:lower:]')-${task_id}" >/dev/null 2>&1; then
      continue
    fi

    # No tracker, no process. Check staleness.
    local updated_epoch
    updated_epoch=$(date -d "${updated_at%%.*}" +%s 2>/dev/null || \
                    date -jf "%Y-%m-%dT%H:%M:%S" "${updated_at%%.*}" +%s 2>/dev/null || echo 0)
    local stale_secs=$(( $(date +%s) - updated_epoch ))

    # Stale > 2 hours with no process = sweep to todo
    if [ "$stale_secs" -gt 7200 ]; then
      curl -s --max-time 10 -X POST "$MC_URL/api/tasks/$task_id/activity" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"task_comment\",\"content\":\"[Board Sweep] Task in doing for $((stale_secs / 3600))h with no running process. Moving to todo for re-pull.\",\"actor\":\"$AGENT\"}" >/dev/null 2>&1
      curl -s --max-time 10 -X PUT "$MC_URL/api/tasks/$task_id" \
        -H "Content-Type: application/json" \
        -d '{"column":"todo"}' >/dev/null 2>&1
      echo "{\"board_sweep\":\"recycled\",\"task_id\":\"$task_id\",\"stale_secs\":$stale_secs}"
      _bs_swept=$((_bs_swept + 1))
    fi

    # Budget: max 30 seconds
    [ $(( $(date +%s) - _bs_start )) -ge 30 ] && break
  done <<< "$agent_tasks"

  [ $_bs_swept -gt 0 ] && echo "{\"board_sweep\":\"done\",\"swept\":$_bs_swept}" || true
}
board_sweep

# ── Main logic ──
TASKS=$(curl -s --max-time "$CURL_MAX_TIME" "$MC_URL/api/tasks" 2>/dev/null)
if [ -z "$TASKS" ]; then
  echo '{"error":"mc_unreachable"}'
  exit 1
fi

DOING_COUNT=$(echo "$TASKS" | jq --arg agent "$AGENT" \
  '[.tasks[]? // .[]? | select(.column == "doing" and (.assignee | ascii_downcase) == ($agent | ascii_downcase))] | length' 2>/dev/null || echo 0)

if [ "$DOING_COUNT" -ge "$MAX_DOING" ]; then
  echo '{"action":"skip","reason":"at_capacity","doing_count":'"$DOING_COUNT"',"agent":"'"$AGENT"'"}'
  exit 0
fi

TODO_COUNT=$(echo "$TASKS" | jq --arg agent "$AGENT" \
  '[.tasks[]? // .[]? | select(.column == "todo" and (.assignee | ascii_downcase) == ($agent | ascii_downcase))] | length' 2>/dev/null || echo 0)

if [ "$TODO_COUNT" -eq 0 ]; then
  # ── Auto-promote from backlog ──
  # If agent has no todo items, look for backlog tasks assigned to this agent
  # and promote the oldest one to todo, then continue with normal pull logic.
  BACKLOG_TASK=$(echo "$TASKS" | jq -c --arg agent "$AGENT" \
    '[.tasks[]? // .[]? | select(.column == "backlog" and .blocked != true and (.assignee | ascii_downcase) == ($agent | ascii_downcase))] | sort_by(.created_at) | .[0] // empty' 2>/dev/null)

  if [ -z "$BACKLOG_TASK" ] || [ "$BACKLOG_TASK" = "null" ]; then
    # No backlog tasks for this agent — try "Enterprise Crew" pool tasks
    BACKLOG_TASK=$(echo "$TASKS" | jq -c \
      '[.tasks[]? // .[]? | select(.column == "backlog" and .blocked != true and (.assignee | ascii_downcase) == "enterprise crew")] | sort_by(.priority, .created_at) | .[0] // empty' 2>/dev/null)

    if [ -z "$BACKLOG_TASK" ] || [ "$BACKLOG_TASK" = "null" ]; then
      # Still nothing — try fully unassigned backlog tasks
      BACKLOG_TASK=$(echo "$TASKS" | jq -c \
        '[.tasks[]? // .[]? | select(.column == "backlog" and .blocked != true and (.assignee == null or .assignee == "" or (.assignee | ascii_downcase) == "unassigned"))] | sort_by(.priority, .created_at) | .[0] // empty' 2>/dev/null)
    fi

    if [ -z "$BACKLOG_TASK" ] || [ "$BACKLOG_TASK" = "null" ]; then
      echo '{"action":"skip","reason":"no_todo_or_backlog_items","agent":"'"$AGENT"'"}'
      exit 0
    fi

    # Claim from pool (Enterprise Crew or unassigned)
    BL_ID=$(echo "$BACKLOG_TASK" | jq -r '.id')
    curl -s --max-time "$CURL_MAX_TIME" -X PATCH "$MC_URL/api/tasks/$BL_ID" \
      -H "Content-Type: application/json" \
      -d "{\"assignee\": \"$AGENT\"}" > /dev/null 2>&1
    echo "{\"backlog_promote\":\"claimed_unassigned\",\"task_id\":\"$BL_ID\",\"agent\":\"$AGENT\"}"
  fi

  BL_ID="${BL_ID:-$(echo "$BACKLOG_TASK" | jq -r '.id')}"
  BL_NAME=$(echo "$BACKLOG_TASK" | jq -r '.name // "unknown"')

  # Move backlog → todo
  curl -s --max-time "$CURL_MAX_TIME" -X PATCH "$MC_URL/api/tasks/$BL_ID" \
    -H "Content-Type: application/json" \
    -d "{\"column\": \"todo\", \"actor\": \"$AGENT\"}" > /dev/null 2>&1

  echo "{\"backlog_promote\":\"moved_to_todo\",\"task_id\":\"$BL_ID\",\"task_name\":$(echo "$BL_NAME" | jq -Rs .),\"agent\":\"$AGENT\"}"

  # Re-fetch tasks so pull_and_exec sees the newly promoted task
  TASKS=$(curl -s --max-time "$CURL_MAX_TIME" "$MC_URL/api/tasks" 2>/dev/null)
  TODO_COUNT=1
fi

# ── Pull #1 (always) ──
# Get the estimate of the first todo task before pulling (for smart-pull decision)
FIRST_ESTIMATE=$(echo "$TASKS" | jq -r --arg agent "$AGENT" \
  '[.tasks[]? // .[]? | select(.column == "todo" and (.assignee | ascii_downcase) == ($agent | ascii_downcase))] | sort_by(.created_at) | .[0].estimate_hours // 0' 2>/dev/null || echo "0")

pull_and_exec 0 3>/dev/null || {
  echo '{"action":"skip","reason":"no_todo_items","agent":"'"$AGENT"'"}'
  exit 0
}

# ── Smart pull: if first task is small (<1h estimate), pull one more ──
FIRST_EST_NUM=$(echo "$FIRST_ESTIMATE" | awk '{printf "%d", $1+0}')
NEW_DOING=$((DOING_COUNT + 1))

if [ "$FIRST_EST_NUM" -lt "$SMALL_TASK_HOURS" ] && [ "$NEW_DOING" -lt "$MAX_DOING" ] && [ "$TODO_COUNT" -gt 1 ]; then
  echo '{"smart_pull":"triggered","reason":"first_task_estimate_under_'${SMALL_TASK_HOURS}'h","first_estimate":"'"$FIRST_ESTIMATE"'"}'
  pull_and_exec 1 3>/dev/null || echo '{"smart_pull":"no_more_tasks"}'
fi
