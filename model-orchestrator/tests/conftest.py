"""Shared pytest fixtures for fleet_drain tests."""
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture
def sample_config_yaml():
    """Minimal account config as YAML string."""
    return """
accounts:
  - name: luna
    email: henrino3@gmail.com
    priority: 1
    quota_source: manual
    quota_file: state/luna-quota.json
    surfaces:
      - host: enterprise
        agent: geordi
        ssh_target: enterprise@100.104.229.62
        codex_cli_path: /usr/local/bin/codex
        auth_file: ~/.codex/auth.json
      - host: mascotm3
        agent: geordi
        ssh_target: henrymascot@100.86.150.96
        codex_cli_path: ~/.npm-global/bin/codex
        auth_file: ~/.codex/auth.json
  - name: herald
    email: henry@theherald.co
    priority: 2
    quota_source: manual
    quota_file: state/herald-quota.json
    surfaces:
      - host: enterprise
        agent: geordi
        ssh_target: enterprise@100.104.229.62
        codex_cli_path: /usr/local/bin/codex
        auth_file: ~/.codex/auth.json

policy:
  min_remaining_pct: 10
  target_remaining_pct: 50
  drain_order: priority
  dry_run_default: true

ssh:
  defaults:
    user: enterprise
    key: ~/.ssh/id_ed25519
"""


@pytest.fixture
def config_file(tmp_path, sample_config_yaml):
    """Write sample config to a temp file."""
    cfg = tmp_path / "accounts.yaml"
    cfg.write_text(sample_config_yaml)
    return cfg


@pytest.fixture
def accounts_with_quota(config_file):
    """Load accounts and set manual quota."""
    from fleet_drain import load_config, set_manual_quota

    accounts, policy, ssh_config = load_config(config_file)
    set_manual_quota(
        accounts,
        {
            "luna": {"five_hour_remaining_pct": 80, "weekly_remaining_pct": 90, "status": "healthy"},
            "herald": {"five_hour_remaining_pct": 5, "weekly_remaining_pct": 15, "status": "warning"},
        },
    )
    return accounts, policy


@pytest.fixture
def exhausted_luna(accounts_with_quota):
    """Accounts where luna is exhausted and herald is healthy."""
    from fleet_drain import set_manual_quota

    accounts, policy = accounts_with_quota
    set_manual_quota(
        accounts,
        {
            "luna": {"five_hour_remaining_pct": 2, "weekly_remaining_pct": 5, "status": "exhausted"},
            "herald": {"five_hour_remaining_pct": 70, "weekly_remaining_pct": 85, "status": "healthy"},
        },
    )
    return accounts, policy
