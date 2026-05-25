#!/usr/bin/env bash
# Enforce GitHub PR governance.
# New project bootstrap branches may self-merge; existing codebases require Henry approval.
# Usage: ./pr-governance.sh [--classify-only] [--project-class new|existing|auto] [--base main] [--head branch] [--dry-run]
set -euo pipefail

WORK_DIR="${WORK_DIR:-$(pwd)}"
cd "$WORK_DIR"

PROJECT_CLASS="${PR_GOVERNANCE_PROJECT_CLASS:-auto}"
BASE_BRANCH="${PR_GOVERNANCE_BASE:-}"
HEAD_BRANCH="${PR_GOVERNANCE_HEAD:-}"
TITLE="${PR_GOVERNANCE_TITLE:-}"
BODY_FILE="${PR_GOVERNANCE_BODY_FILE:-}"
DRY_RUN=false
CLASSIFY_ONLY=false
MERGE_METHOD="${PR_GOVERNANCE_MERGE_METHOD:-squash}"

usage() {
  sed -n '2,4p' "$0" | sed 's/^# //'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --classify-only) CLASSIFY_ONLY=true ;;
    --project-class) PROJECT_CLASS="${2:-}"; shift ;;
    --base) BASE_BRANCH="${2:-}"; shift ;;
    --head) HEAD_BRANCH="${2:-}"; shift ;;
    --title) TITLE="${2:-}"; shift ;;
    --body-file) BODY_FILE="${2:-}"; shift ;;
    --merge-method) MERGE_METHOD="${2:-}"; shift ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [ ! -d .git ]; then
  echo "Error: Not a git repository. Run from repository root or set WORK_DIR." >&2
  exit 1
fi

if [ "$PROJECT_CLASS" != "auto" ] && [ "$PROJECT_CLASS" != "new" ] && [ "$PROJECT_CLASS" != "existing" ]; then
  echo "Error: --project-class must be one of: new, existing, auto" >&2
  exit 1
fi

default_branch() {
  if [ -n "$BASE_BRANCH" ]; then
    echo "$BASE_BRANCH"
    return
  fi

  if command -v gh >/dev/null 2>&1; then
    local gh_default
    gh_default="$(gh repo view --json defaultBranchRef -q '.defaultBranchRef.name' 2>/dev/null || true)"
    if [ -n "$gh_default" ]; then
      echo "$gh_default"
      return
    fi
  fi

  local origin_head
  origin_head="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
  if [ -n "$origin_head" ]; then
    echo "$origin_head"
    return
  fi

  if git show-ref --quiet refs/heads/main || git show-ref --quiet refs/remotes/origin/main; then
    echo "main"
  else
    echo "master"
  fi
}

current_branch() {
  if [ -n "$HEAD_BRANCH" ]; then
    echo "$HEAD_BRANCH"
    return
  fi

  git branch --show-current
}

base_commit_count() {
  local base="$1"
  local ref="$base"
  if git show-ref --verify --quiet "refs/remotes/origin/$base"; then
    ref="origin/$base"
  elif git show-ref --verify --quiet "refs/heads/$base"; then
    ref="$base"
  fi

  git rev-list --count "$ref" 2>/dev/null || echo 0
}

marker_project_class() {
  local marker=".github/pr-governance"
  if [ ! -f "$marker" ]; then
    return
  fi

  awk -F= '
    /^[[:space:]]*PROJECT_CLASS[[:space:]]*=/ {
      gsub(/[[:space:]]/, "", $2);
      print $2;
      exit
    }
  ' "$marker"
}

classify_project() {
  local base="$1"

  if [ "$PROJECT_CLASS" = "new" ] || [ "$PROJECT_CLASS" = "existing" ]; then
    echo "$PROJECT_CLASS"
    return
  fi

  local marker_class
  marker_class="$(marker_project_class || true)"
  local max_commits="${PR_GOVERNANCE_NEW_PROJECT_BASE_MAX_COMMITS:-3}"
  local commits
  commits="$(base_commit_count "$base")"

  if [ "$marker_class" = "new" ] && [ "$commits" -le "$max_commits" ]; then
    echo "new"
  else
    echo "existing"
  fi
}

ensure_pushed() {
  local head="$1"
  if [ "$DRY_RUN" = true ]; then
    echo "DRY_RUN: would push branch $head"
    return
  fi

  git push -u origin "$head"
}

