---
name: compound-engineering
description: Install and use the Compound Engineering suite: CE planning, brainstorming, code review, debug, work execution, PR workflows, browser/Xcode testing, knowledge compounding, and specialized reviewer/research agents for high-leverage software work.
---

# Compound Engineering

This package mirrors the Compound Engineering plugin for Codex/OpenClaw-style skill environments.

## What is included

- `skills/` — individual CE skills such as `ce-plan`, `ce-work`, `ce-debug`, `ce-code-review`, `ce-compound`, and `ce-commit-push-pr`.
- `agents/` — reviewer, researcher, architecture, security, performance, and platform-specific agent definitions used by the CE workflows.
- plugin metadata for Codex, Claude, and Cursor where present.

## Install into Codex

From this directory:

```bash
mkdir -p ~/.codex/skills ~/.codex/agents/compound-engineering
rsync -a skills/ ~/.codex/skills/
rsync -a agents/ ~/.codex/agents/compound-engineering/source-agent-md/
```

Then start a fresh Codex session and invoke the relevant CE skill, for example `ce-plan`, `ce-work`, `ce-debug`, or `ce-code-review`.

## Primary entry points

- `ce-setup` — diagnose and bootstrap a project for CE workflows.
- `ce-ideate` / `ce-brainstorm` / `ce-plan` — shape ideas into executable plans.
- `ce-work` — execute planned work systematically.
- `ce-debug` — investigate and fix bugs from root cause.
- `ce-code-review` / `ce-doc-review` — parallel review with specialist lenses.
- `ce-compound` / `ce-compound-refresh` — preserve solved problems as reusable knowledge.

See `README.md` and each nested `skills/*/SKILL.md` for details.
