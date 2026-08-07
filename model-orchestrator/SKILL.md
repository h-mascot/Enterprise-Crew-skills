---
name: model-orchestrator
description: "Intelligent model load balancer and Codex fleet drain controller for OpenClaw crons. Use when optimizing model selection, balancing provider load, draining low Codex accounts, or routing configured agents to fallback providers."
---

# Model Orchestrator

Intelligent model load balancer for OpenClaw crons plus a safe Codex fleet account drain controller. It distributes crons across providers based on task complexity, provider health, quota status, and cost, and it can plan or apply host-local Codex account drains through `codex-keyring`.

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

## Codex Fleet Drain

Use `scripts/fleet.py plan`, `apply --apply`, and `status` for account-aware Codex routing. Planning is the default; mutating actions require the explicit `--apply` acknowledgement. The fleet CLI is intentionally separate from the legacy cron-oriented `scripts/orchestrate.sh`.

Policy defaults:

- Drain active Codex accounts at `<= 20%` remaining.
- Recover fallback-routed hosts at `>= 35%` remaining, or by switching to an eligible alternate and then restoring routes.
- Use persisted state for hysteresis.
- Parse `codex-keyring status --json` as top-level `state` plus `aliases`.
- Treat the lower known value of `limit5hRemainingPercent` and `limitWeekRemainingPercent` as the account floor.
- Require explicit per-alias quota observation timestamps; generic `updatedAt` timestamps do not make quota fresh.
- Block mutation when the active alias is missing, quota is unknown, quota is stale, or quota confidence is not `exact`.
- Select alternates only when quota is known/fresh, `confidence=exact`, `manualOnly=false`, and health is allowed by policy (default: `ready`, `healthy`, `ok`).
- Enforce switch cooldown and bounded action timeouts.

Behavior:

- Above drain threshold: no action.
- Low active account plus eligible alternate: close admission for configured agents, disable `codex-keyring` auto-switch, switch account, then reopen admission.
- Low active account without an eligible alternate: close admission, run each agent's declarative non-Codex fallback command, then reopen admission so fallback work can continue.
- Previously fallback-routed host plus recovered active Codex quota: close admission, run declarative restore commands, then reopen admission.
- Previously fallback-routed host plus low active quota but an eligible alternate: close admission, disable `codex-keyring` auto-switch, switch and verify the host account, run declarative restore commands, then reopen admission.

Active workers drain naturally. Do not kill, restart, or terminate in-flight workers as part of this workflow.

The fleet config is generic JSON. `config/fleet.example.json` demonstrates one shared Enterprise quota source with Book, Ada, and `geordi-enterprise` enabled under that quota pool; Spock, Scotty, Zora, Midas, and EntityBuilder are present but disabled until private config confirms they consume that same Codex quota pool and have validated routing. MascotM3 remains a separate keyring host with an enabled `geordi-mascotm3` agent. Each host must have at least one enabled agent so fallback planning cannot record state with zero route actions. Disabled arbitrary agents can remain as data under `hosts[].agents[]` until private rollout supplies commands.

Host transports are `local` and `ssh`; all commands are argv arrays with bounded placeholder expansion. Agents can override transport separately from the quota/keyring host. `agentctl` in the public example is an adapter contract, not a shipped binary. Private configs may set `hosts[].status_command` to `["python3", "{skill_dir}/scripts/codex-keyring-status.py", "--json"]` or another argv array that returns codex-keyring-compatible JSON enriched with alias-level quota timestamps. `{skill_dir}` expands to this skill's installed directory, so copied configs do not depend on the caller's cwd. The shipped sensor reads safe per-alias metadata from `~/.codex-keyring/stats` by default and never emits auth/token payloads. The controller does not use `shell=True`, does not copy auth files or tokens between hosts, redacts obvious secret-bearing argv values in receipts, and does not record command stdout/auth payloads. Apply uses an exclusive non-blocking file lock for state load, status read, planning, and execution, and persists `partial_failure` state after any failed action when a state path exists so future plans fail closed until operator reconciliation. Desktop ChatGPT synchronization is opt-in through private policy/config and is disabled in the public example.

## Files
- `state/cron-tiers.json` — cron ID → tier mapping + metadata
- `state/provider-status.json` — compact current provider health/quota snapshot (backward-compatible)
- `state/provider-tracking.json` — detailed provider registry: status, source, checks, quota payloads, staleness, temp-model registry
- `state/fleet-state.json` — persisted Codex fleet hysteresis state (private runtime file)
- `state/fleet-receipts/` — apply receipts with redacted argv and action results
- `state/switches.log` — audit trail of all model switches
- `config/fleet.example.json` — public placeholder config for hosts and arbitrary agents
- `fleet_controller.py` — standard-library Codex fleet policy/controller
- `scripts/orchestrate.sh` — main orchestrator (run daily or on-demand)
- `scripts/fleet.py` — thin CLI for fleet plan/apply/status
- `scripts/check-providers.sh` — test configured providers
- `scripts/scrape-quota.sh` — scrape dashboards for quota info
