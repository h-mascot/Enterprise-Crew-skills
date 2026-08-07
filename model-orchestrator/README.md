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

2. Edit `config/accounts.yaml` with your account names, priorities, explicit `current_assignments`, and agent surfaces. Never put secrets in the config. Use protected per-account `auth_source_path` values such as `~/.codex/accounts/luna/auth.json` and the active destination `~/.codex/auth.json`.

3. Run the quota scraper once per account. Each Camofox session must already be authenticated to the matching account before you scrape it; the scraper only reads the quota page for that session. The scripts default to the public `CAMOFOX_USER_ID=operator`, so set `CAMOFOX_USER_ID` to your stored profile when you reuse a personal Camofox session.

   These invocations populate the `quota_file` paths from `config/accounts.example.yaml`:
   ```bash
   CAMOFOX_USER_ID="luna" \
   CAMOFOX_SESSION_KEY="openai-codex-quota-luna" \
   GOOGLE_LOGIN_EMAIL="luna@example.invalid" \
   CODEX_QUOTA_FILE="state/openai-codex-quota-luna.json" \
   ./scripts/scrape-quota-openai-codex.sh

   CAMOFOX_USER_ID="herald" \
   CAMOFOX_SESSION_KEY="openai-codex-quota-herald" \
   GOOGLE_LOGIN_EMAIL="herald@example.invalid" \
   CODEX_QUOTA_FILE="state/openai-codex-quota-herald.json" \
   ./scripts/scrape-quota-openai-codex.sh
   ```

### Usage

```bash
# Show fleet status
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml status

# Generate a switch plan artifact (always safe - no mutations)
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml plan --out state/fleet-drain-plan.json

# Review as JSON
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml plan --json

# Dry-run the reviewed artifact
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml apply --plan state/fleet-drain-plan.json

# Execute the reviewed artifact (--confirm required for real changes)
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml apply --plan state/fleet-drain-plan.json --confirm
```

### Policy

| Setting | Default | Description |
|---------|---------|-------------|
| `min_remaining_pct` | 10 | Switch away when account drops below this |
| `target_remaining_pct` | 50 | Replacement eligibility/hysteresis: targets must be at or above this; current accounts between min and target stay active |
| `drain_order` | priority | `priority` \| `most_remaining` \| `round_robin` |

### Safety Contract

Plan/apply is a review boundary. `plan` writes an artifact with schema version, config digest, action digest, full plan digest, quota snapshot, and one action per unique surface. `apply` consumes that exact artifact, regenerates the expected actions under the current policy, rejects config/action/plan drift, duplicate surface IDs, unknown accounts, and unsafe current state, and defaults to dry-run.

Confirmed apply uses SSH with a static remote script sent on stdin. The SSH host must already have a trusted key in `known_hosts`; the command never auto-accepts a new host key. The remote script validates source/destination paths and JSON, verifies the active auth matches the declared current account source before mutation, backs up the active auth, installs the target auth atomically as mode `0600`, verifies target identity when available, and runs `codex login status`. Any post-mutation verification failure rolls back to the prior auth. Auth contents and tokens are never printed or returned.

### Tests

```bash
python3 -m pytest model-orchestrator/tests/ -v
```

## Requirements

- OpenClaw with cron support
- `curl`, `jq` (standard OpenClaw deps)
- Provider API keys configured
- Python 3.9+ (for quota scraping scripts and fleet drain)
- `pyyaml` for YAML account config

## Credits

Built by the [Enterprise Crew](https://github.com/henrino3) for [OpenClaw](https://github.com/openclaw/openclaw).
