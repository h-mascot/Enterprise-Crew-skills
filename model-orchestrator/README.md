# Model Orchestrator

Intelligent model load balancer for OpenClaw crons — distributes cron jobs across providers based on task complexity, provider health, quota status, and cost.

## What it does

Every cron gets assigned a tier based on task complexity:

| Tier | Task Type | Examples |
|------|-----------|----------|
| **T1 (Simple)** | Bash scripts, binary checks, trivial reports | Heartbeat pings, disk checks, session cleanup |
| **T2 (Medium)** | Content screening, simple decisions | Email triage, notification routing, content filtering |
| **T3 (Complex)** | Strategy, analysis, creative work | Research synthesis, blog writing, multi-step reasoning |

The orchestrator then routes each tier to the cheapest healthy provider:

| Tier | Primary | Secondary | Tertiary | Emergency |
|------|---------|-----------|----------|-----------|
| T1 | MiniMax | Gemini Flash | Kimi | Pause |
| T2 | Gemini Flash | Kimi | MiniMax | Pause |
| T3 | Opus | Sonnet | Kimi | Gemini Flash |

## Features

- **Provider health checks** — tests each provider with a real API call
- **Quota scraping** — checks dashboard pages for reset times (Anthropic, Gemini, GLM, MiniMax, OpenAI)
- **Smart rebalancing** — moves crons away from overloaded/erroring providers
- **Rate limit awareness** — distinguishes rate limits from "balance depleted" errors
- **Crisis mode** — when 2+ providers are down, preserves critical crons and pauses everything else
- **Audit trail** — logs every model switch with reason and timestamp
- **Discord reporting** — posts fleet health summary to a configured channel

## Setup

1. Copy this skill into your OpenClaw skills directory:
   ```bash
   cp -r model-orchestrator ~/.openclaw/skills/
   ```

2. Set environment variables:
   ```bash
   export OPENCLAW_GATEWAY_URL="http://localhost:18789"
   export OPENCLAW_GATEWAY_TOKEN="your-gateway-token"
   ```

3. Configure provider API keys via OpenClaw config or individual secret files.

4. Edit `scripts/orchestrate.sh` to add your cron-to-tier mappings in the tier assignment section.

## Usage

```bash
# Check provider health
./scripts/orchestrate.sh check

# Full orchestration run (check + distribute + report)
./scripts/orchestrate.sh distribute

# Crisis mode — manual override when things are bad
./scripts/orchestrate.sh crisis

# Show current status
./scripts/orchestrate.sh status
```

### As an OpenClaw cron

Add as a recurring cron (we run it every 6 hours):

```
Name: model-orchestrator
Schedule: 0 */6 * * *
Command: ./scripts/orchestrate.sh distribute
Model: openai-codex/gpt-5.4  # needs reasoning for distribution decisions
```

## Billing Model Notes

Not all providers bill the same way. Common mistake: confusing rate limits with empty balances.

| Provider | Billing | Error Interpretation |
|----------|---------|---------------------|
| **Z.ai (GLM)** | Rate-limit / quota ($360/yr Coding Max) | Error 1113 = quota exhausted for period, NOT empty balance. Resets periodically. |
| **Anthropic** | Rate-limit tiers | Cooldown = temporary |
| **OpenAI** | Rate-limit tiers + monthly spend caps | Check spend cap |
| **Google** | Rate-limit per model/minute | Free tier available |
| **MiniMax** | Actual API credits | Balance = real balance |
| **Kimi** | Rate-limit system | Cooldown = temporary |

**Rule:** When a provider returns quota/limit errors, report "quota limit hit — resets on [date]" NOT "balance depleted."

## Files

- `SKILL.md` — OpenClaw skill definition
- `scripts/orchestrate.sh` — main orchestrator script
- `scripts/check-providers.sh` — health check individual providers
- `scripts/scrape-quota.sh` — scrape provider dashboards
- `scripts/update-crons.sh` — batch update cron model assignments

## Fleet Account Drain

Distribute Codex work across multiple accounts on multiple agent surfaces, draining accounts in priority order with safe plan/apply separation.

### Setup

1. Copy the example config:
   ```bash
   cp config/accounts.example.yaml config/accounts.yaml
   ```

2. Edit `config/accounts.yaml` with your account names, priorities, and agent surfaces (hosts + Codex CLI paths). Never put secrets in the config — it references auth file paths.

3. Run quota scrapers first (they populate the state files the drain module reads):
   ```bash
   ./scripts/scrape-quota-openai-codex.sh
   ```

### Usage

```bash
# Show fleet status
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml status

# Generate a switch plan (always safe — no mutations)
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml plan

# Execute the plan (--confirm required for real changes)
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml apply --confirm
```

### Policy

| Setting | Default | Description |
|---------|---------|-------------|
| `min_remaining_pct` | 10 | Switch away when account drops below this |
| `target_remaining_pct` | 50 | Account must be above this to stay primary |
| `drain_order` | priority | `priority` \| `most_remaining` \| `round_robin` |

### Tests

```bash
python3 -m pytest model-orchestrator/tests/ -v
```

## Requirements

- OpenClaw with cron support
- `curl`, `jq` (standard OpenClaw deps)
- Provider API keys configured
- Python 3.9+ (for quota scraping scripts and fleet drain)
- `pyyaml` (optional — enables full YAML config; falls back to minimal parser)

## Credits

Built by the [Enterprise Crew](https://github.com/henrino3) for [OpenClaw](https://github.com/openclaw/openclaw).