ensure_pr() {
  local base="$1"
  local head="$2"

  if [ "$DRY_RUN" = true ]; then
    echo "DRY_RUN_PR_URL"
    return
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echo "Error: gh CLI is required to create or inspect PRs." >&2
    exit 1
  fi

  local pr_url
  pr_url="$(gh pr view "$head" --json url -q .url 2>/dev/null || true)"
  if [ -n "$pr_url" ]; then
    echo "$pr_url"
    return
  fi

  local args=(pr create --base "$base" --head "$head")
  if [ -n "$TITLE" ]; then
    args+=(--title "$TITLE")
  else
    args+=(--fill)
  fi

  if [ -n "$BODY_FILE" ]; then
    args+=(--body-file "$BODY_FILE")
  fi

  gh "${args[@]}"
}

notify_henry_if_configured() {
  local pr_url="$1"
  local message="Approval required for existing codebase PR: $pr_url"

  if [ "${GITHUB_SYNC_NOTIFY_HENRY:-0}" != "1" ]; then
    echo "HENRY_NOTIFICATION=manual"
    echo "HENRY_NOTIFICATION_MESSAGE=$message"
    return
  fi

  if [ -z "${HENRY_NOTIFY_TARGET:-}" ]; then
    echo "Error: GITHUB_SYNC_NOTIFY_HENRY=1 requires HENRY_NOTIFY_TARGET." >&2
    exit 1
  fi

  if ! command -v openclaw >/dev/null 2>&1; then
    echo "Error: openclaw CLI not found; cannot notify Henry automatically." >&2
    exit 1
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "DRY_RUN: would notify Henry target ${HENRY_NOTIFY_TARGET}"
    return
  fi

  openclaw message send \
    --channel "${HENRY_NOTIFY_CHANNEL:-discord}" \
    --target "$HENRY_NOTIFY_TARGET" \
    -m "$message"
  echo "HENRY_NOTIFICATION=sent"
}

merge_pr() {
  local pr_url="$1"
  local method_flag="--squash"
  case "$MERGE_METHOD" in
    merge) method_flag="--merge" ;;
    rebase) method_flag="--rebase" ;;
    squash) method_flag="--squash" ;;
    *) echo "Error: merge method must be squash, merge, or rebase" >&2; exit 1 ;;
  esac

  if [ "$DRY_RUN" = true ]; then
    echo "DRY_RUN: would self-merge $pr_url with $MERGE_METHOD"
    return
  fi

  gh pr merge "$pr_url" "$method_flag" --delete-branch
}

BASE_BRANCH="$(default_branch)"
HEAD_BRANCH="$(current_branch)"
if [ -z "$HEAD_BRANCH" ]; then
  echo "Error: detached HEAD is not supported. Check out a branch first." >&2
  exit 1
fi

CLASSIFICATION="$(classify_project "$BASE_BRANCH")"
BASE_COMMITS="$(base_commit_count "$BASE_BRANCH")"

echo "PR_GOVERNANCE_PROJECT_CLASS=$CLASSIFICATION"
echo "PR_GOVERNANCE_BASE=$BASE_BRANCH"
echo "PR_GOVERNANCE_HEAD=$HEAD_BRANCH"
echo "PR_GOVERNANCE_BASE_COMMITS=$BASE_COMMITS"

if [ "$CLASSIFY_ONLY" = true ]; then
  exit 0
fi

if [ "$HEAD_BRANCH" = "$BASE_BRANCH" ]; then
  if [ "$CLASSIFICATION" = "existing" ]; then
    echo "PR_GOVERNANCE_DECISION=blocked_default_branch_push"
    echo "Existing codebases require a feature branch, PR, and Henry approval before merge."
    exit 2
  fi

  echo "PR_GOVERNANCE_DECISION=new_project_default_branch_allowed"
  echo "New project bootstrap is allowed to push directly to the default branch."
  exit 0
fi

ensure_pushed "$HEAD_BRANCH"
PR_URL="$(ensure_pr "$BASE_BRANCH" "$HEAD_BRANCH")"
echo "PR_URL=$PR_URL"

if [ "$CLASSIFICATION" = "new" ]; then
  echo "PR_GOVERNANCE_DECISION=self_merge_allowed"
  merge_pr "$PR_URL"
  echo "PR_GOVERNANCE_RESULT=self_merged"
else
  echo "PR_GOVERNANCE_DECISION=approval_required"
  notify_henry_if_configured "$PR_URL"
  echo "PR_GOVERNANCE_RESULT=waiting_for_henry_approval"
fi
