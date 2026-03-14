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

## About

These are tools we built while running AI agents in production. They solve real problems we hit daily — session management, automation, data processing, and more.

Each skill lives in its own folder with its own README and usage instructions.

## License

MIT
