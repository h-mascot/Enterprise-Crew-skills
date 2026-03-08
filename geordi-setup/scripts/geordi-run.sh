#!/bin/bash
# Wrapper: Run Codex with Geordi identity + post-completion hook
# Usage: geordi-run.sh "task description" [--repo /path/to/repo]
#
# This injects GEORDI.md as instructions and posts results to Discord when done.

set -e

TASK="${1:?Usage: geordi-run.sh \"task\" [--repo /path/to/repo]}"
REPO="${3:-$(pwd)}"
GEORDI_MD="$HOME/.agents/GEORDI.md"
SCRIPTS_DIR="$HOME/.agents/scripts"
WEBHOOK_URL="https://discord.com/api/webhooks/1474651838757343242/O9byiAS2OWjT-UdL49FyQkT61apQC89gdfEAbbaklFmMcDKTiyN-JDzYQGYTQTqcv2rL"

# Ensure nvm/node available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

# Post start notification
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg content "👷 **Geordi Starting**\n\n🔧 Task: ${TASK}\n📁 Repo: ${REPO}" '{username: "Geordi 👷", content: $content}')" > /dev/null

cd "$REPO"

# Run Codex with Geordi identity
# Capture exit code
set +e
CODEX_OUTPUT=$(npx @openai/codex \
  --instructions-file "$GEORDI_MD" \
  --full-auto \
  --quiet \
  "$TASK" 2>&1)
EXIT_CODE=$?
set -e

# Truncate output for Discord
SHORT_OUTPUT="${CODEX_OUTPUT:0:1500}"

if [ $EXIT_CODE -eq 0 ]; then
  # Auto-bootstrap CTRL if missing
  if [ -f package.json ]; then
    if ! node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync('package.json','utf8'));process.exit(j.scripts&&j.scripts['ctrl:gate']?0:1)"; then
      echo "[geordi] CTRL not configured, auto-bootstrapping..."
      bash ~/clawd/scripts/ctrl-bootstrap.sh . --mode mvp 2>/dev/null || true
    fi
  fi
  
  if [ -f package.json ] && node -e "const fs=require('fs');const j=JSON.parse(fs.readFileSync('package.json','utf8'));process.exit(j.scripts&&j.scripts['ctrl:gate']?0:1)"; then
    echo "[geordi] running ctrl:gate before completion"
    set +e
    CTRL_OUTPUT=$(npm run ctrl:gate 2>&1)
    CTRL_EXIT=$?
    set -e
    if [ $CTRL_EXIT -ne 0 ]; then
      EXIT_CODE=$CTRL_EXIT
      CODEX_OUTPUT="$CODEX_OUTPUT

[ctrl:gate failed]
$CTRL_OUTPUT"
      STATUS="❌ **Task Failed** (ctrl:gate failed)"
    else
      CODEX_OUTPUT="$CODEX_OUTPUT

[ctrl:gate]
$CTRL_OUTPUT"
      STATUS="✅ **Task Complete**"
    fi
  else
    STATUS="✅ **Task Complete**"
  fi
else
  STATUS="❌ **Task Failed** (exit code: $EXIT_CODE)"
fi

# Post completion
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg content "${STATUS}\n\n🔧 Task: ${TASK}\n\n\`\`\`\n${SHORT_OUTPUT}\n\`\`\`" \
    '{username: "Geordi 👷", content: $content}')" > /dev/null

echo "$STATUS"
echo "$CODEX_OUTPUT"
