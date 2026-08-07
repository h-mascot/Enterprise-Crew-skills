# Plan: Fleet-wide Codex Account Drain in model-orchestrator

**Created:** 2026-08-07
**MC Task:** #1250
**Status:** Ready for Review

## Goal

Extend the public `model-orchestrator` skill with account-aware Codex quota policy so the orchestrator can distribute load across multiple Codex accounts (e.g. Luna, Herald) on multiple agent surfaces (Geordi on Enterprise, Geordi on MascotM3), drain accounts in priority order, and safely plan/apply switches without exposing secrets.

## Background

The existing model-orchestrator (commit `6f6a1a4`) is a shell-script skill with:
- Provider health checks (6 providers)
- Quota scraping via Camofox browser
- Tier-based distribution (T1/T2/T3)
- Crisis mode

It has no concept of multiple accounts under one provider, no agent-target configuration, no safe plan/apply separation, no tests, and inline secrets in some scrape scripts.

## Architecture

### New files

| File | Purpose |
|------|---------|
| `model-orchestrator/scripts/fleet_drain.py` | Core Python module: account config, policy, plan, apply |
| `model-orchestrator/scripts/fleet_drain_cli.py` | CLI wrapper: `fleet_drain.py plan\|apply\|status` |
| `model-orchestrator/config/accounts.example.yaml` | Example account config (no secrets) |
| `model-orchestrator/tests/test_fleet_drain.py` | Unit tests for policy, plan, config parsing |
| `model-orchestrator/tests/test_fleet_drain_cli.py` | CLI integration tests |
| `model-orchestrator/tests/test_security_scan.py` | Regression scan for tracked secret patterns and known leaked literal hashes |
| `model-orchestrator/tests/conftest.py` | Pytest fixtures |

### Account config schema (`config/accounts.yaml`)

```yaml
accounts:
  - name: luna
    email: luna@example.invalid
    priority: 1           # lower = drain first
    quota_source: camofox # or "api" or "manual"
    quota_file: state/openai-codex-quota-luna.json
    surfaces:
      - host: enterprise   # 100.104.229.62
        agent: geordi
        ssh_target: enterprise@100.104.229.62
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/luna/auth.json
      - host: mascotm3     # 100.86.150.96
        agent: geordi
        ssh_target: henrymascot@100.86.150.96
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/luna/auth.json
  - name: herald
    email: herald@example.invalid
    priority: 2
    quota_source: camofox
    quota_file: state/openai-codex-quota-herald.json
    surfaces:
      - host: enterprise
        agent: geordi
        ssh_target: enterprise@100.104.229.62
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/herald/auth.json
      - host: mascotm3
        agent: geordi
        ssh_target: henrymascot@100.86.150.96
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/herald/auth.json

current_assignments:
  enterprise:geordi: luna
  mascotm3:geordi: luna

policy:
  min_remaining_pct: 10      # switch trigger
  target_remaining_pct: 50   # replacement eligibility / hysteresis
  drain_order: priority      # priority | most_remaining | round_robin
  dry_run_default: true      # plan is always safe; apply requires --confirm

```

### Policy logic (fleet_drain.py)

1. Load account config
2. Collect quota for each account (from scrape scripts or manual input)
3. Rank accounts by drain_order
4. For each unique surface, require an explicit current assignment from config or `--current`:
   - KEEP: current account is usable and at/above `min_remaining_pct`
   - SWITCH: current account is below `min_remaining_pct` or unusable, and a replacement is at/above `target_remaining_pct`
   - BLOCKED: current assignment is unknown/ambiguous/unsafe, or no eligible replacement exists
5. Emit a JSON artifact with schema version, config digest, action digest, quota snapshot, summary, and one action per unique surface
6. Apply consumes only that exact artifact, defaults to dry-run, and requires `--confirm` for SSH mutation

### CLI

```
fleet_drain.py status                         # show accounts, quotas, surfaces
fleet_drain.py plan --out state/plan.json     # emit review artifact, no mutations
fleet_drain.py apply --plan state/plan.json   # dry-run reviewed artifact
fleet_drain.py apply --plan state/plan.json --confirm
```

## Steps

- [x] 1. Create this plan
- [x] 2. Build `fleet_drain.py` core module (config loader, policy, plan generator, artifact validation, fail-closed SSH switch contract)
- [x] 3. Build `fleet_drain_cli.py` CLI wrapper
- [x] 4. Create example config `config/accounts.example.yaml`
- [x] 5. Write tests `tests/test_fleet_drain.py`
- [x] 6. Security cleanup: sanitize inline secrets in scrape scripts
- [x] 7. Update SKILL.md and README.md
- [x] 8. Run tests, shell syntax checks, security regression scan, and safe plan/apply verification
- [x] 9. Commit and push the repair to PR #2
- [x] 10. Submit MC #1250 for review with PR, test, and live-target receipts

## Security

- No secrets in config files, code, or tests
- Config references secret paths, never inline values
- Existing scrape scripts sanitized: remove inline credentials and personal account/org identifiers, use env vars/placeholders
- `.gitignore` for `state/` and `config/accounts.yaml` (only `.example.yaml` tracked)
- Confirmed apply regenerates expected actions under the current policy, requires pre-trusted SSH host keys, and verifies `codex login status` after atomic auth installation.
- Successful and rollback account switches are tested with temp auth artifacts. A live Enterprise/MascotM3 preflight was also run against missing protected sources and proved both active auth files stayed unchanged; no live account switch was performed.
- Regression scan covers tracked `model-orchestrator` text for obvious token/password patterns and known leaked literal hashes without printing the known literals.

## Verification

- `python3 -m pytest -q` — 63 passed
- `bash -n model-orchestrator/scripts/*.sh` — 11 scripts passed
- `python3 -m py_compile ...` and `git diff --cached --check` — passed
- Live fail-closed apply — two real Geordi targets returned `current_source_missing`, exit 4, with both active auth files unchanged

## Resume instructions

If context compacts, re-read this file. The code lives in `model-orchestrator/scripts/fleet_drain.py` and tests in `model-orchestrator/tests/`. Run `python3 -m pytest model-orchestrator/tests/ -v` to verify.
