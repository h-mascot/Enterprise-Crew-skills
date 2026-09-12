#!/bin/bash
# Mission Control CLI helper
# Usage: mc.sh <command> [args]

MC_URL="${ENTITY_MC_MC_URL:-${MC_URL:-http://localhost:3000}}"
USER="${MC_USER:-${ENTITY_MC_AGENT_NAME:-Agent}}"
ENTITY_MC_CONNECT_TIMEOUT="${ENTITY_MC_CONNECT_TIMEOUT:-5}"
ENTITY_MC_MAX_TIME="${ENTITY_MC_MAX_TIME:-30}"

require_option_value() {
    if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
        echo "❌ ERROR: $1 requires a value." >&2
        exit 1
    fi
}

read_task_or_fail() {
    local id="$1" response
    response=$(curl -fsS --connect-timeout "$ENTITY_MC_CONNECT_TIMEOUT" --max-time "$ENTITY_MC_MAX_TIME" "$MC_URL/api/tasks/$id") || return 1
    if ! printf '%s' "$response" | jq -e --arg id "$id" '
        type == "object" and (.error? == null) and ((.id | tostring) == $id)
        and (.column | IN("backlog", "todo", "doing", "review", "done", "archived", "cancelled"))
        and ((.metadata // {}) | if type == "string" then fromjson else . end
            | type == "object" and (.review_packet == null or (.review_packet | type) == "object"))
    ' >/dev/null 2>&1; then
        echo "❌ ERROR: Could not read a valid task contract for #$id; no task mutation attempted." >&2
        return 1
    fi
    printf '%s\n' "$response"
}

api_write_or_fail() {
    local method="$1"
    local request_path="$2"
    local payload="$3"
    local context="$4"
    local expected_column="${5:-}"
    local expected_blocked="${6:-}"
    local expected_output="${7:-}"
    local expected_archived="${8:-}"
    local expected_created_task="${9:-}"
    local response_file http_code curl_status response actual

    response_file="$(mktemp "${TMPDIR:-/tmp}/entity-mc-response.XXXXXX")" || {
        echo "❌ ERROR: Could not create a temporary response file." >&2
        exit 1
    }
    http_code="$(curl -sS \
        --connect-timeout "$ENTITY_MC_CONNECT_TIMEOUT" \
        --max-time "$ENTITY_MC_MAX_TIME" \
        -o "$response_file" \
        -w '%{http_code}' \
        -X "$method" "$MC_URL$request_path" \
        -H "Content-Type: application/json" \
        -H "X-Agent-Name: $USER" \
        --data "$payload")"
    curl_status=$?
    response="$(cat "$response_file")"
    rm -f "$response_file"

    if [ "$curl_status" -ne 0 ]; then
        echo "❌ ERROR: MC API transport failed while $context (curl exit $curl_status)." >&2
        exit 1
    fi
    if ! printf '%s' "$response" | jq -e . >/dev/null 2>&1; then
        [ -z "$response" ] || printf '%s\n' "$response" >&2
        echo "❌ ERROR: MC API returned non-JSON response while $context (HTTP $http_code)." >&2
        exit 1
    fi
    if ! printf '%s' "$http_code" | grep -Eq '^2[0-9][0-9]$'; then
        printf '%s\n' "$response" | jq . >&2
        echo "❌ ERROR: MC API rejected $context (HTTP $http_code)." >&2
        exit 1
    fi
    if ! printf '%s' "$response" | jq -e 'type == "object" and (.error? == null)' >/dev/null 2>&1; then
        printf '%s\n' "$response" | jq . >&2
        echo "❌ ERROR: MC API returned an error payload while $context." >&2
        exit 1
    fi
    if [[ "$request_path" =~ ^/api/tasks/([0-9]+)$ ]]; then
        if ! printf '%s' "$response" | jq -e --arg id "${BASH_REMATCH[1]}" '(.id | tostring) == $id' >/dev/null 2>&1; then
            echo "❌ ERROR: MC API returned a receipt for the wrong task while $context." >&2
            exit 1
        fi
    fi

    if [ -n "$expected_column" ]; then
        actual="$(printf '%s' "$response" | jq -r '.column // empty')"
        if [ "$actual" != "$expected_column" ]; then
            printf '%s\n' "$response" | jq . >&2
            echo "❌ ERROR: Task did not move to '$expected_column' while $context (current: ${actual:-unknown})." >&2
            exit 1
        fi
    fi
    if [ -n "$expected_blocked" ]; then
        actual="$(printf '%s' "$response" | jq -r '.blocked | if . == true then "true" elif . == false then "false" else "" end')"
        if [ "$actual" != "$expected_blocked" ]; then
            printf '%s\n' "$response" | jq . >&2
            echo "❌ ERROR: Task blocked state did not become '$expected_blocked' while $context." >&2
            exit 1
        fi
    fi
    if [ -n "$expected_output" ]; then
        actual="$(printf '%s' "$response" | jq -r '.output // empty')"
        if [ "$actual" != "$expected_output" ]; then
            printf '%s\n' "$response" | jq . >&2
            echo "❌ ERROR: Task output was not preserved while $context." >&2
            exit 1
        fi
    fi
    if [ -n "$expected_archived" ]; then
        if ! printf '%s' "$response" | jq -e '.archived == true or .archived == 1' >/dev/null 2>&1; then
            printf '%s\n' "$response" | jq . >&2
            echo "❌ ERROR: Task archived state was not confirmed while $context." >&2
            exit 1
        fi
    fi
    if [ -n "$expected_created_task" ]; then
        if ! printf '%s' "$response" | jq -e \
            'type == "object" and (.id | type == "number" and . > 0) and .column == "todo"' >/dev/null 2>&1; then
            printf '%s\n' "$response" | jq . >&2
            echo "❌ ERROR: MC API did not return a valid created task receipt while $context." >&2
            exit 1
        fi
    fi

    printf '%s\n' "$response" | jq .
}

validate_review_actor() {
    local task_json="$1"
    local expected_id="$2"
    local authorization
    authorization="$(printf '%s' "$task_json" | jq -r --arg actor "$USER" --arg expected_id "$expected_id" --arg humans "${ENTITY_MC_HUMAN_REVIEWERS:-}" '
      (.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end) as $m
      | ($actor | ascii_downcase) as $actor_lower
      | (($m.review_type // "peer") | tostring | ascii_downcase) as $review_type
      | (($m.reviewer // "") | tostring) as $reviewer
      | ($humans | split(",") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "") | ascii_downcase) | index($actor_lower) != null) as $human_actor
      | ([($m.submitted_by // ""), ($m.created_by // ""), (.assignee // "")]
          | map(tostring | ascii_downcase) | index($actor_lower) == null) as $independent
      | if ((.id | tostring) != $expected_id) or .column != "review" then "invalid_task"
        elif ($m.review_packet.requires_approval == true) or ($m.review_packet.requires_human_read == true) or ($m.human_gate_required == true) or ($m.requires_human == true) or ($review_type == "human") then
          if ($human_actor | not) then "human_gate_required" elif ($independent | not) then "reviewer_not_independent" else "ok" end
        elif $reviewer == "" then "reviewer_missing"
        elif (($reviewer | ascii_downcase) != $actor_lower) and ($human_actor | not) then "reviewer_mismatch"
        elif ($independent | not) then "reviewer_not_independent"
        else "ok" end
    ' 2>/dev/null)" || authorization="invalid_task"
    case "$authorization" in
        ok) return 0 ;;
        human_gate_required) echo "❌ ERROR: This human review requires an actor configured in ENTITY_MC_HUMAN_REVIEWERS." >&2 ;;
        reviewer_missing) echo "❌ ERROR: Review has no assigned reviewer; refusing to decide it." >&2 ;;
        reviewer_mismatch) echo "❌ ERROR: Only the assigned reviewer may decide this peer review." >&2 ;;
        reviewer_not_independent) echo "❌ ERROR: The assigned reviewer is not independent of the producer or assignee." >&2 ;;
        *) echo "❌ ERROR: Could not validate the task review contract." >&2 ;;
    esac
    exit 1
}

