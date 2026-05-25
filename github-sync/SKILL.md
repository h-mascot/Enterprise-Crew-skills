---
name: github-sync
description: "GitHub repository initialization, authentication, PR governance, and cross-machine synchronization workflows."
---

# GitHub Sync

Streamlined workflows for repository management and cross-machine synchronization.

## Quick Start

### Initialize New Repo

```bash
scripts/init-repo.sh <repo-name> [github-username]
```

Creates local repo, adds .gitignore, initializes git, creates GitHub repo, pushes initial commit.

### Push Changes

```bash
scripts/sync-push.sh ["commit message"]
```

Stages changes (excluding agent-specific files), commits, pushes to origin, then runs PR governance for non-default branches. Default message: "Auto-sync YYYY-MM-DD HH:MM"

### Pull Changes

```bash
scripts/sync-pull.sh
```

Pulls latest from origin, handles merge conflicts with strategy preference for remote changes.

### PR Governance

```bash
scripts/pr-governance.sh
```

Enforces Henry's repo governance:
- New projects created by `init-repo.sh`: self-merge is allowed while the default branch is still bootstrap-small.
- Existing codebases: direct default-branch pushes are blocked; create a branch, open a PR, and notify Henry for approval.

Use explicit override only when the project source of truth is clear:

```bash
PR_GOVERNANCE_PROJECT_CLASS=new scripts/pr-governance.sh
PR_GOVERNANCE_PROJECT_CLASS=existing scripts/pr-governance.sh
```

### Fix Authentication

```bash
scripts/fix-auth.sh
```

Configures gh auth, sets up git credential helper, tests authentication.

## File Exclusion Patterns

Scripts automatically exclude:
- Agent-specific: `SOUL.md`, `IDENTITY.md`
- Media: `*.png`, `*.jpg`, `*.jpeg`, `*.gif`
- Sensitive: `secrets/`, `.env`, `*.pem`, `*.key`
- Build artifacts: `node_modules/`, `dist/`, `.next/`

## PR Approval Flow

Default policy is conservative:
- Existing repos have no `.github/pr-governance` marker and therefore require approval.
- `init-repo.sh` writes `.github/pr-governance` with `PROJECT_CLASS=new`.
- `pr-governance.sh` treats that marker as new only while the default branch has at most 3 commits. After the bootstrap branch grows, the repo is treated as existing unless explicitly overridden.

Workflow:

```bash
git checkout -b feature/my-change
# ... make changes ...
scripts/sync-push.sh "Describe change"
```

`sync-push.sh` calls `pr-governance.sh` after a successful feature-branch push. For existing codebases the helper creates or reuses a PR, prints `PR_GOVERNANCE_DECISION=approval_required`, and prints a Henry notification message. For new projects it self-merges the PR. To send approval requests automatically from an agent runtime, set:

```bash
GITHUB_SYNC_NOTIFY_HENRY=1 HENRY_NOTIFY_TARGET=<target> scripts/pr-governance.sh
```

## Cross-Machine Sync

**Pattern**: Each machine pulls before working, pushes after changes.

```bash
# On any machine
cd ~/clawd  # or workspace directory
scripts/sync-pull.sh
# ... make changes ...
scripts/sync-push.sh "Description of changes"
```

**Conflict resolution**: If push fails due to remote changes, pull first:

```bash
scripts/sync-pull.sh  # Handles conflicts automatically
scripts/sync-push.sh "Merged changes"
```

## Git Configuration

Scripts auto-configure on first run:
```bash
git config user.email "henry@curacel.ai"
git config user.name "Henry Mascot"
git config credential.helper '!gh auth git-credential'
```

Override by setting manually before running scripts.

## Common Issues

**Push rejected**: Remote has changes you don't have locally.
```bash
scripts/sync-pull.sh  # Pull and merge
scripts/sync-push.sh  # Push again
```

**Authentication failed**: Credential helper not configured or gh not authenticated.
```bash
scripts/fix-auth.sh  # Fixes both issues
```

**Merge conflicts**: Auto-resolved favoring remote. If manual resolution needed, script will prompt.

## Script Details

### init-repo.sh
- Creates directory structure
- Generates comprehensive .gitignore
- Adds `.github/pr-governance` marker for new-project self-merge policy
- Initializes git repository
- Creates GitHub repo via `gh`
- Makes initial commit and push

### sync-push.sh
- Configures git if needed
- Stages non-excluded files
- Blocks direct default-branch pushes to existing codebases
- Commits with timestamp
- Pushes the current branch to origin
- Runs PR governance for feature branches so new projects self-merge and existing codebases wait for Henry approval

### pr-governance.sh
- Classifies repo as new or existing
- Creates or reuses PRs for feature branches
- Self-merges new-project PRs
- Leaves existing-code PRs waiting on Henry approval

### sync-pull.sh
- Fetches from origin
- Checks for divergence
- Merges with strategy preference
- Reports conflicts if manual resolution needed

### fix-auth.sh
- Checks gh authentication status
- Runs `gh auth login` if needed
- Configures git credential helper
- Validates with test push/pull
