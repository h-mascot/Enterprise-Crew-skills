#!/usr/bin/env bash
# Initialize a new GitHub repository with proper setup
# Usage: ./init-repo.sh <repo-name> [github-username]
set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <repo-name> [github-username]"
  echo "Example: $0 my-project henrino3"
  exit 1
fi

REPO_NAME="$1"
GH_USER="${2:-$(gh api user -q .login 2>/dev/null || echo '')}"

if [ -z "$GH_USER" ]; then
  echo "Error: Could not determine GitHub username. Please provide it as second argument."
  echo "Usage: $0 <repo-name> <github-username>"
  exit 1
fi

REPO_DIR="$HOME/$REPO_NAME"

echo "Initializing repository: $REPO_NAME"
echo "GitHub user: $GH_USER"
echo "Directory: $REPO_DIR"
echo

# Create directory
if [ -d "$REPO_DIR" ]; then
  echo "Error: Directory $REPO_DIR already exists"
  exit 1
fi

mkdir -p "$REPO_DIR"
cd "$REPO_DIR"

# Create comprehensive .gitignore
cat > .gitignore << 'EOF'
# Agent-specific files
SOUL.md
IDENTITY.md
.agent-*

# Secrets and credentials
secrets/
.env
.env.*
*.pem
*.key
*.crt
credentials.json
token.json

# Media files
*.png
*.jpg
*.jpeg
*.gif
*.svg
*.mp4
*.mov
*.avi

# Dependencies
node_modules/
venv/
.venv/
__pycache__/
*.pyc
.Python

# Build artifacts
dist/
build/
.next/
.nuxt/
.cache/
*.egg-info/

# IDE and editor files
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# Logs
*.log
logs/
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# OS files
Thumbs.db
.Spotlight-V100
.Trashes
EOF

# Initialize git
echo "Initializing git repository..."
git init
git config user.email "henry@curacel.ai"
git config user.name "Henry Mascot"
git config credential.helper '!gh auth git-credential'

# Add PR governance marker. Existing codebases do not get this marker, so the
# governance helper defaults them to approval-required.
mkdir -p .github
cat > .github/pr-governance << 'EOF'
# github-sync PR governance marker
# New repos created by init-repo may self-merge while the default branch is still bootstrap-small.
PROJECT_CLASS=new
EOF

# Add and commit bootstrap files
git add .gitignore .github/pr-governance
git commit -m "Initial commit: Add repository policy"

# Create README
echo "# $REPO_NAME" > README.md
echo "" >> README.md
echo "Created on $(date '+%Y-%m-%d')" >> README.md
git add README.md
git commit -m "Add README"

# Create GitHub repository
echo
echo "Creating GitHub repository..."
gh repo create "$GH_USER/$REPO_NAME" --public --source=. --remote=origin

# Push to GitHub
echo
echo "Pushing to GitHub..."
CURRENT_BRANCH="$(git branch --show-current)"
git push -u origin "$CURRENT_BRANCH"

echo
echo "✓ Repository initialized successfully!"
echo "  Local: $REPO_DIR"
echo "  Remote: https://github.com/$GH_USER/$REPO_NAME"