validate_peer_reviewer() {
    local task_json="$1" reviewer="$2" review_type="$3"
    [ "$review_type" = "peer" ] || return 0
    if ! printf '%s' "$task_json" | jq -e --arg reviewer "$reviewer" --arg actor "$USER" '
        (.metadata | if type == "string" then fromjson else (. // {}) end) as $m
        | ($reviewer | ascii_downcase) as $candidate
        | $candidate != "" and ([($actor // ""), (.assignee // ""), ($m.created_by // "")]
            | map(tostring | ascii_downcase) | index($candidate) == null)
    ' >/dev/null; then
        echo "❌ ERROR: Select an independent peer with --reviewer; the configured reviewer is a producer, submitter or creator." >&2
        exit 1
    fi
}

validate_review_submission() {
    local task_json="$1" option="${2:-}" expected="${3:-}" actual
    actual=$(printf '%s' "$task_json" | jq -r '(.metadata | if type == "string" then fromjson else (. // {}) end).review_submitted_at // "" | tostring')
    if [ -z "$option" ] && [ "${ENTITY_MC_REVIEW_SUBMISSION+x}" = x ]; then
        option="--submission"
        expected="$ENTITY_MC_REVIEW_SUBMISSION"
        set -- "$task_json" "$option" "$expected"
    fi
    if [ -z "$option" ]; then
        [ -n "$actual" ] || return 0
        echo "❌ ERROR: Supply --submission with the generation you inspected before deciding this review." >&2
        exit 1
    fi
    if [ "$option" != "--submission" ] || [ "$#" -lt 3 ]; then
        echo "❌ ERROR: Review decisions accept --submission <generation>." >&2
        exit 1
    fi
    if [ "$actual" != "$expected" ]; then
        echo "❌ ERROR: Review submission changed; inspect the current submission before deciding it." >&2
        exit 1
    fi
}

patch_task_or_fail() {
    local id="$1"
    local payload="$2"
    local expected_column="${3:-}"
    local expected_blocked="${4:-}"
    local expected_output="${5:-}"
    local expected_archived="${6:-}"
    api_write_or_fail PATCH "/api/tasks/$id" "$payload" "patching task #$id" \
        "$expected_column" "$expected_blocked" "$expected_output" "$expected_archived"
}

post_task_note_or_fail() {
    local id="$1"
    local note="$2"
    local payload
    payload="$(jq -cn --arg body "$note" --arg author "$USER" '{body: $body, author: $author}')"
    api_write_or_fail POST "/api/tasks/$id/comments" "$payload" "commenting on task #$id"
}

normalize_output_links() {
    local text="$1"
    local entity_base="${MC_URL%/}"
    local normalized="$text"

    # Rewrite legacy docsify links to Entity docs links.
    normalized=$(printf '%s' "$normalized" | sed -E 's#https?://[^ )]+:8788/(output|memory|workspace)/#'"$entity_base"'/docs/\1/#g')
    normalized=$(printf '%s' "$normalized" | sed -E 's#https?://[^ )]+:8788/#'"$entity_base"'/docs/workspace/#g')

    while IFS= read -r ref; do
        [ -z "$ref" ] && continue
        local mapped
        mapped="$(entity_docs_url_for_ref "$ref" 2>/dev/null || true)"
        [ -z "$mapped" ] && continue
        normalized=$(printf '%s' "$normalized" | ENTITY_MC_LOCAL_REF="$ref" ENTITY_MC_MAPPED_REF="$mapped" perl -0pe '
            s{(?<![A-Za-z0-9_/.-])\Q$ENV{ENTITY_MC_LOCAL_REF}\E(?![A-Za-z0-9_/.-])}{
                my $matched = $&;
                my $prefix = substr($_, 0, $-[0]);
                $prefix =~ m{https?://[^\s"<>)]*$} ? $matched : $ENV{ENTITY_MC_MAPPED_REF}
            }gex
        ')
    done < <(extract_file_refs "$normalized")

    printf '%s' "$normalized"
}

# Entity-accessible workspace directories
ENTITY_ACCESSIBLE_DIRS="output memory workspace plans skills"

extract_file_refs() {
    printf '%s\n' "$1" | perl -ne '
        while (/(?<![A-Za-z0-9_\/.-])((?:output|memory|docs|plans|skills|workspace)\/[^ )"<>]+\.(?:md|txt|html|json|pdf|png|jpg|jpeg)|~\/[^ )"<>]+\.(?:md|txt|html|json|pdf|png|jpg|jpeg)|\/home\/[^ )"<>]+\.(?:md|txt|html|json|pdf|png|jpg|jpeg)|\/Users\/[^ )"<>]+\.(?:md|txt|html|json|pdf|png|jpg|jpeg))/g) {
            next if $` =~ m{https?://[^\s"<>)]*$};
            $ref = $1;
            $ref =~ s/[.,;:)]$//;
            print "$ref\n";
        }
    ' | sort -u || true
}

current_entity_source() {
    printf '%s\n' "${ENTITY_MC_DOCS_SOURCE_ID:-workspace}"
}

workspace_root_for_ref() {
    local ref="$1"
    [ -n "${ENTITY_MC_TARGET_HOME:-}" ] && { echo "$ENTITY_MC_TARGET_HOME"; return; }
    [ -f "$PWD/$ref" ] && { echo "$PWD"; return; }
    for root in "$PWD" "${ENTITY_MC_TARGET_HOME:-$PWD}"; do
        [ -f "$root/$ref" ] && { echo "$root"; return; }
    done
    echo "$PWD"
}

local_path_for_ref() {
    local ref="$1"
    if [[ "$ref" == "~/"* ]]; then
        printf '%s\n' "${ref/#\~/$HOME}"
    elif [[ "$ref" = /* ]]; then
        printf '%s\n' "$ref"
    else
        printf '%s/%s\n' "$(workspace_root_for_ref "$ref")" "$ref"
    fi
}

entity_docs_url_for_ref() {
    local ref="$1"
    local entity_base="${MC_URL%/}"
    local path="$ref" source_id="" rel=""

    if [[ "$path" == "~/"* ]]; then
        path="${path/#\~/$HOME}"
    fi

    if [[ "$path" = /* ]]; then
        local root="${ENTITY_MC_TARGET_HOME:-$PWD}"
        [[ "$path" == "$root/"* ]] || return 1
        source_id="$(current_entity_source)"; rel="${path#"$root/"}"
    else
        source_id="$(current_entity_source)"
        rel="$path"
    fi

    rel="${rel// /%20}"
    printf '%s/docs/source/%s/%s\n' "$entity_base" "$source_id" "$rel"
}

entity_docs_ref_exists() {
    local ref="$1"
    local url
    url="$(entity_docs_url_for_ref "$ref" 2>/dev/null || true)"
    [ -n "$url" ] || return 1
    local api="${url/\/docs\//\/api\/docs\/}"
    local status
    status="$(curl -sS -o /dev/null -w '%{http_code}' "$api" 2>/dev/null || true)"
    [ "$status" = "200" ]
}

# Copy file to accessible location if needed, return Entity URL
ensure_accessible_output() {
    local filepath="$1"
    local workspace="${ENTITY_MC_TARGET_HOME:-$HOME}"
    local entity_base="${MC_URL%/}/docs"
    
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
            entity_docs_url_for_ref "$filepath" 2>/dev/null || echo "$filepath"
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
        entity_docs_url_for_ref "output/${dest_filename}" 2>/dev/null || echo "${entity_base}/output/${dest_filename}"
    else
        echo "$filepath"
    fi
}

default_review_type() {
    if [ "$1" = "high" ]; then echo "human"; else echo "peer"; fi
}

default_reviewer() {
    if [ "${2:-low}" = "high" ]; then
        printf '%s\n' "${ENTITY_MC_HUMAN_REVIEWERS%%,*}"
    else
        printf '%s\n' "${ENTITY_MC_DEFAULT_REVIEWER:-Reviewer}"
    fi
}

build_review_metadata() {
    local task_id="$1"
    local output="$2"
    local risk="$3"
    local reviewer="$4"
    local review_type="$5"
    local existing_metadata="$6"
    local task_name="$7"
    local submitted_by="${8:-$USER}"
    local proof_ref="${9:-}"
    local existing_json="${existing_metadata:-}"

    if ! printf '%s' "$existing_json" | jq -e . >/dev/null 2>&1; then
        existing_json='{}'
    fi

    jq -cn \
      --argjson existing "$existing_json" \
      --arg review_type "$review_type" \
      --arg reviewer "$reviewer" \
      --arg risk "$risk" \
      --arg submitted_by "$submitted_by" \
      --arg proof_ref "$proof_ref" \
      --arg outcome "Validate task #$task_id: $task_name" \
      --arg evidence "$output" \
      --arg artifact "$MC_URL/api/tasks/$task_id" '
        ($existing.review_packet | if type == "object" then . else {} end) as $packet
        | ($existing // {})
        + (if $proof_ref != "" then {proof_ref: $proof_ref} else {} end)
        + {
            review_type: $review_type,
            reviewer: $reviewer,
            human_gate_required: ($risk == "high" or $review_type == "human" or ($existing.human_gate_required // false) or ($existing.requires_human // false)),
            risk_level: $risk,
            submitted_by: $submitted_by,
            review_submitted_at: (now|tostring),
            review_decision: "pending",
            review_packet: ($packet + {
              requested_outcome: ($packet.requested_outcome // $outcome),
              output_artifact: $artifact,
              evidence: $evidence,
              done_criteria: ($packet.done_criteria // [
                "Reviewer can inspect the output artifact",
                "Reviewer can verify the stated evidence",
                "Reviewer can confirm risk level and completion"
              ]),
              risk_level: $risk,
              requires_approval: ($packet.requires_approval == true or $risk == "high"),
              requires_human_read: ($packet.requires_human_read == true or $risk == "high" or $review_type == "human" or ($existing.human_gate_required // false) or ($existing.requires_human // false)),
              external_risk: ($packet.external_risk // false)
            })
          }
      '
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
                --estimate) require_option_value "$@"; ESTIMATE_HOURS="$2"; shift 2 ;;
                --model) require_option_value "$@"; TASK_MODEL="$2"; shift 2 ;;
                --skill) require_option_value "$@"; TASK_SKILL="$2"; shift 2 ;;
                --context) require_option_value "$@"; TASK_CONTEXT="$2"; shift 2 ;;
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
        
        # Build metadata JSON if skill or context specified
        META=""
        if [ -n "$TASK_SKILL" ] || [ -n "$TASK_CONTEXT" ]; then
            META="$(jq -cn --arg skill "$TASK_SKILL" --arg context "$TASK_CONTEXT" '
              {}
              + (if $skill != "" then {skill: $skill} else {} end)
              + (if $context != "" then {context: ($context | split(",") | map(select(length > 0)))} else {} end)
            ')"
        fi
        if [ -z "$META" ]; then
            META="$(jq -cn --arg created_by "$USER" --arg reviewer "$(default_reviewer "$USER" low)" '{created_by:$created_by, submitted_by:$created_by, review_type:"peer", reviewer:$reviewer, human_gate_required:false}')"
        else
            META="$(jq -cn --argjson existing "$META" --arg created_by "$USER" --arg reviewer "$(default_reviewer "$USER" low)" '$existing + {created_by:$created_by, submitted_by:$created_by, review_type:($existing.review_type // "peer"), reviewer:($existing.reviewer // $reviewer), human_gate_required:($existing.human_gate_required // false)}')"
        fi

        # Build JSON payload with jq so names/descriptions containing quotes,
        # slashes, or newlines cannot corrupt the request body.
        PAYLOAD="$(jq -n \
            --arg name "$NAME" \
            --arg desc "$DESC" \
            --arg user "$USER" \
            --arg estimate "$ESTIMATE_HOURS" \
            --arg model "$TASK_MODEL" \
            --arg metadata "$META" \
            --arg humans "${ENTITY_MC_HUMAN_REVIEWERS:-}" '
              {
                name: $name,
                description: $desc,
                created_by: $user,
                created_by_principal_id: $user,
                initiator_principal_id: $user,
                initiator_type: (if ($humans | split(",") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "") | ascii_downcase) | index($user | ascii_downcase) != null) then "human" else "agent" end),
                owner_principal_id: $user,
                owner_principal_type: (if ($humans | split(",") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "") | ascii_downcase) | index($user | ascii_downcase) != null) then "human" else "agent" end),
                assignee: $user,
                column: "todo",
                actor: $user,
                metadata: $metadata
              }
              + (if $estimate != "" then {estimate_hours: ($estimate | tonumber)} else {} end)
              + (if $model != "" then {model: $model} else {} end)
            ')"

        api_write_or_fail POST "/api/tasks" "$PAYLOAD" "creating task" "" "" "" "" "task"
        ;;
    
    note|update)
        # mc.sh note <id> "Note text"
        ID="$2"
        NOTE="$3"
        post_task_note_or_fail "$ID" "$NOTE"
        ;;

    block)
        ID="$2"
        REASON="$3"
        if [ "${#REASON}" -lt 20 ]; then
            echo "❌ ERROR: State the blocker, recovery attempted, and required next action (20+ characters)." >&2
            exit 1
        fi
        post_task_note_or_fail "$ID" "BLOCKED: $REASON" >/dev/null
        patch_task_or_fail "$ID" "$(jq -n --arg reason "$REASON" --arg user "$USER" '{blocked:true, blocker_reason:$reason, actor:$user}')" "" "true"
        ;;

    unblock)
        # mc.sh unblock <id> "reason"
        ID="$2"
        REASON="${3:-UNBLOCKED: recovery path is available; resuming task.}"
        post_task_note_or_fail "$ID" "$REASON"
        patch_task_or_fail "$ID" "$(jq -n --arg u "$USER" '{blocked: false, blocker_reason: null, actor: $u}')" "" "false"
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
        patch_task_or_fail "$ID" \
            "$(jq -n --arg column "$COLUMN" --arg user "$USER" '{column: $column, actor: $user}')" \
            "$COLUMN"
        ;;
    
    start)
        # mc.sh start <id> - move to doing
        ID="$2"
        patch_task_or_fail "$ID" \
            "$(jq -n --arg user "$USER" '{column: "doing", actor: $user}')" \
            "doing"
        ;;
    
    review)
        # mc.sh review <id> <output> - move to review WITH MANDATORY OUTPUT
        ID="$2"
        OUTPUT="$3"
        REVIEW_RISK=""
        REVIEWER=""
        REVIEW_PROOF=""
        shift 3 2>/dev/null || true
        while [ $# -gt 0 ]; do
            case "$1" in
                --risk) require_option_value "$@"; REVIEW_RISK="$(echo "$2" | tr '[:upper:]' '[:lower:]')"; shift 2 ;;
                --reviewer) require_option_value "$@"; REVIEWER="$2"; shift 2 ;;
                --proof) require_option_value "$@"; REVIEW_PROOF="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        if [ -z "$OUTPUT" ]; then
            echo "❌ ERROR: Output is MANDATORY to move to review."
            echo "Usage: mc.sh review <id> \"deliverable description/URL/file path\" --risk low|medium|high [--reviewer NAME] [--proof ARTIFACT_REF]"
            echo ""
            echo "Output must be a concrete deliverable:"
            echo "  - Google Doc URL"
            echo "  - File path"
            echo "  - PR link"
            echo "  - Deployed URL"
            echo "  - Summary of what was produced"
            exit 1
        fi
        REVIEW_RISK="${REVIEW_RISK:-low}"
        if ! echo "$REVIEW_RISK" | grep -Eq '^(low|medium|high)$'; then
            echo "❌ ERROR: Review risk must be low, medium, or high."
            echo "Usage: mc.sh review <id> \"deliverable\" [--risk low|medium|high] [--reviewer NAME] [--proof ARTIFACT_REF]"
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
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        TASK_NAME=$(printf '%s' "$TASK_JSON" | jq -r '.name // empty')
        TASK_DESC=$(printf '%s' "$TASK_JSON" | jq -r '.description // empty')
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
        WORKSPACE="${ENTITY_MC_TARGET_HOME:-$HOME}"
        FILE_PATHS=$(extract_file_refs "$OUTPUT")
        
        if [ -n "$FILE_PATHS" ]; then
            MISSING_FILES=""
            EMPTY_FILES=""
            while IFS= read -r fpath; do
                full_path="$(local_path_for_ref "$fpath")"
                
                if [ ! -f "$full_path" ]; then
                    if ! entity_docs_ref_exists "$fpath"; then
                        MISSING_FILES="${MISSING_FILES}\n  - $fpath"
                    fi
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
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        TASK_NAME=$(echo "$TASK_JSON" | jq -r '.name // empty')
        TASK_DESC=$(echo "$TASK_JSON" | jq -r '.description // empty')
        TASK_ASSIGNEE=$(printf '%s' "$TASK_JSON" | jq -r --arg actor "$USER" '.assignee // $actor')
        EXISTING_METADATA=$(echo "$TASK_JSON" | jq -c '(.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end)')
        EXISTING_HUMAN_GATE=$(printf '%s' "$EXISTING_METADATA" | jq -r --arg humans "${ENTITY_MC_HUMAN_REVIEWERS:-}" '
          . as $m | ($humans | split(",") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "") | ascii_downcase)) as $human_names
          | ($m.review_packet.requires_approval == true) or ($m.review_packet.requires_human_read == true) or ($m.human_gate_required == true) or ($m.requires_human == true) or ((($m.review_type // "") | ascii_downcase) == "human")
            or (($m.reviewer // "" | ascii_downcase) as $reviewer | $reviewer != "" and ($human_names | index($reviewer) != null))
        ')
        [ "$EXISTING_HUMAN_GATE" != "true" ] || REVIEW_RISK="high"
        EXISTING_REVIEWER=$(printf '%s' "$EXISTING_METADATA" | jq -r '.reviewer // empty')
        REVIEWER="${REVIEWER:-${EXISTING_REVIEWER:-$(default_reviewer "$TASK_ASSIGNEE" "$REVIEW_RISK")}}"
        REVIEW_TYPE="$(default_review_type "$REVIEW_RISK")"
        validate_peer_reviewer "$TASK_JSON" "$REVIEWER" "$REVIEW_TYPE"
        REVIEW_METADATA=$(build_review_metadata "$ID" "$OUTPUT" "$REVIEW_RISK" "$REVIEWER" "$REVIEW_TYPE" "$EXISTING_METADATA" "$TASK_NAME" "$USER" "$REVIEW_PROOF")

        # Verify any file paths in output actually exist
        FILE_REFS=$(extract_file_refs "$OUTPUT")
        if [ -n "$FILE_REFS" ]; then
            MISSING=""
            while IFS= read -r fref; do
                RESOLVED="$(local_path_for_ref "$fref")"
                if [ ! -f "$RESOLVED" ] && ! entity_docs_ref_exists "$fref"; then
                    MISSING="${MISSING}  ⚠️  $fref → $RESOLVED or Entity FS (NOT FOUND)\n"
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
        patch_task_or_fail "$ID" "$(jq -n --arg o "$OUTPUT" --arg u "$USER" --arg metadata "$REVIEW_METADATA" '{output: $o, column: "review", actor: $u, metadata: $metadata}')" "review" "" "$OUTPUT"
        
        # Track estimation accuracy
        TRACKER="$(dirname "$0")/track-estimation-accuracy.sh"
        if [ -f "$TRACKER" ]; then
            chmod +x "$TRACKER" 2>/dev/null
            "$TRACKER" "$ID" 2>/dev/null
        fi
        ;;
    
    deliver)
        # mc.sh deliver <id> <output> - move directly from doing to done
        # Use when output was already delivered to the user in live conversation.
        # Skips the review queue. Still requires substantive output description.
        ID="$2"
        OUTPUT="$3"
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        if ! printf '%s' "$TASK_JSON" | jq -e --arg id "$ID" 'type == "object" and (.id | tostring) == $id' >/dev/null; then
            echo "❌ ERROR: Could not validate the task before delivery." >&2
            exit 1
        fi
        if printf '%s' "$TASK_JSON" | jq -e --arg humans "${ENTITY_MC_HUMAN_REVIEWERS:-}" '
            (.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end) as $m
            | ($humans | split(",") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "") | ascii_downcase)) as $human_names
            | ($m.review_packet.requires_approval == true) or ($m.review_packet.requires_human_read == true) or ($m.human_gate_required == true) or ($m.requires_human == true) or ((($m.review_type // "") | ascii_downcase) == "human")
              or (($m.reviewer // "" | ascii_downcase) as $reviewer | $reviewer != "" and ($human_names | index($reviewer) != null))
        ' >/dev/null; then
            echo "❌ ERROR: Human-gated work must be decided through accept-review; deliver cannot bypass the gate." >&2
            exit 1
        fi
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
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        CURRENT=$(printf '%s' "$TASK_JSON" | jq -r '.column')
        if [ "$CURRENT" != "doing" ] && [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in 'doing' or 'review' to deliver."
            echo "Current column: $CURRENT"
            exit 1
        fi
        OUTPUT="$(normalize_output_links "$OUTPUT")"
        patch_task_or_fail "$ID" "$(jq -n --arg o "$OUTPUT" --arg u "$USER" '{column: "done", output: $o, actor: $u}')" "done" "" "$OUTPUT"
        echo "✅ Delivered directly to done (skipped review queue)"
        
        TRACKER="$(dirname "$0")/track-estimation-accuracy.sh"
        if [ -f "$TRACKER" ]; then
            chmod +x "$TRACKER" 2>/dev/null
            "$TRACKER" "$ID" 2>/dev/null
        fi
        ;;

    accept-review)
        # mc.sh accept-review <id> "review note" - record reviewer acceptance and move to done
        ID="$2"
        NOTE="$3"
        if [ -z "$NOTE" ] || [ ${#NOTE} -lt 20 ]; then
            echo "❌ ERROR: accept-review requires a substantive review note (20+ chars)."
            echo "Usage: mc.sh accept-review <id> \"what you verified\" --submission GENERATION"
            exit 1
        fi
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        validate_review_actor "$TASK_JSON" "$ID"
        if [ "$#" -gt 3 ]; then validate_review_submission "$TASK_JSON" "${4:-}" ${5+"$5"}; else validate_review_submission "$TASK_JSON"; fi
        CURRENT=$(echo "$TASK_JSON" | jq -r '.column')
        if [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in review before accept-review."
            echo "Current column: $CURRENT"
            exit 1
        fi
        EXISTING_METADATA=$(echo "$TASK_JSON" | jq -c '(.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end)')
        REVIEW_METADATA=$(jq -cn \
            --argjson existing "$EXISTING_METADATA" \
            --arg user "$USER" \
            --arg note "$NOTE" \
            '$existing + {reviewed_by:$user, reviewed_at:(now|todate), review_decision:"accepted", review_note:$note}')
        patch_task_or_fail "$ID" "$(jq -n --arg u "$USER" --arg metadata "$REVIEW_METADATA" '{column: "done", actor: $u, metadata: $metadata}')" "done"
        ;;

    request-fix)
        # mc.sh request-fix <id> "review note" - mark review as needing producer fixes and return to todo
        ID="$2"
        NOTE="$3"
        if [ -z "$NOTE" ] || [ ${#NOTE} -lt 20 ]; then
            echo "❌ ERROR: request-fix requires a substantive review note (20+ chars)."
            echo "Usage: mc.sh request-fix <id> \"what failed review and what must be fixed\" --submission GENERATION"
            exit 1
        fi
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        validate_review_actor "$TASK_JSON" "$ID"
        if [ "$#" -gt 3 ]; then validate_review_submission "$TASK_JSON" "${4:-}" ${5+"$5"}; else validate_review_submission "$TASK_JSON"; fi
        CURRENT=$(echo "$TASK_JSON" | jq -r '.column')
        if [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in review before request-fix."
            echo "Current column: $CURRENT"
            exit 1
        fi
        EXISTING_METADATA=$(echo "$TASK_JSON" | jq -c '(.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end)')
        REVIEW_METADATA=$(jq -cn \
            --argjson existing "$EXISTING_METADATA" \
            --arg user "$USER" \
            --arg note "$NOTE" \
            '$existing + {reviewed_by:$user, reviewed_at:(now|todate), review_decision:"needs_fix", review_note:$note}')
        post_task_note_or_fail "$ID" "REVIEW_NEEDS_FIX: $NOTE" >/dev/null
        patch_task_or_fail "$ID" "$(jq -n --arg u "$USER" --arg metadata "$REVIEW_METADATA" '{column: "todo", actor: $u, metadata: $metadata}')" "todo"
        ;;

    done)
        # mc.sh done <id> - move to done (only from review)
        ID="$2"
        # Check task is in review first
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        CURRENT=$(printf '%s' "$TASK_JSON" | jq -r '.column')
        if [ "$CURRENT" != "review" ]; then
            echo "❌ ERROR: Task must be in 'review' before moving to 'done'."
            echo "Current column: $CURRENT"
            echo "Move to review first: mc.sh review $ID \"deliverable\""
            exit 1
        fi
        TASK_JSON=$(read_task_or_fail "$ID") || exit 1
        EXISTING_METADATA=$(echo "$TASK_JSON" | jq -c '(.metadata | if type == "string" then (fromjson? // {}) elif type == "object" then . else {} end)')
        REVIEW_DECISION=$(echo "$EXISTING_METADATA" | jq -r '.review_decision // "pending"')
        if [ "$REVIEW_DECISION" != "accepted" ]; then
            echo "❌ ERROR: Task #$ID has not been accepted by its reviewer."
            echo "Current review_decision: $REVIEW_DECISION"
            echo "Use: mc.sh accept-review $ID \"what was verified\""
            exit 1
        fi
        patch_task_or_fail "$ID" "$(jq -n --arg u "$USER" '{column: "done", actor: $u}')" "done"
        
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
        patch_task_or_fail "$ID" "$(jq -n --arg output "$OUTPUT" --arg user "$USER" '{output: $output, actor: $user}')" "" "" "$OUTPUT"
        ;;

    archive)
        # mc.sh archive <id> - archive a task (move to backlog + archived flag)
        ID="$2"
        patch_task_or_fail "$ID" \
            "$(jq -n --arg user "$USER" '{column: "backlog", archived: 1, actor: $user}')" \
            "backlog" "" "" "true"
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
        echo "  review <id> <output> --risk low|medium|high [--reviewer name] [--proof ARTIFACT_REF]"
        echo "                        - Move to review with reviewer metadata and packet"
        echo "  block <id> <reason>   - Record blocker, recovery attempted, and required next action"
        echo "  unblock <id> <note>  - Clear blocked flag before resuming"
        echo "  accept-review <id> <note> --submission GENERATION"
        echo "                        - Accept assigned review and move to done"
        echo "  request-fix <id> <note> --submission GENERATION"
        echo "                        - Mark review as needing fixes and return task to todo"
        echo "  deliver <id> <output> - Close output already delivered to the user; preserves human gates"
        echo "  done <id>             - Move to done (server enforces accepted review)"
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
        echo "Set MC_USER or ENTITY_MC_AGENT_NAME env var to change user (default: Agent)"
        ;;
esac
