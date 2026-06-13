# Example Cursor goal

Goal: Use `cursor-agent` to add a settings toggle and dark-mode flag to a
Next.js dashboard. The agent's primary editing loop is the Cursor editor, so
we route the mission through `cursor-agent` headless print mode.

Install and run:

```bash
geordi init --goal "Add dark-mode settings toggle" --mode cursor
geordi mission add "Add dark-mode flag to settings and persist to localStorage" \
  --accept "npm test -- settings" \
  --scope "Settings page, theme helper, and tests only."
geordi run --mode cursor --model gpt-5
```

If your shared agent rules live outside the default `~/.agents/AGENTS.md` path:

```bash
GEORDI_AGENTS_FILE=/path/to/AGENTS.md geordi run --mode cursor --model gpt-5
```

To override the default `cursor-agent` flags (rarely needed):

```bash
GEORDI_CURSOR_ARGS="-p --trust --output-format text" geordi run --mode cursor
```

The `cursor` runtime is not installed by default. Install it from Cursor's
official docs and verify with `command -v cursor-agent` before running.
