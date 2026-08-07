# Plan: Fleet-wide Codex Account Drain in model-orchestrator

**Created:** 2026-08-07
**MC Task:** #1250
**Status:** In Progress

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
| `model-orchestrator/tests/conftest.py` | Pytest fixtures |

### Account config schema (`config/accounts.yaml`)

```yaml
accounts:
  - name: luna
    email: henrino3@gmail.com
    priority: 1           # lower = drain first
    quota_source: camofox # or "api" or "manual"
    surfaces:
      - host: enterprise   # 100.104.229.62
        agent: geordi
        codex_cli_path: /usr/local/bin/codex
        auth_file: ~/.codex/auth.json
      - host: mascotm3     # 100.86.150.96
        agent: geordi
        codex_cli_path: ~/.npm-global/bin/codex
        auth_file: ~/.codex/auth.json
  - name: herald
    email: henry@theherald.co
    priority: 2
    quota_source: camofox
    surfaces:
      - host: enterprise
        agent: geordi
        codex_cli_path: /usr/local/bin/codex
        auth_file: ~/.codex/auth.json

policy:
  min_remaining_pct: 10      # switch when account drops below this
  target_remaining_pct: 50   # preferred account must be above this to stay primary
  drain_order: priority      # priority | most_remaining | round_robin
  dry_run_default: true      # plan is always safe; apply requires --confirm

ssh:
  defaults:
    user: enterprise
    key: ~/.ssh/id_ed25519
```

### Policy logic (fleet_drain.py)

1. Load account config
2. Collect quota for each account (from scrape scripts or manual input)
3. Rank accounts by drain_order
4. For each surface (host + agent + account), decide:
   - KEEP: current account has sufficient quota
   - SWITCH: current account below threshold, better candidate exists
   - BLOCKED: no healthy alternative
5. Emit a plan (JSON) with proposed switches

### CLI

```
fleet_drain.py status   # show all accounts, quotas, active surfaces
fleet_drain.py plan     # emit JSON plan (always safe, no mutations)
fleet_drain.py apply    # execute plan (--confirm required, --dry-run default)
```

## Steps

- [x] 1. Create this plan
- [ ] 2. Build `fleet_drain.py` core module (config loader, policy, plan generator)
- [ ] 3. Build `fleet_drain_cli.py` CLI wrapper
- [ ] 4. Create example config `config/accounts.example.yaml`
- [ ] 5. Write tests `tests/test_fleet_drain.py`
- [ ] 6. Security cleanup: sanitize inline secrets in scrape scripts
- [ ] 7. Update SKILL.md and README.md
- [ ] 8. Run tests, verify all green
- [ ] 9. Commit, push, open PR
- [ ] 10. Close MC #1250 with review

## Security

- No secrets in config files, code, or tests
- Config references secret paths, never inline values
- Existing scrape scripts sanitized: remove inline `Bearer ***` patterns, use env vars
- `.gitignore` for `state/` and `config/accounts.yaml` (only `.example.yaml` tracked)

## Resume instructions

If context compacts, re-read this file. The code lives in `model-orchestrator/scripts/fleet_drain.py` and tests in `model- Trails/tests/`. Run `python3 -m pytest model-orchestrator/tests/` to verify.
