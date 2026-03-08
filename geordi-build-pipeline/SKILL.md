---
name: geordi-build-pipeline
description: "Execute PRD stories sequentially via Codex with verification and retry. Uses ACP protocol (primary) with SSH+tmux fallback. Use when running multi-story builds via Geordi, executing PRDs, or orchestrating Codex build pipelines."
---

# Geordi Build Pipeline v4 🔧👓

**Geordi** is the builder — runs on **MascotM3 (Mac, primary)** or **ada-gateway (fallback)**.

## What This Skill Does

Takes a PRD with stories and executes them sequentially via Codex, with **separate verification, automatic retry, YAML-driven workflow definitions, and dual-location support**.

## v4 Upgrades
1. **ACP transport** — Structured JSON tasks via HTTP, pollable run status, cancellation support
2. **No more SSH+tmux wrapping** — ACP adapter on Mac handles Codex lifecycle
3. **Automatic fallback** — If ACP adapter is down, falls back to legacy SSH+tmux
4. **ada-gateway fallback** — If Mac is unreachable entirely, use local `sessions_spawn`

## Previous versions
- v3: Dual-location (Mac primary + ada-gateway fallback)
- v2: Separate verify step, YAML workflows, automatic retry

---

## 🔀 Location & Transport Detection (ALWAYS RUN FIRST)

```bash
ACP_SCRIPTS=~/clawd/skills/acp/scripts

# 1. Try ACP adapter on Mac (preferred)
if curl -s --connect-timeout 2 http://100.86.150.96:8100/ping | grep -q '"ok"'; then
  GEORDI_MODE="acp"
  echo "✅ ACP adapter available on Mac"

# 2. Try SSH to Mac (fallback)
elif ssh -o ConnectTimeout=2 -o BatchMode=yes henrymascot@100.86.150.96 "echo ok" 2>/dev/null; then
  GEORDI_MODE="ssh"
  echo "⚠️ ACP down, using SSH+tmux fallback"

# 3. Run locally on ada-gateway
else
  GEORDI_MODE="local"
  echo "⚠️ Mac unreachable, using ada-gateway"
fi
```

| Priority | Mode | Transport | How |
|----------|------|-----------|-----|
| 1 | **ACP** | HTTP to Mac:8100 | `acp-run.sh geordi "task"` + `acp-status.sh geordi <id> --wait` |
| 2 | **SSH** | SSH+tmux to Mac | Legacy `acpx codex exec --full-auto` |
| 3 | **Local** | sessions_spawn | Codex subagent on ada-gateway |

---

## Pipeline Steps (per story)

### 0. Detect Transport (see above)

### 1. Load Project Context (MANDATORY — BEFORE implementing)

**⚠️ PIPELINE FAILS if no context file exists. Create one first with `update-context.sh <path> --init`**

```bash
# Load context and capture it for the task prompt
CONTEXT=$(~/clawd/skills/geordi-build-pipeline/scripts/load-context.sh ~/Code/<project>)

# CONTEXT now contains: project overview, tech stack, architecture, recent updates,
# package.json summary, recent commits, testing info, PRD excerpt
# Prepend this to every Geordi task prompt (see Step 2)
```

This reads the project's context file (CONTEXT.md or memory/*-context.md), plus supplementary
info from package.json, TESTING.md, PRD.md, and recent git history. The full output gets
injected into the Codex task prompt so Geordi has full project awareness.

### 2. Implement (Codex)

**ACP MODE (preferred):**
```bash
# Send task via ACP — returns run_id for polling
RUN_ID=$(~/clawd/skills/acp/scripts/acp-run.sh geordi "Build feature X in ~/Code/entity. Context: ..." \
  --workdir ~/Code/entity 2>&1 | grep "Run ID:" | awk '{print $NF}')

# Wait for completion (polls every 5s)
~/clawd/skills/acp/scripts/acp-status.sh geordi "$RUN_ID" --wait
```

**SSH MODE (fallback):**
```bash
# Write task to file, launch tmux
ssh henrymascot@100.86.150.96 "cat > /tmp/geordi-task.txt << 'TASK'
<task description here>
TASK"
ssh henrymascot@100.86.150.96 "export PATH=/opt/homebrew/bin:\$PATH && tmux kill-session -t geordi 2>/dev/null; tmux new-session -d -s geordi -c ~/Code/<project>"
ssh henrymascot@100.86.150.96 "export PATH=/opt/homebrew/bin:\$PATH && tmux send-keys -t geordi 'acpx codex exec --full-auto \"\$(cat /tmp/geordi-task.txt)\"' Enter"
```

**LOCAL MODE (ada-gateway):**
```python
sessions_spawn(
    task="<task description with full context>",
    label="geordi-<project>-<story-id>",
    model="codex"
)
```

### 3. Monitor Progress

**ACP MODE:** Status is polled automatically by `acp-status.sh --wait`. For manual checks:
```bash
~/clawd/skills/acp/scripts/acp-status.sh geordi "$RUN_ID"
```

**SSH MODE (every 5 min):**
```bash
ssh henrymascot@100.86.150.96 "export PATH=/opt/homebrew/bin:\$PATH && tmux capture-pane -t geordi -p -S -30" | tail -20
```

**LOCAL MODE:**
```python
sessions_list(kinds=["subagent"], limit=5, messageLimit=1)
```

### 4. Verify Build (SEPARATE STEP — not Codex)

```bash
# Same for ACP and SSH modes — verify on Mac:
ssh henrymascot@100.86.150.96 "source ~/.nvm/nvm.sh && cd ~/Code/<project> && npx tsc --noEmit 2>&1 | tail -20"
ssh henrymascot@100.86.150.96 "source ~/.nvm/nvm.sh && cd ~/Code/<project>/packages/app && npx vite build 2>&1 | tail -20"

# LOCAL MODE — verify on ada-gateway:
cd ~/Code/<project> && npx tsc --noEmit 2>&1 | tail -20
```

