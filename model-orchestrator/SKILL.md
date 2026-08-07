---
name: model-orchestrator
description: "Intelligent model load balancer for OpenClaw crons — distributes across providers by complexity and cost. Use when optimizing model selection for crons, balancing provider load, or troubleshooting model routing."
---

# Model Orchestrator

Intelligent model load balancer for OpenClaw crons. Distributes crons across providers based on task complexity, provider health, quota status, and cost.

## Architecture

### Tier System
- **Tier 1 (Simple):** Runs bash scripts, reports HEARTBEAT_OK → cheapest available
- **Tier 2 (Medium):** Reads output, screens content, makes simple decisions → mid-tier
- **Tier 3 (Complex):** Strategy, analysis, creative, long context → smartest available

### Provider Priority (per tier)
| Tier | Primary | Secondary | Tertiary | Emergency |
|------|---------|-----------|----------|-----------|
| T1   | MiniMax | Flash 3   | Kimi     | (pause)   |
| T2   | Flash 3 | Kimi      | MiniMax  | (pause)   |
| T3   | Opus    | Sonnet    | Kimi     | Flash 3   |

### Provider Billing Models
**IMPORTANT:** Not all providers use credit-based billing. Know the model before diagnosing.

| Provider | Billing Model | "Down" Diagnosis |
|----------|--------------|------------------|
| **Z.ai (GLM)** | **Rate-limit / quota system** (like Anthropic/OpenAI). $360/yr Coding Max plan. NOT credits. Error 1113 ("余额不足") = quota exhausted for current period, NOT empty balance. Resets periodically. Check dashboard: https://open.bigmodel.cn/ |
| **Anthropic** | Rate-limit tiers (tokens/min, requests/min). Cooldown = temporary. |
| **OpenAI** | Rate-limit tiers. Monthly spend caps optional. |
| **Google** | Rate-limit per model per minute. Free tier available. |
| **MiniMax** | API credits (actual balance). |
| **Kimi** | Rate-limit system. |

**Rule:** When a provider returns quota/limit errors, report it as "quota limit hit — resets on [date]" NOT "balance depleted / needs top-up." Check the dashboard for reset timing before recommending action.

### Crisis Mode
When 2+ providers are down:
1. Move ALL T1 crons to surviving cheap provider
2. PAUSE T2 crons that aren't critical
3. Keep only critical T3 crons running
4. Log everything for recovery

### Recovery
- Scrape provider dashboards for quota reset times
- Create one-shot `at` crons to check recovery
- Auto-redistribute when providers come back

## Fleet Account Drain

Account-aware Codex quota policy for distributing work across multiple Codex accounts (Luna, Herald, etc.) on multiple agent surfaces (Geordi on Enterprise, Geordi on MascotM3, etc.).

### Drain Policy

- **min_remaining_pct** — switch away from an account when its remaining quota drops below this (default: 10%)
- **target_remaining_pct** — replacement eligibility/hysteresis: a target account must be at or above this value to receive a switch. Accounts between `min_remaining_pct` and `target_remaining_pct` stay active if already current, but are not selected as replacements.
- **drain_order** — how to rank accounts: `priority` (drain low-priority-number first), `most_remaining`, `round_robin`

### Safe plan/apply

The drain module separates planning from execution:

1. `plan` — generates a JSON artifact with schema version, config digest, action digest, full plan digest, quota snapshot, and proposed switches. Always safe, no mutations.
2. `apply` — consumes an exact plan artifact with `--plan`, regenerates the expected actions under the current policy, and rejects config drift, action drift, duplicate surface IDs, unknown accounts, and unsafe current state. It is dry-run unless `--confirm` is passed.

Current account assignment is explicit and fail-closed. Declare `current_assignments` in config or pass `--current SURFACE=ACCOUNT`; unknown or ambiguous current state produces blocked actions and never mutates.

Confirmed apply switches auth over SSH with a static remote script sent on stdin. The host key must already be trusted in `known_hosts`; new keys are not auto-accepted. The remote side validates protected source auth JSON, verifies the active auth matches the declared current account before mutation, backs up the active auth, atomically installs the target auth as mode `0600`, verifies the target identity when available, runs `codex login status`, and rolls back automatically on post-mutation failures. It never prints or returns auth contents or tokens.

### Config

Copy `config/accounts.example.yaml` to `config/accounts.yaml` and fill in real values. The real config is git-ignored. Never store secrets inline. Each account/surface binding has `auth_source_path` for a protected per-account source, `active_auth_path` for the active Codex destination, and `codex_cli_path` for the remote Codex binary.

Before you drain accounts, scrape each account into its matching quota file with a Camofox session that is already authenticated to that same account. The scripts default to the public `CAMOFOX_USER_ID=operator`, so set `CAMOFOX_USER_ID` to your stored profile when you reuse a personal Camofox session.

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

The `CODEX_QUOTA_FILE` paths above line up with `config/accounts.example.yaml`. Do not reuse a Camofox session across different Codex accounts; the session must already be signed into the account that owns the quota file you are updating.

### CLI

```bash
# Show all accounts, quotas, active surfaces
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml status

# Generate a switch plan artifact (safe, no mutations)
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml plan --out state/fleet-drain-plan.json

# Dry-run an existing artifact
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml apply --plan state/fleet-drain-plan.json

# Execute an existing artifact
python3 scripts/fleet_drain_cli.py --config config/accounts.yaml apply --plan state/fleet-drain-plan.json --confirm
```

### Python API

```python
from fleet_drain import FleetDrain
fd = FleetDrain("config/accounts.yaml")
artifact = fd.plan_artifact()
result = fd.apply(artifact, confirm=False)
```

## Files
- `state/cron-tiers.json` — cron ID → tier mapping + metadata
- `state/provider-status.json` — compact current provider health/quota snapshot (backward-compatible)
- `state/provider-tracking.json` — detailed provider registry: status, source, checks, quota payloads, staleness, temp-model registry
- `state/switches.log` — audit trail of all model switches
- `scripts/orchestrate.sh` — main orchestrator (run daily or on-demand)
- `scripts/check-provider.sh` — test a single provider
- `scripts/scrape-quota.sh` — scrape dashboards for quota info
- `scripts/fleet_drain.py` — account-aware Codex quota policy core module
- `scripts/fleet_drain_cli.py` — CLI for fleet drain (status/plan/apply)
- `config/accounts.example.yaml` — example account configuration (no secrets)
- `tests/` — pytest suite for fleet drain logic
