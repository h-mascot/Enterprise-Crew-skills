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
- **Codex fleet drain** — plans or applies account-aware admission drains across configured hosts and agents

## Setup

1. Copy this skill into your OpenClaw skills directory:
   ```bash
   cp -r model-orchestrator "$OPENCLAW_SKILLS_DIR/"
   ```

2. Set environment variables:
   ```bash
   export OPENCLAW_GATEWAY_URL="http://localhost:18789"
   export OPENCLAW_GATEWAY_TOKEN="your-gateway-token"
   ```

3. Configure provider API keys through environment or private runtime config. Public files in this skill must not contain API keys, OAuth emails, passwords, host addresses, or tokens.

4. Edit `scripts/orchestrate.sh` to add your cron-to-tier mappings in the tier assignment section.

5. For Codex fleet drain, copy `config/fleet.example.json` to a private path, replace placeholder hostnames and commands, and point the CLI at it:
   ```bash
   export MODEL_ORCHESTRATOR_FLEET_CONFIG="/private/path/fleet.json"
   ```

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

# Plan Codex account drain actions only
python3 scripts/fleet.py plan --config /private/path/fleet.json

# Apply a freshly planned Codex fleet action list (explicit mutation acknowledgement required)
python3 scripts/fleet.py apply --apply --config /private/path/fleet.json

# Show parsed codex-keyring account status and persisted hysteresis state
python3 scripts/fleet.py status --config /private/path/fleet.json
```

Planning is the default. `fleet.py apply` refuses to run mutating commands unless the CLI receives `--apply`. The fleet CLI is intentionally separate from the legacy cron-oriented `orchestrate.sh`.

## Codex Fleet Drain

The fleet controller is a pure-Python, standard-library policy engine in `fleet_controller.py` with a thin CLI at `scripts/fleet.py`. It reads `codex-keyring status --json`, including top-level `state` plus `aliases`, and treats the lower known value of `limit5hRemainingPercent` and `limitWeekRemainingPercent` as each account's quota floor. Quota freshness is explicit: each alias needs a quota observation timestamp such as `quotaObservedAt`, `quota_observed_at`, or alias-level `checkedAt`. Generic top-level `updatedAt` values are not treated as quota observations.

Default policy:

- Drain when the active account is at or below 20% remaining.
- Recover fallback-routed hosts when the active account is at or above 35%, or by switching to another eligible Codex account and then restoring agent routes.
- Persist host state for hysteresis, so a host does not flap between fallback and Codex.
- Block mutation when the active alias is missing, quota is unknown, quota is stale, or quota confidence is not `exact`.
- Select alternates only when their quota is known, fresh, `confidence=exact`, `manualOnly=false`, and their health is in `alternate_health_allowlist` (default: `ready`, `healthy`, `ok`).
- Enforce a 600 second switch cooldown by default; during cooldown the controller waits instead of switching or fallback-routing again unless the active account is exhausted at `0%`.
- Bound every action with `default_action_timeout_seconds` or per-host/per-agent timeout overrides.

Switch behavior:

- Above the drain threshold, no action is planned.
- Low active account with an eligible alternate: close admission for every configured agent, disable `codex-keyring` auto-switch, switch the host account, then reopen admission.
- Low active account with no eligible alternate: close admission, run every agent's declarative non-Codex fallback command, then reopen admission so fallback work can continue.
- Previously fallback-routed host with recovered active Codex quota: close admission, run declarative restore commands, then reopen admission.
- Previously fallback-routed host with low active quota but an eligible alternate: close admission, disable `codex-keyring` auto-switch, switch and verify the host account, run declarative restore commands, then reopen admission.

Active workers are not killed or restarted. Admission closes only stop new work from landing on the drained account; in-flight workers finish naturally.

Config is generic JSON. The example demonstrates one shared Enterprise quota source with Book, Ada, and `geordi-enterprise` as enabled agents under that quota pool; Spock, Scotty, Zora, Midas, and EntityBuilder are present but disabled until private config confirms they consume that same Codex quota pool and have validated routing. MascotM3 remains a separate keyring host with its own enabled `geordi-mascotm3` agent. Every host must have at least one enabled agent so low-account plans cannot persist fallback state without route actions. Disabled arbitrary agents may remain as data until private rollout supplies commands. Add future agents by adding objects under `hosts[].agents[]`; do not add code branches for agent names.

Host transports are `local` and `ssh`. Agents can override their transport independently from the quota/keyring host: keyring and status commands use `hosts[].transport`, while agent admission/route commands use `agents[].transport` when present. Commands are arrays of arguments, placeholder expansion is limited to documented fields such as `{host_id}`, `{agent_id}`, `{active_alias}`, and `{target_alias}`. Status commands also support `{skill_dir}`, which expands to this skill's installed directory so copied configs can still invoke the shipped sensor. The controller never uses `shell=True`. For SSH hosts, remote argv is shell-quoted with `shlex.join`.

`agentctl` in the example is an adapter contract, not a shipped binary. Private deployments must provide an argv-compatible tool that closes admission, opens admission, and switches the agent route without killing active workers.

Private configs may set `hosts[].status_command` to an argv array that returns codex-keyring-compatible JSON. The shipped standard-library sensor `scripts/codex-keyring-status.py` runs configured `codex-keyring status --json`, reads safe per-alias quota metadata from `~/.codex-keyring/stats` by default, and emits only sanitized `state` and `aliases` data:

```json
"status_command": ["python3", "{skill_dir}/scripts/codex-keyring-status.py", "--json"]
```

```json
{
  "state": {"activeAlias": "primary-codex", "autoSwitch": true},
  "aliases": [
    {
      "alias": "primary-codex",
      "active": true,
      "limit5hRemainingPercent": 25,
      "limitWeekRemainingPercent": 25,
      "quotaObservedAt": "2026-08-07T12:00:00Z",
      "confidence": "exact",
      "health": "active",
      "manualOnly": false
    }
  ]
}
```

Desktop ChatGPT synchronization is opt-in. Keep `desktop_chatgpt_sync` false unless a private rollout explicitly defines how desktop session routing should be coordinated; this controller does not copy auth files or tokens between hosts.

Apply uses an exclusive non-blocking file lock for the complete transaction: state load, status read, planning, and execution. Receipts record the ordered action attempt list, redacted argv, return codes, and whether execution stopped early. They do not store command stdout, auth payloads, or secret-bearing argument values. Apply is fail-closed: the first failed command stops the run, writes a receipt, and persists `partial_failure` state whenever a state path exists, even if the failed action was first. Future plans return `blocked_partial_failure` with zero actions until an operator reconciles or clears that state.

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
- `fleet_controller.py` — Codex fleet policy/controller
- `config/fleet.example.json` — placeholder fleet config showing hosts and arbitrary agents
- `tests/` — standard-library unit tests for fleet policy and CLI behavior
- `scripts/orchestrate.sh` — main orchestrator script
- `scripts/fleet.py` — thin executable CLI for fleet plan/apply/status
- `scripts/check-providers.sh` — health check individual providers
- `scripts/scrape-quota.sh` — scrape provider dashboards
- `scripts/update-crons.sh` — batch update cron model assignments

## Requirements

- OpenClaw with cron support
- `curl`, `jq` (standard OpenClaw deps)
- Provider API keys configured outside public files
- Python 3 (for quota scraping scripts)
