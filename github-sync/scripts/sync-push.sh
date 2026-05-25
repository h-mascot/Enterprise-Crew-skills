#!/usr/bin/env bash
# Push workspace changes to GitHub
# Usage: ./sync-push.sh ["commit message"]
set -e

# Default to current directory, but allow override
WORK_DIR="${WORK_DIR:-$(pwd)}"
cd "$WORK_DIR"

# Verify we're in a git repository
if [ ! -d .git ]; then
  echo "Error: Not a git repository. Run from repository root or set WORK_DIR."
  exit 1
fi

# Configure git if not already set
if [ -z "$(git config user.email)" ]; then
  echo "Configuring git..."
  git config user.email "henry@curacel.ai"
  git config user.name "Henry Mascot"
  git config credential.helper '!gh auth git-credential'
fi

# Get remote and branch info before staging/committing so governance can block
# unsafe default-branch writes without leaving a local commit behind.
REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if [ -z "$REMOTE" ]; then
  echo "Warning: No remote 'origin' configured"
  exit 1
fi

BRANCH=$(git branch --show-current)
if [ -z "$BRANCH" ]; then
  BRANCH="main"
fi

# Enforce PR governance before pushing directly to an established default branch.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_BRANCH=""
RUN_POST_PUSH_GOVERNANCE=false
if [ "${GITHUB_SYNC_ENFORCE_PR_GOVERNANCE:-1}" = "1" ] && [ -x "$SCRIPT_DIR/pr-governance.sh" ]; then
  BASE_BRANCH="${PR_GOVERNANCE_BASE:-}"
  if [ -z "$BASE_BRANCH" ]; then
    BASE_BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
  fi
  if [ -z "$BASE_BRANCH" ]; then
    BASE_BRANCH="main"
  fi

  if [ "$BRANCH" = "$BASE_BRANCH" ]; then
    CLASSIFICATION="$(PR_GOVERNANCE_BASE="$BASE_BRANCH" "$SCRIPT_DIR/pr-governance.sh" --classify-only | awk -F= '/^PR_GOVERNANCE_PROJECT_CLASS=/{print $2}')"
    if [ "$CLASSIFICATION" = "existing" ]; then
      echo "Error: Existing codebases require a feature branch, PR, and Henry approval."
      echo "Create a branch, push it, then run: $SCRIPT_DIR/pr-governance.sh"
      exit 2
    fi
  fi

  if [ "${GITHUB_SYNC_PR_GOVERNANCE_AFTER_PUSH:-1}" = "1" ] && [ "$BRANCH" != "$BASE_BRANCH" ]; then
    RUN_POST_PUSH_GOVERNANCE=true
    if ! command -v gh >/dev/null 2>&1; then
      echo "Error: gh CLI is required before pushing feature branches through PR governance."
      exit 2
    fi
    if ! gh auth status >/dev/null 2>&1; then
      echo "Error: gh authentication is required before pushing feature branches through PR governance."
      echo "Run: $SCRIPT_DIR/fix-auth.sh"
      exit 2
    fi
  fi
fi

# Define exclusion patterns
EXCLUDE_PATTERNS=(
  'SOUL.md'
  'IDENTITY.md'
  '.png$'
  '.jpg$'
  '.jpeg$'
  '.gif$'
  'secrets/'
  '.env'
  '.pem$'
  '.key$'
  'node_modules/'
  '__pycache__/'
)

should_exclude() {
  local file="$1"
  local pattern
  for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    if [[ "$file" =~ $pattern ]]; then
      return 0
    fi
  done
  return 1
}

# Get changed files, including staged adds, deletes, renames, and paths with
# spaces. The NUL format avoids shell quoting issues; rename entries include
# a second old-path record, which does not start with a porcelain status.
CHANGED_FILES=()
while IFS= read -r -d '' entry; do
  [ -n "$entry" ] || continue
  if [[ ! "$entry" =~ ^..\  ]]; then
    continue
  fi
  file="${entry:3}"
  [ -n "$file" ] || continue
  if should_exclude "$file"; then
    continue
  fi
  CHANGED_FILES+=("$file")
done < <(git status --porcelain -z 2>/dev/null)

if [ "${#CHANGED_FILES[@]}" -eq 0 ]; then
  echo "No changes to push"
  exit 0
fi

echo "Staging files:"
printf '  %s\n' "${CHANGED_FILES[@]}"
echo

# Stage files
git add -- "${CHANGED_FILES[@]}"

# Commit with provided message or default timestamp
MSG="${1:-Auto-sync $(date '+%Y-%m-%d %H:%M')}"
if git commit -m "$MSG" 2>/dev/null; then
  echo "✓ Committed: $MSG"
else
  echo "Nothing new to commit"
fi

# Push to remote
echo
echo "Pushing to $REMOTE ($BRANCH)..."
if git push origin "$BRANCH"; then
  echo "✓ Push complete"
else
  echo
  echo "Push failed. This usually means:"
  echo "  1. Remote has changes you don't have (run sync-pull.sh first)"
  echo "  2. Authentication failed (run fix-auth.sh)"
  exit 1
fi

if [ "$RUN_POST_PUSH_GOVERNANCE" = true ]; then
  echo
  echo "Running PR governance for $BRANCH -> $BASE_BRANCH..."
  PR_GOVERNANCE_BASE="$BASE_BRANCH" "$SCRIPT_DIR/pr-governance.sh" --head "$BRANCH"
fi
