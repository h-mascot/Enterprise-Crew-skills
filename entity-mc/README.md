# entity-mc

Bootstrap Entity Mission Control for AI agents — one-command setup for task management scripts, auto-pull crons, stall-check, and verification

## Structure

```
entity-mc/
├── README.md
├── SKILL.md
├── VERSION
├── install.sh
├── lib.sh
├── rollback.sh
├── source-scripts/mc-assign-model.sh
├── source-scripts/mc-auto-pull.sh
├── source-scripts/mc-build-context.sh
├── source-scripts/mc-health-check.sh
├── source-scripts/mc-stall-check.sh
├── source-scripts/mc.sh
├── verify.sh
```

## Agent Instructions

This skill includes a `SKILL.md` file with instructions for AI agents on how to use it. If you're running [OpenClaw](https://github.com/openclaw/openclaw), drop this folder into your skills directory and it will be auto-detected.

## Requirements

- [OpenClaw](https://github.com/openclaw/openclaw) or compatible AI agent framework
- Node.js 18+ (for JavaScript-based scripts)

## License

MIT
