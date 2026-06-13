# Changelog

## 1.2.0

- Adds `cursor` mode that runs missions through the `cursor-agent` CLI (headless print mode).
- Adds `claude` mode that runs missions through the `claude` CLI (Claude Code, non-interactive print mode).
- Expands `geordi doctor`, `geordi init`, and `geordi run` to accept all four runtimes: `codex`, `droid`, `cursor`, `claude`.
- Adds `GEORDI_CURSOR_ARGS` and `GEORDI_CLAUDE_ARGS` environment overrides for per-runtime flag customization.
- Adds `examples/cursor-goal.md` and `examples/claude-goal.md` showing minimal goal/mission flow.
- Refreshes `SKILL.md` with a Cursor mode section and a Claude mode section; metadata tags gain `cursor` and `claude`.
- Bumps the pinned installer tarball tag to `v1.2.0`.

## 1.1.1

- Requires global `AGENTS.md` context by default before Geordi mission prompts.
- Adds `GEORDI_AGENTS_FILE` and `GEORDI_REQUIRE_AGENTS` controls for portable installs.
- Updates Codex/Droid examples and build-loop reference to document the global context step.

## 1.1.0

- Renames the public bundle and command to `geordi`.
- Merges the reusable geordi build-pipeline discipline into the installable mission runner.
- Adds sanitized references for the build loop and Geordi builder identity.
- Keeps Codex and Droid modes, goal/mission state, acceptance checks, receipts, and resumable logs.

## 1.0.0

- Initial public installable mission-runner bundle.
- Adds install script, CLI, Codex mode, Droid mode, goal/mission state, acceptance checks, and examples.
- Sanitized for public reuse: no private hostnames, private IPs, operator names, account-specific model IDs, or secrets.
