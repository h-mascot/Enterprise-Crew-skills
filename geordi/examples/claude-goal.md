# Example Claude goal

Goal: Use Claude Code (`claude` CLI) to repair a flaky end-to-end checkout
test. Claude is a last-resort runtime here because Codex and Droid are both
rate-limited on the operator's accounts.

Install and run:

```bash
geordi init --goal "Stabilize flaky checkout E2E" --mode claude
geordi mission add "Stabilize checkout E2E test by replacing time-based waits with explicit element waits" \
  --accept "npm run test:e2e -- checkout" \
  --scope "Checkout E2E spec and any helpers it imports. Do not touch other flows."
geordi run --mode claude --model sonnet
```

If your shared agent rules live outside the default `~/.agents/AGENTS.md` path:

```bash
GEORDI_AGENTS_FILE=/path/to/AGENTS.md geordi run --mode claude --model sonnet
```

To override the default `claude` flags (rarely needed):

```bash
GEORDI_CLAUDE_ARGS="-p --dangerously-skip-permissions --output-format text" geordi run --mode claude
```

The `claude` runtime is the Claude Code CLI. Install it from Anthropic's
official docs and verify with `command -v claude` before running. The
`--dangerously-skip-permissions` flag is intended for sandboxed/headless runs
only; do not use it in interactive shells.
