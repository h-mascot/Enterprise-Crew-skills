#!/bin/bash
# Git commit and push with conventional commit message
# Usage: commit-and-push.sh "commit message"

MSG="${1:?Usage: commit-and-push.sh \"commit message\"}"

git add -A
git commit -m "$MSG"
git push

echo "✅ Committed and pushed: $MSG"
