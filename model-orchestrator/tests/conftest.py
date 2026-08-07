"""Shared pytest fixtures for fleet_drain tests."""

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture
def sample_config_yaml():
    return """
current_assignments:
  enterprise:geordi: luna
  mascotm3:geordi: luna

accounts:
  - name: luna
    email: luna@example.invalid
    priority: 1
    quota_source: manual
    quota_file: state/luna-quota.json
    surfaces:
      - host: enterprise
        agent: geordi
        ssh_target: enterprise@100.104.229.62
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/luna/auth.json
      - host: mascotm3
        agent: geordi
        ssh_target: henrymascot@100.86.150.96
        codex_cli_path: ~/.local/bin/codex
        active_auth_path: ~/.codex/auth.json
        auth_source_path: ~/.codex/accounts/luna/auth.json
  - name: herald
    email: herald@example.invalid
    priority: 2
    quota_source: manual
    quota_file: state/herald-quota.json
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

policy:
  min_remaining_pct: 10
  target_remaining_pct: 50
  drain_order: priority
  dry_run_default: true

"""


@pytest.fixture
def config_file(tmp_path, sample_config_yaml):
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text(sample_config_yaml)
    return cfg


@pytest.fixture
def accounts_with_quota(config_file):
    from fleet_drain import load_config, set_manual_quota

    accounts, policy, ssh_config = load_config(config_file)
    set_manual_quota(
        accounts,
        {
            "luna": {
                "five_hour_remaining_pct": 80,
                "weekly_remaining_pct": 90,
                "status": "healthy",
            },
            "herald": {
                "five_hour_remaining_pct": 55,
                "weekly_remaining_pct": 65,
                "status": "healthy",
            },
        },
    )
    return accounts, policy, ssh_config


@pytest.fixture
def exhausted_luna(accounts_with_quota):
    from fleet_drain import set_manual_quota

    accounts, policy, ssh_config = accounts_with_quota
    set_manual_quota(
        accounts,
        {
            "luna": {
                "five_hour_remaining_pct": 2,
                "weekly_remaining_pct": 5,
                "status": "exhausted",
            },
            "herald": {
                "five_hour_remaining_pct": 70,
                "weekly_remaining_pct": 85,
                "status": "healthy",
            },
        },
    )
    return accounts, policy, ssh_config
