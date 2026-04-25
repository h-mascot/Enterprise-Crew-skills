# Enterprise Crew Skills

Open-source skills and scripts built by the [Enterprise Crew](https://github.com/henrino3) — a multi-agent AI team powered by [OpenClaw](https://github.com/openclaw/openclaw).

## Skills

| Skill | Description |
|-------|-------------|
| [session-cleaner](./session-cleaner/) | Converts OpenClaw session JSONL files into clean, readable markdown transcripts. Strips tool calls and noise, keeps the conversation. |
| [skill-sharer](./skill-sharer/) | Share skills publicly to GitHub with automatic sanitization of personal info, secrets, and IPs |
| [x-video-transcribe](./x-video-transcribe/) | Transcribe and summarize X/Twitter videos using bird CLI + Gemini audio transcription |
| [daily-review](./daily-review/) | Comprehensive daily performance review with communication tracking, meeting analysis, output metrics, and focus time monitoring |
| [3pass](./3pass/) | 3-pass recursive prompting (critique → refine → final answer) for stress-testing claims, diagnoses, and plans |
| [benchmarking](./benchmarking/) | Benchmark models and agents based on operator leverage, hidden constraints, recovery, and proof — not just pretty answers |
| [ralph](./ralph/) | Autonomous AI coding loop (Ralph) - runs Codex/Claude Code repeatedly until all PRD items are complete |
| [council](./council/) | Topic-aware multi-agent council for structured debate and synthesis across engineering, sales, support, product, growth, ops, and strategy topics |
| [model-orchestrator](./model-orchestrator/) | Intelligent model load balancer for OpenClaw crons — distributes across providers by complexity, health, quota status, and cost |
| [entity-mc](./entity-mc/) | Bootstrap Entity Mission Control for AI agents — one-command setup for task management scripts, auto-pull crons, stall-check, and verification |
| [exec-approvals](./exec-approvals/) | Manage all OpenClaw exec approval settings — elevated access, obfuscation bypass, security posture, and per-provider allowlists |
| [release-manager](./release-manager/) | Turn shipped git activity into release candidates with suggested versions, changelog drafts, GitHub release notes, and launch-copy drafts |
| [self-healing](./self-healing/) | Wrap fragile OpenClaw work in checkpoints, fallback-model retries, watchdog crons, and proof-of-completion |

## About

These are tools we built while running AI agents in production. They solve real problems we hit daily — session management, automation, data processing, and more.

Each skill lives in its own folder with its own README and usage instructions.

## License

MIT
