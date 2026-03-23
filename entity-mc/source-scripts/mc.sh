#!/bin/bash
# Mission Control CLI helper
# Usage: mc.sh <command> [args]

MC_URL="http://<REDACTED_IP>:<PORT>"
USER="${MC_USER:-Ada}"

normalize_output_links() {
    local text="$1"
    local entity_base="${MC_URL%/}"
    local normalized="$text"

    normalized=$(printf '%s' "$normalized" | sed -E 's#https?://[^ )]+:8788/([^ )]+)#'"$entity_base"'/docs/\1#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#~/(clawd|clawd-spock|clawd-scotty)/output/([^ )]+)#'"$entity_base"'/docs/output/\2#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#~/(clawd|clawd-spock|clawd-scotty)/memory/([^ )]+)#'"$entity_base"'/docs/memory/\2#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#/home/(henrymascot|jamify)/(clawd|clawd-spock|clawd-scotty)/output/([^ )]+)#'"$entity_base"'/docs/output/\3#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#/home/(henrymascot|jamify)/(clawd|clawd-spock|clawd-scotty)/memory/([^ )]+)#'"$entity_base"'/docs/memory/\3#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#(^|[[:space:]])output/([^ )]+)#\1'"$entity_base"'/docs/output/\2#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#(^|[[:space:]])memory/([^ )]+)#\1'"$entity_base"'/docs/memory/\2#g')

    printf '%s' "$normalized"
}

normalize_output_links() {
    local val="$1"
    # Rewrite legacy docsify links to Entity docs links.
    val=$(echo "$val" | sed -E 's#https?://[^ ]+:8788/(output|memory|workspace)/#http://<REDACTED_IP>:<PORT>/docs/\1/#g')
    val=$(echo "$val" | sed -E 's#https?://[^ ]+:8788/#http://<REDACTED_IP>:<PORT>/docs/workspace/#g')
    echo "$val"
}

# Entity-accessible directories (relative to ~/agent-workspace)
ENTITY_ACCESSIBLE_DIRS="output memory workspace plans skills"

# Copy file to accessible location if needed, return Entity URL
ensure_accessible_output() {
    local filepath="$1"
    local workspace="${HOME}/clawd"
    local entity_base="http://<REDACTED_IP>:<PORT>/docs"
    
    # Expand ~ and resolve path
    filepath="${filepath/#\~/$HOME}"
    
    # If not a file path, return as-is
    if [[ ! "$filepath" =~ \.(html|md|txt|json|pdf|png|jpg)$ ]]; then
        echo "$filepath"
        return
    fi
    
    # If file doesn't exist, return as-is (let later validation catch it)
    if [ ! -f "$filepath" ]; then
        echo "$filepath"
        return
    fi
    
    # Check if already in an accessible directory
    for dir in $ENTITY_ACCESSIBLE_DIRS; do
        if [[ "$filepath" == *"/$dir/"* ]] || [[ "$filepath" == "$workspace/$dir/"* ]]; then
            # Already accessible - convert to Entity URL
            local relpath="${filepath#$workspace/}"
            echo "${entity_base}/${relpath}"
            return
        fi
    done
    
    # Not accessible - copy to output/ and return Entity URL
    local filename=$(basename "$filepath")
    local timestamp=$(date +%Y%m%d-%H%M%S)
    local dest_filename="${timestamp}-${filename}"
    local dest_path="${workspace}/output/${dest_filename}"
    
    cp "$filepath" "$dest_path" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "ℹ️  Copied to accessible location: output/${dest_filename}" >&2
        echo "${entity_base}/output/${dest_filename}"
    else
        echo "$filepath"
    fi
}

