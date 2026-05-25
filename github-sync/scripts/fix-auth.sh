#!/usr/bin/env bash
# Fix GitHub authentication issues
# Usage: ./fix-auth.sh
set -e

echo "GitHub Authentication Setup"
echo "==========================="
echo

# Check if gh is installed
if ! command -v gh &> /dev/null; then
  echo "Error: GitHub CLI (gh) is not installed"
  echo
  echo "Install instructions:"
  echo "  macOS:   brew install gh"
  echo "  Linux:   See https://github.com/cli/cli/blob/trunk/docs/install_linux.md"
  exit 1
fi

echo "1. Checking GitHub CLI authentication..."
if gh auth status &> /dev/null; then
  echo "   ✓ Already authenticated"
  GH_USER=$(gh api user -q .login)
  echo "   User: $GH_USER"
else
  echo "   ✗ Not authenticated"
  echo
  echo "Starting authentication flow..."
  echo
  gh auth login
  
  echo
  if gh auth status &> /dev/null; then
    GH_USER=$(gh api user -q .login)
    echo "✓ Authentication successful"
    echo "  User: $GH_USER"
  else
    echo "✗ Authentication failed"
    exit 1
  fi
fi

echo
echo "2. Configuring git credential helper..."

# Configure credential helper globally
git config --global credential.helper '!gh auth git-credential'
echo "   ✓ Global credential helper configured"

# Also configure locally if in a git repo
if [ -d .git ]; then
  git config credential.helper '!gh auth git-credential'
  echo "   ✓ Local credential helper configured"
  
  # Configure user info if not set
  if [ -z "$(git config user.email)" ]; then
    git config user.email "henry@curacel.ai"
    git config user.name "Henry Mascot"
    echo "   ✓ Git user info configured"
  fi
fi

echo
echo "3. Testing authentication..."

# Test with a simple API call
if gh api user &> /dev/null; then
  echo "   ✓ API access working"
else
  echo "   ✗ API access failed"
  exit 1
fi

# Test git operations if in a repo
if [ -d .git ]; then
  REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
  if [ -n "$REMOTE" ]; then
    echo "   Testing git remote access..."
    if git fetch origin --dry-run &> /dev/null; then
      echo "   ✓ Git remote access working"
    else
      echo "   ✗ Git remote access failed"
      echo
      echo "This might be a permissions issue. Verify:"
      echo "  1. You have access to the repository"
      echo "  2. The repository exists: $REMOTE"
      exit 1
    fi
  fi
fi

echo
echo "=========================================="
echo "✓ GitHub authentication is fully configured"
echo
echo "You can now:"
echo "  - Push/pull from repositories"
echo "  - Use gh CLI commands"
echo "  - Run sync scripts without auth prompts"