**On verify failure → RETRY (up to 3x):**

**ACP MODE:**
```bash
RUN_ID=$(~/clawd/skills/acp/scripts/acp-run.sh geordi \
  "Fix build errors in ~/Code/entity: <paste errors>. Original task: <original>" \
  --workdir ~/Code/entity 2>&1 | grep "Run ID:" | awk '{print $NF}')
~/clawd/skills/acp/scripts/acp-status.sh geordi "$RUN_ID" --wait
```

**SSH/LOCAL MODE:** Same as original v3 (see legacy patterns).

**After 3 failed retries → mark story as blocked, move to next.**

### 5. Verify UI (optional, for visual stories)
```bash
# Use agent-browser or Camofox to screenshot and inspect
```

### 6. Commit & Push
```bash
ssh henrymascot@100.86.150.96 "cd ~/Code/<project> && git add -A && git commit -m 'feat: story N - <title>' && git push origin main"
```

### 7. Update PRD & Docs, Sync

Same as v3 — mark `passes: true`, update docs, sync to ada-gateway.

### 8. Update Project Context (MANDATORY — AFTER every build)

**⚠️ Every completed build MUST update the project context file.**

```bash
# Update context with what changed
~/clawd/skills/geordi-build-pipeline/scripts/update-context.sh ~/Code/<project> \
  "Perf fix: removed activity N+1 queries from task list API, added column filters, WebSocket updates replace polling"

# If no context file exists yet, create one:
~/clawd/skills/geordi-build-pipeline/scripts/update-context.sh ~/Code/<project> --init
```

This appends a dated entry to the project's context file with: what changed, new endpoints/features,
architectural decisions, known issues. Keeps the context file current for the next build.

---

## Cancellation (ACP only)

```bash
# Cancel a running build
curl -X POST http://100.86.150.96:8100/runs/$RUN_ID/cancel
```

---

## ACP vs Legacy Comparison

| | Legacy (SSH+tmux) | ACP |
|---|---|---|
| Start task | SSH → tmux → codex exec | `acp-run.sh geordi "task"` |
| Monitor | tmux capture-pane | `acp-status.sh geordi <id>` |
| Wait for done | Poll tmux + grep | `acp-status.sh geordi <id> --wait` |
| Cancel | Kill tmux session | `POST /runs/{id}/cancel` |
| Output | Parse tmux buffer | Structured JSON response |
| Error handling | Parse text | HTTP status codes + JSON |

---

## Workflow Definition (YAML)

Workflows live in `~/clawd/skills/geordi-build-pipeline/workflows/`

```yaml
# entity-build.yaml
name: entity-build
repo: ~/Code/entity
mac: 100.86.150.96
dev_server: http://localhost:5173
mc_api: http://100.106.69.9:3000
transport: acp  # acp | ssh | auto (default: auto = try ACP first)

steps:
  - name: implement
    agent: codex
    transport: auto          # Uses ACP if available, SSH fallback
    timeout_min: 45
    retry: 3

  - name: verify-build
    agent: ada
    commands:
      - "cd ~/Code/{repo}/packages/app && npx vite build"
    on_fail: retry_implement

  - name: commit
    agent: ada
    commands:
      - "cd ~/Code/{repo} && git add -A && git commit -m 'feat: {story_title}'"
```

---

## Entity Task Tracking (MC Integration)

Same as v3 — every story creates an MC task, progress tracked in real-time via MC API.

---

## Full Pipeline Runner (Sub-Agent)

**Launch command:**
```
sessions_spawn(task="Run Geordi pipeline for Entity. Read ~/clawd/skills/geordi-build-pipeline/SKILL.md for protocol. Use ACP transport (~/clawd/skills/acp/scripts/acp-run.sh geordi 'task') for each build step. Poll with acp-status.sh --wait. Verify builds separately. Retry up to 3x. Report after each story.")
```

**Pipeline loop:**
```
For each story in prd.json where passes === false:
  0. DETECT TRANSPORT: ACP → SSH → local
  1. LOAD CONTEXT (MANDATORY): 
     CONTEXT=$(scripts/load-context.sh ~/Code/<project>)
     If exit 1 → STOP. Create context file first.
  2. IMPLEMENT: 
     acp-run.sh geordi "$CONTEXT\n\n## Task\n<story details>" → acp-status.sh --wait
     (Context is PREPENDED to every task prompt)
  3. VERIFY BUILD: npx vite build (separate from Codex)
  4. VERIFY UI: agent-browser snapshot (if frontend)
  5. On failure → RETRY (up to 3x via ACP)
  6. On success → COMMIT + PUSH
  7. UPDATE prd.json, docs, sync
  8. UPDATE CONTEXT (MANDATORY):
     scripts/update-context.sh ~/Code/<project> "summary of changes"
  9. Move to next story
```

---

## Decision: Codex Exec vs Ralph Loop

| Criteria | Use Codex Exec | Use Ralph Loop |
|----------|---------------|----------------|
| Single file change | ✅ | |
| Backend endpoint | ✅ | |
| Simple UI component | ✅ | |
| Complex multi-file UI | | ✅ |
| Needs verification checkpoints | | ✅ |

---

## Naming Convention

| Agent | Emoji | Role | Location |
|-------|-------|------|----------|
| **Geordi** | 🔧👓 | Mac Codex builder | MascotM3 (Mac) |
| **Ralph** | 🤖 | Codex loop runner (PRD stories) | MascotM3 (Mac) |

---

*v4 updated 2026-02-28. Added: ACP transport as primary, SSH+tmux as fallback, structured run management.*