case "$1" in
    create|add|new)
        # mc.sh create "Task name" "Optional description" [--estimate hours] [--model model_id] [--skill skill_name] [--context file1,file2]
        NAME="$2"
        DESC="${3:-}"
        ESTIMATE_HOURS=""
        TASK_MODEL=""
        TASK_SKILL=""
        TASK_CONTEXT=""
        
        # Parse optional flags from all remaining args
        shift 2; shift 2>/dev/null || true  # skip name, desc
        while [ $# -gt 0 ]; do
            case "$1" in
                --estimate) ESTIMATE_HOURS="$2"; shift 2 ;;
                --model) TASK_MODEL="$2"; shift 2 ;;
                --skill) TASK_SKILL="$2"; shift 2 ;;
                --context) TASK_CONTEXT="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        
        # Check if explicit estimate provided
        if [ -n "$ESTIMATE_HOURS" ]; then
            : # already set from flags
        else
            # Auto-suggest estimate based on AI timeline baseline
            ESTIMATE_SCRIPT="$(dirname "$0")/estimate.sh"
            if [ -f "$ESTIMATE_SCRIPT" ]; then
                chmod +x "$ESTIMATE_SCRIPT" 2>/dev/null
                
                # Run estimation (capture output but don't block)
                echo ""
                echo "🔮 Running AI Timeline Estimation..."
                echo ""
                
                # Get estimates from description or name
                TASK_TEXT="${DESC:-$NAME}"
                "$ESTIMATE_SCRIPT" "$TASK_TEXT" 2>/dev/null
                
                # Extract P75 estimate for default
                P75_ESTIMATE=$("$ESTIMATE_SCRIPT" "$TASK_TEXT" 2>/dev/null | grep "Safe Estimate" | awk '{print $NF}' | sed 's/h$//' | sed 's/m$//')
                
                # Convert minutes to hours if needed
                if echo "$P75_ESTIMATE" | grep -q "m"; then
                    P75_ESTIMATE=$(echo "$P75_ESTIMATE" | awk '{print $1/60}')
                fi
                
                # Set default estimate to P75
                if [ -n "$P75_ESTIMATE" ]; then
                    ESTIMATE_HOURS="$P75_ESTIMATE"
                    echo ""
                    echo "✅ Auto-set estimate_hours: ${ESTIMATE_HOURS}h (P75 - Safe Estimate)"
                    echo ""
                fi
            fi
        fi
        
        # Build JSON payload
        PAYLOAD="{\"name\": \"$NAME\", \"description\": \"$DESC\", \"created_by\": \"$USER\", \"assignee\": \"$USER\", \"column\": \"todo\", \"actor\": \"$USER\""
        [ -n "$ESTIMATE_HOURS" ] && PAYLOAD="$PAYLOAD, \"estimate_hours\": $ESTIMATE_HOURS"
        [ -n "$TASK_MODEL" ] && PAYLOAD="$PAYLOAD, \"model\": \"$TASK_MODEL\""
        
        # Build metadata JSON if skill or context specified
        if [ -n "$TASK_SKILL" ] || [ -n "$TASK_CONTEXT" ]; then
            META="{"
            FIRST=true
            if [ -n "$TASK_SKILL" ]; then
                META="$META\"skill\": \"$TASK_SKILL\""
                FIRST=false
            fi
            if [ -n "$TASK_CONTEXT" ]; then
                $FIRST || META="$META, "
                # Convert comma-separated to JSON array
                CTX_ARRAY=$(echo "$TASK_CONTEXT" | sed 's/,/","/g')
                META="$META\"context\": [\"$CTX_ARRAY\"]"
            fi
            META="$META}"
            PAYLOAD="$PAYLOAD, \"metadata\": \"$(echo "$META" | sed 's/"/\\"/g')\""
        fi
        
        PAYLOAD="$PAYLOAD}"

        curl -s -X POST "$MC_URL/api/tasks" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "$PAYLOAD" | jq .
        ;;
    
    note|update)
        # mc.sh note <id> "Note text"
        ID="$2"
        NOTE="$3"
        curl -s -X POST "$MC_URL/api/tasks/$ID/comments" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"body\": \"$NOTE\", \"author\": \"$USER\"}" | jq .
        ;;
    
    move)
        # mc.sh move <id> <column>
        ID="$2"
        COLUMN="$3"
        if [ "$COLUMN" = "review" ]; then
            echo "❌ ERROR: Use 'mc.sh review <id> \"output\"' to move to review."
            echo "Output is MANDATORY — describe what was delivered."
            exit 1
        fi
        if [ "$COLUMN" = "done" ]; then
            echo "❌ ERROR: Use 'mc.sh done <id>' to move to done (must be in review first)."
            exit 1
        fi
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"column\": \"$COLUMN\", \"actor\": \"$USER\"}" | jq .
        ;;
    
    start)
        # mc.sh start <id> - move to doing
        ID="$2"
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"column\": \"doing\", \"actor\": \"$USER\"}" | jq .
        ;;
    
    review)
        # mc.sh review <id> <output> - move to review WITH MANDATORY OUTPUT
        ID="$2"
        OUTPUT="$3"
        if [ -z "$OUTPUT" ]; then
            echo "❌ ERROR: Output is MANDATORY to move to review."
            echo "Usage: mc.sh review <id> \"deliverable description/URL/file path\""
            echo ""
            echo "Output must be a concrete deliverable:"
            echo "  - Google Doc URL"
            echo "  - File path"
            echo "  - PR link"
            echo "  - Deployed URL"
            echo "  - Summary of what was produced"
            exit 1
        fi
        
        # Validate output is substantive (not just "done" or similar)
        OUTPUT_LENGTH=${#OUTPUT}
        if [ "$OUTPUT_LENGTH" -lt 50 ]; then
            echo "❌ ERROR: Output too short ($OUTPUT_LENGTH chars). Minimum 50 characters required."
            echo ""
            echo "Output must be a concrete deliverable description:"
            echo "  - What was built/created/delivered"
            echo "  - File path or URL to the output"
            echo "  - Summary of key findings/results"
            echo ""
            echo "Example: 'Built token dashboard at output/tokens.html. Shows daily costs across 5 models.'"
            exit 1
        fi
        
        # Check for low-effort outputs
        LOWER_OUTPUT=$(echo "$OUTPUT" | tr '[:upper:]' '[:lower:]')
        if [[ "$LOWER_OUTPUT" == "done" ]] || [[ "$LOWER_OUTPUT" == "completed" ]] || [[ "$LOWER_OUTPUT" == "finished" ]] || [[ "$LOWER_OUTPUT" == "n/a" ]]; then
            echo "❌ ERROR: '$OUTPUT' is not a valid output."
            echo "Describe what was actually delivered, not just 'done'."
            exit 1
        fi

        # Reject vague / inaccessible handwave references
        if echo "$LOWER_OUTPUT" | grep -Eq 'subagent output|see conversation|see chat|see above|shared in thread|full analysis elsewhere|details in notes|see notes|see thread'; then
            echo "❌ ERROR: Output references an inaccessible or vague artifact."
            echo ""
            echo "Do not use phrases like 'subagent output' or 'see conversation'."
            echo "Point to a real file, URL, PR, docs link, or other accessible deliverable."
            exit 1
        fi

        # Research/eval tasks require an accessible artifact reference in the review output
        TASK_NAME=$(curl -s "$MC_URL/api/tasks/$ID" | jq -r '.name // empty')
        TASK_DESC=$(curl -s "$MC_URL/api/tasks/$ID" | jq -r '.description // empty')
        TASK_CONTEXT=$(printf '%s %s' "$TASK_NAME" "$TASK_DESC" | tr '[:upper:]' '[:lower:]')
        if echo "$TASK_CONTEXT" | grep -Eq 'evaluate|analysis|analyze|compare|audit|research|investigate|benchmark'; then
            if ! echo "$OUTPUT" | grep -Eq '(https?://|output/[^ ]+|memory/[^ ]+|plans/[^ ]+|workspace/[^ ]+|skills/[^ ]+|~/[^ ]+|/home/[^ ]+|/Users/[^ ]+|PR #[0-9]+|pull request|commit [0-9a-f]{7,40})'; then
                echo "❌ ERROR: Research/eval tasks must include an accessible artifact in review output."
                echo ""
                echo "Include at least one of: file path, docs/output link, URL, PR, or commit reference."
                exit 1
            fi
        fi
        
        # Extract and validate file paths from output
        # Look for patterns like output/*, memory/*, ~/agent-workspace/*, /home/*/agent-workspace/*
        WORKSPACE="${HOME}/clawd"
        FILE_PATHS=$(echo "$OUTPUT" | grep -oE '(output|memory)/[^ )]+\.(md|txt|html|json|pdf|png|jpg)|~/agent-workspace/[^ )]+\.(md|txt|html|json|pdf|png|jpg)|/home/[^/]+/clawd[^/]*/[^ )]+\.(md|txt|html|json|pdf|png|jpg)')
        
        if [ -n "$FILE_PATHS" ]; then
            MISSING_FILES=""
            EMPTY_FILES=""
            while IFS= read -r fpath; do
                # Expand ~ and resolve paths
                if [[ "$fpath" == "output/"* ]] || [[ "$fpath" == "memory/"* ]]; then
                    full_path="${WORKSPACE}/${fpath}"
                elif [[ "$fpath" == "~/"* ]]; then
                    full_path="${fpath/#\~/$HOME}"
                else
                    full_path="$fpath"
                fi
                
                if [ ! -f "$full_path" ]; then
                    MISSING_FILES="${MISSING_FILES}\n  - $fpath"
                elif [ ! -s "$full_path" ]; then
                    EMPTY_FILES="${EMPTY_FILES}\n  - $fpath (0 bytes)"
                fi
            done <<< "$FILE_PATHS"
            
            if [ -n "$MISSING_FILES" ]; then
                echo "❌ ERROR: Output references non-existent file(s):"
                echo -e "$MISSING_FILES"
                echo ""
                echo "Create the file first, then move to review."
                exit 1
            fi
            
            if [ -n "$EMPTY_FILES" ]; then
                echo "❌ ERROR: Output references empty file(s):"
                echo -e "$EMPTY_FILES"
                echo ""
                echo "Files must have content. Write the deliverable first."
                exit 1
            fi
        fi
        
        # First ensure any file paths are in accessible locations (copies if needed)
        OUTPUT=$(ensure_accessible_output "$OUTPUT")
        
        # Then normalize links to Entity URLs
        NORMALIZED_OUTPUT=$(normalize_output_links "$OUTPUT")
        if [ "$NORMALIZED_OUTPUT" != "$OUTPUT" ]; then
            echo "ℹ️  Normalized output links to Entity /docs URLs"
            OUTPUT="$NORMALIZED_OUTPUT"
        fi

        # Verify any file paths in output actually exist
        FILE_REFS=$(echo "$OUTPUT" | grep -oE '(output/[^ "]+\.(md|txt|json|html|pdf)|~/[^ "]+\.(md|txt|json|html|pdf))' || true)
        if [ -n "$FILE_REFS" ]; then
            MISSING=""
            while IFS= read -r fref; do
                # Resolve relative paths against workspace
                if [[ "$fref" == ~/* ]]; then
                    RESOLVED="${fref/#\~/$HOME}"
                else
                    RESOLVED="$HOME/agent-workspace/$fref"
                fi
                if [ ! -f "$RESOLVED" ]; then
                    MISSING="${MISSING}  ⚠️  $fref → $RESOLVED (NOT FOUND)\n"
                fi
            done <<< "$FILE_REFS"
            if [ -n "$MISSING" ]; then
                echo "❌ ERROR: Output references files that don't exist:"
                echo -e "$MISSING"
                echo "Create the file(s) first, or remove the path from the output."
                exit 1
            fi
        fi

        # Set output FIRST, then move to review
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"output\": \"$OUTPUT\", \"column\": \"review\", \"actor\": \"$USER\"}" | jq .
        
        # Track estimation accuracy
        TRACKER="$(dirname "$0")/track-estimation-accuracy.sh"
        if [ -f "$TRACKER" ]; then
            chmod +x "$TRACKER" 2>/dev/null
            "$TRACKER" "$ID" 2>/dev/null
        fi
        ;;
    
    deliver)
        # mc.sh deliver <id> <output> - move directly from doing to done
        # Use when output was already delivered to Henry in live conversation.
        # Skips the review queue. Still requires substantive output description.
        ID="$2"
        OUTPUT="$3"
        if [ -z "$OUTPUT" ]; then
            echo "❌ ERROR: Output is MANDATORY for deliver."
            echo "Usage: mc.sh deliver <id> \"what was delivered and where\""
            exit 1
        fi
        OUTPUT_LENGTH=${#OUTPUT}
        if [ "$OUTPUT_LENGTH" -lt 30 ]; then
            echo "❌ ERROR: Output too short ($OUTPUT_LENGTH chars). Minimum 30 characters."
            exit 1
        fi
        LOWER_OUTPUT=$(echo "$OUTPUT" | tr '[:upper:]' '[:lower:]')
        if [[ "$LOWER_OUTPUT" == "done" ]] || [[ "$LOWER_OUTPUT" == "completed" ]] || [[ "$LOWER_OUTPUT" == "finished" ]]; then
            echo "❌ ERROR: Describe what was actually delivered."
            exit 1
        fi
        CURRENT=$(curl -s "$MC_URL/api/tasks/$ID" | jq -r '.column')
        if [ "$CURRENT" != "doing" ] && [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in 'doing' or 'review' to deliver."
            echo "Current column: $CURRENT"
            exit 1
        fi
        OUTPUT="$(normalize_output_links "$OUTPUT")"
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "$(jq -n --arg o "$OUTPUT" --arg u "$USER" '{column: "done", output: $o, actor: $u}')" | jq .
        echo "✅ Delivered directly to done (skipped review queue)"
        
        TRACKER="$(dirname "$0")/track-estimation-accuracy.sh"
        if [ -f "$TRACKER" ]; then
            chmod +x "$TRACKER" 2>/dev/null
            "$TRACKER" "$ID" 2>/dev/null
        fi
        ;;

    done)
        # mc.sh done <id> - move to done (only from review)
        ID="$2"
        # Check task is in review first
        CURRENT=$(curl -s "$MC_URL/api/tasks/$ID" | jq -r '.column')
        if [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in 'review' before moving to 'done'."
            echo "Current column: $CURRENT"
            echo "Move to review first: mc.sh review $ID \"deliverable\""
            exit 1
        fi
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"column\": \"done\", \"actor\": \"$USER\"}" | jq .
        
        # Track estimation accuracy
        TRACKER="$(dirname "$0")/track-estimation-accuracy.sh"
        if [ -f "$TRACKER" ]; then
            chmod +x "$TRACKER" 2>/dev/null
            "$TRACKER" "$ID" 2>/dev/null
        fi
        ;;
    
    output)
        # mc.sh output <id> "deliverable" - set output on a task
        ID="$2"
        OUTPUT="$3"
        OUTPUT="$(normalize_output_links "$OUTPUT")"
        if [ -z "$OUTPUT" ]; then
            echo "Usage: mc.sh output <id> \"deliverable\""
            exit 1
        fi
        NORMALIZED_OUTPUT=$(normalize_output_links "$OUTPUT")
        if [ "$NORMALIZED_OUTPUT" != "$OUTPUT" ]; then
            echo "ℹ️  Normalized output links to Entity /docs URLs"
            OUTPUT="$NORMALIZED_OUTPUT"
        fi
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"output\": \"$OUTPUT\", \"actor\": \"$USER\"}" | jq .
        ;;

    archive)
        # mc.sh archive <id> - archive a task (move to backlog + archived flag)
        ID="$2"
        curl -s -X PATCH "$MC_URL/api/tasks/$ID" \
            -H "Content-Type: application/json" \
            -H "X-Agent-Name: $USER" \
            -d "{\"column\": \"backlog\", \"archived\": 1, \"actor\": \"$USER\"}" | jq .
        ;;

    list|ls)
        # mc.sh list [column]
        COLUMN="${2:-}"
        if [ -n "$COLUMN" ]; then
            curl -s "$MC_URL/api/tasks" | jq ".tasks" | jq ".[] | select(.column == \"$COLUMN\") | {id, name, column, assignee, output}"
        else
            curl -s "$MC_URL/api/tasks" | jq ".tasks" | jq '.[] | {id, name, column, assignee}'
        fi
        ;;
    
    show|get)
        # mc.sh show <id>
        ID="$2"
        curl -s "$MC_URL/api/tasks/$ID" | jq .
        ;;
    
    progress|ip)
        # mc.sh progress - show doing tasks
        curl -s "$MC_URL/api/tasks" | jq ".tasks" | jq '.[] | select(.column == "doing") | {id, name, assignee}'
        ;;
    
    *)
        echo "Mission Control CLI"
        echo ""
        echo "Usage: mc.sh <command> [args]"
        echo ""
        echo "Commands:"
        echo "  create <name> [desc] [--estimate hours] [--model model_id] [--skill skill_name] [--context file1,file2]"
        echo "                        - Create new task (starts in todo, use 'start' to move to doing)"
        echo "                          Models: opus, sonnet, flash, codex, glm (or full model id)"
        echo "                        - Auto-suggests AI timeline estimate based on category"
        echo "                        - Shows ⚡ AI | 👤 Human | 🎯 Safe timelines"
        echo "  note <id> <text>      - Add activity note to task"
        echo "  move <id> <column>    - Move task (backlog/todo/doing/review/done)"
        echo "  start <id>            - Move to doing"
        echo "  review <id> <output>  - Move to review (OUTPUT REQUIRED, logs accuracy)"
        echo "  done <id>             - Move to done (must be in review, logs accuracy)"
        echo "  output <id> <text>    - Set output/deliverable on task"
        echo "  archive <id>          - Archive a dead task"
        echo "  list [column]         - List all tasks or by column"
        echo "  show <id>             - Show task details"
        echo "  progress              - Show doing tasks"
        echo ""
        echo "AI Timeline Estimation:"
        echo "  Baseline: ~/agent-workspace/output/agents/estimation-engine-baseline.md"
        echo "  Accuracy: ~/agent-workspace/output/agents/estimation-accuracy-log.md"
        echo "  Standalone: ~/agent-workspace/scripts/estimate.sh \"task description\""
        echo ""
        echo "Set MC_USER env var to change user (default: Ada)"
        ;;
esac
