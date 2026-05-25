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

# Build grep exclude pattern
GREP_EXCLUDE=""
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
  GREP_EXCLUDE="$GREP_EXCLUDE -e $pattern"
done

# Get changed files (modified and untracked, excluding patterns)
CHANGED=$(git status --porcelain 2>/dev/null | \
  grep -E '^\s*M\s+|^\?\?\s+' | \
  grep -v $GREP_EXCLUDE | \
  awk '{print $2}' || true)

if [ -z "$CHANGED" ]; then
  echo "No changes to push"
  exit 0
fi

echo "Staging files:"
echo "$CHANGED" | sed 's/^/  /'
echo

# Stage files
echo "$CHANGED" | xargs git add

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

if [ "${GITHUB_SYNC_ENFORCE_PR_GOVERNANCE:-1}" = "1" ] && \
   [ "${GITHUB_SYNC_PR_GOVERNANCE_AFTER_PUSH:-1}" = "1" ] && \
   [ -x "$SCRIPT_DIR/pr-governance.sh" ]; then
  if [ -z "$BASE_BRANCH" ]; then
    BASE_BRANCH="${PR_GOVERNANCE_BASE:-main}"
  fi

  if [ "$BRANCH" != "$BASE_BRANCH" ]; then
    echo
    echo "Running PR governance for $BRANCH -> $BASE_BRANCH..."
    PR_GOVERNANCE_BASE="$BASE_BRANCH" "$SCRIPT_DIR/pr-governance.sh" --head "$BRANCH"
  fi
fi
