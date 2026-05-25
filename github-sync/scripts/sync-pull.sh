#!/usr/bin/env bash
# Pull changes from GitHub with conflict handling
# Usage: ./sync-pull.sh
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

# Get remote info
REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if [ -z "$REMOTE" ]; then
  echo "Error: No remote 'origin' configured"
  exit 1
fi

BRANCH=$(git branch --show-current)
if [ -z "$BRANCH" ]; then
  BRANCH="main"
fi

echo "Pulling from $REMOTE ($BRANCH)..."

# Fetch latest changes
git fetch origin

# Check if we're behind
LOCAL=$(git rev-parse @ 2>/dev/null)
REMOTE_REF=$(git rev-parse @{u} 2>/dev/null || echo "")

if [ -z "$REMOTE_REF" ]; then
  echo "No upstream branch configured. Setting upstream..."
  git branch --set-upstream-to=origin/"$BRANCH" "$BRANCH"
  REMOTE_REF=$(git rev-parse @{u} 2>/dev/null)
fi

BASE=$(git merge-base @ @{u} 2>/dev/null || echo "")

if [ "$LOCAL" = "$REMOTE_REF" ]; then
  echo "✓ Already up to date"
  exit 0
fi

# Check for local changes
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
  echo "Local changes detected. Stashing..."
  git stash push -m "Auto-stash before pull $(date '+%Y-%m-%d %H:%M')"
  STASHED=true
else
  STASHED=false
fi

# Attempt merge with strategy preference for theirs (remote)
echo "Merging changes..."
if git merge origin/"$BRANCH" -X theirs -m "Merge from origin/$BRANCH"; then
  echo "✓ Merge successful"
  
  # Pop stash if we stashed
  if [ "$STASHED" = true ]; then
    echo "Restoring local changes..."
    if git stash pop; then
      echo "✓ Local changes restored"
    else
      echo "! Conflict restoring local changes"
      echo "  Your changes are in: git stash list"
      echo "  Resolve conflicts and run: git stash drop"
    fi
  fi
else
  echo
  echo "! Merge conflict detected"
  echo
  echo "Conflicts in:"
  git diff --name-only --diff-filter=U
  echo
  echo "Resolution options:"
  echo "  1. Accept remote (theirs):  git checkout --theirs <file> && git add <file>"
  echo "  2. Accept local (ours):      git checkout --ours <file> && git add <file>"
  echo "  3. Manual edit:              Edit files, then git add <file>"
  echo "  4. After resolving:          git commit"
  echo
  echo "To abort merge:               git merge --abort"
  exit 1
fi

echo "✓ Pull complete"
