"""Unit tests for fleet_drain core module."""
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from fleet_drain import (  # noqa: E402
    Account,
    FleetDrain,
    Policy,
    Surface,
    SwitchAction,
    apply_plan,
    best_candidate,
    collect_quota,
    format_status,
    generate_plan,
    load_config,
    plan_to_json,
    rank_accounts,
    set_manual_quota,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_loads_accounts(self, config_file):
        accounts, policy, ssh = load_config(config_file)
        assert len(accounts) == 2
        assert accounts[0].name == "luna"
        assert accounts[1].name == "herald"

    def test_loads_surfaces(self, config_file):
        accounts, _, _ = load_config(config_file)
        luna = accounts[0]
        assert len(luna.surfaces) == 2
        assert luna.surfaces[0].host == "enterprise"
        assert luna.surfaces[0].agent == "geordi"
        assert luna.surfaces[1].host == "mascotm3"

    def test_loads_policy(self, config_file):
        _, policy, _ = load_config(config_file)
        assert policy.min_remaining_pct == 10
        assert policy.target_remaining_pct == 50
        assert policy.drain_order == "priority"
        assert policy.dry_run_default is True

    def test_loads_ssh_config(self, config_file):
        _, _, ssh = load_config(config_file)
        assert ssh["defaults"]["user"] == "enterprise"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_example_config_loads(self):
        """The shipped example config should load without error."""
        example = Path(__file__).resolve().parent.parent / "config" / "accounts.example.yaml"
        accounts, policy, _ = load_config(example)
        assert len(accounts) == 2
        assert accounts[0].name == "luna"


# ---------------------------------------------------------------------------
# Quota and account state
# ---------------------------------------------------------------------------


class TestAccountQuota:
    def test_effective_remaining_min(self):
        acct = Account(name="test", email="t@t.com", priority=1, quota_source="manual", quota_file="")
        acct.five_hour_remaining_pct = 80
        acct.weekly_remaining_pct = 30
        assert acct.effective_remaining == 30

    def test_effective_remaining_none(self):
        acct = Account(name="test", email="t@t.com", priority=1, quota_source="manual", quota_file="")
        assert acct.effective_remaining == 0.0

    def test_is_usable_healthy(self):
        acct = Account(name="test", email="t@t.com", priority=1, quota_source="manual", quota_file="")
        acct.five_hour_remaining_pct = 50
        acct.status = "healthy"
        assert acct.is_usable is True

    def test_not_usable_exhausted(self):
        acct = Account(name="test", email="t@t.com", priority=1, quota_source="manual", quota_file="")
        acct.five_hour_remaining_pct = 50
        acct.status = "exhausted"
        assert acct.is_usable is False

    def test_set_manual_quota(self, config_file):
        accounts, _, _ = load_config(config_file)
        set_manual_quota(
            accounts,
            {"luna": {"five_hour_remaining_pct": 45, "weekly_remaining_pct": 80, "status": "healthy"}},
        )
        luna = next(a for a in accounts if a.name == "luna")
        assert luna.five_hour_remaining_pct == 45
        assert luna.status == "healthy"
        assert luna.is_usable is True


class TestCollectQuota:
    def test_reads_quota_file(self, config_file, tmp_path):
        accounts, _, _ = load_config(config_file)
        # Write a quota file
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        qfile = state_dir / "luna-quota.json"
        qfile.write_text(json.dumps({
            "five_hour_remaining_pct": 65,
            "weekly_remaining_pct": 80,
            "status": "healthy",
        }))

        collect_quota(accounts, state_dir)
        luna = next(a for a in accounts if a.name == "luna")
        assert luna.five_hour_remaining_pct == 65
        assert luna.status == "healthy"

    def test_skips_error_quota(self, config_file, tmp_path):
        accounts, _, _ = load_config(config_file)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        qfile = state_dir / "luna-quota.json"
        qfile.write_text(json.dumps({"error": "camofox_not_running"}))

        collect_quota(accounts, state_dir)
        luna = next(a for a in accounts if a.name == "luna")
        assert luna.status == "unknown"

    def test_missing_file_ok(self, config_file, tmp_path):
        accounts, _, _ = load_config(config_file)
        collect_quota(accounts, tmp_path / "nonexistent")
        # Should not crash
        luna = next(a for a in accounts if a.name == "luna")
        assert luna.five_hour_remaining_pct is None


# ---------------------------------------------------------------------------
# Ranking and policy
# ---------------------------------------------------------------------------


class TestRanking:
    def test_priority_order(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        ranked = rank_accounts(accounts, policy)
        assert ranked[0].name == "luna"  # priority 1

    def test_most_remaining_order(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        policy.drain_order = "most_remaining"
        ranked = rank_accounts(accounts, policy)
        # luna has 80%, herald has 5%
        assert ranked[0].name == "luna"

    def test_best_candidate_excludes(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        candidate = best_candidate(accounts, policy, exclude="luna")
        # Herald has 5% which is below min 10%, so should return None
        assert candidate is None

    def test_best_candidate_with_healthy_herald(self, config_file):
        accounts, policy, _ = load_config(config_file)
        set_manual_quota(
            accounts,
            {
                "luna": {"five_hour_remaining_pct": 80, "weekly_remaining_pct": 90, "status": "healthy"},
                "herald": {"five_hour_remaining_pct": 70, "weekly_remaining_pct": 85, "status": "healthy"},
            },
        )
        candidate = best_candidate(accounts, policy, exclude="luna")
        assert candidate is not None
        assert candidate.name == "herald"

    def test_best_candidate_none_when_all_exhausted(self, config_file):
        accounts, policy, _ = load_config(config_file)
        set_manual_quota(
            accounts,
            {
                "luna": {"five_hour_remaining_pct": 2, "status": "exhausted"},
                "herald": {"five_hour_remaining_pct": 3, "status": "exhausted"},
            },
        )
        assert best_candidate(accounts, policy) is None


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------


class TestPlanGeneration:
    def test_plan_no_switch_when_healthy(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy)
        # All surfaces currently on their own accounts, healthy ones should be noop
        luna_actions = [a for a in actions if "enterprise:geordi" == a.surface_id]
        # enterprise:geordi appears on both luna and herald; with no current_assignments,
        # each surface defaults to its own account
        for a in actions:
            if a.current_account == "luna":
                assert a.is_noop  # luna is healthy

    def test_plan_switches_exhausted(self, exhausted_luna):
        accounts, policy = exhausted_luna
        # Herald's enterprise surface should switch FROM luna TO herald
        # (or block if herald doesn't have that surface)
        actions = generate_plan(accounts, policy)

        # Find the mascotm3:geordi surface (only on luna)
        m3_action = next(a for a in actions if a.surface_id == "mascotm3:geordi")
        assert m3_action.current_account == "luna"
        # Luna is exhausted (2%), but herald has no mascotm3 surface
        assert m3_action.reason.startswith("BLOCKED")

    def test_plan_blocked_when_no_alternative(self, exhausted_luna):
        accounts, policy = exhausted_luna
        actions = generate_plan(accounts, policy)
        # All surfaces should be blocked since exhausted accounts can't switch
        # to accounts that don't cover that surface
        blocked = [a for a in actions if a.reason.startswith("BLOCKED")]
        assert len(blocked) > 0

    def test_plan_with_current_assignments(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        # Say enterprise:geordi is currently on herald (which is low)
        actions = generate_plan(accounts, policy, {"enterprise:geordi": "herald"})
        ent_action = next(a for a in actions if a.surface_id == "enterprise:geordi")
        assert ent_action.current_account == "herald"
        # Herald is at 5%, below min 10%, so should propose switch to luna
        assert ent_action.proposed_account == "luna"
        assert not ent_action.is_noop

    def test_plan_to_json(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy)
        j = plan_to_json(actions, accounts)
        data = json.loads(j)
        assert "actions" in data
        assert "summary" in data
        assert data["summary"]["total"] == len(actions)

    def test_plan_summary_counts(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy)
        j = json.loads(plan_to_json(actions, accounts))
        s = j["summary"]
        assert s["total"] == s["switches"] + s["blocked"] + s["noop"]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class TestApply:
    def test_dry_run_blocks_all(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy, {"enterprise:geordi": "herald"})
        result = apply_plan(actions, accounts, confirm=False)
        assert result["applied_count"] == 0

    def test_noop_for_healthy_surfaces(self, accounts_with_quota):
        """When accounts are healthy, their surfaces are no-op."""
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy)
        # Luna surfaces should be noop (80% remaining, healthy)
        luna_actions = [a for a in actions if a.current_account == "luna"]
        for a in luna_actions:
            assert a.is_noop

    def test_blocked_actions_stay_blocked(self, exhausted_luna):
        """Exhausted account surfaces with no alternative stay blocked."""
        accounts, policy = exhausted_luna
        actions = generate_plan(accounts, policy)
        # mascotm3:geordi only exists on luna (exhausted), no alternative -> BLOCKED
        m3_action = next(a for a in actions if a.surface_id == "mascotm3:geordi")
        assert m3_action.reason.startswith("BLOCKED")
        result = apply_plan(actions, accounts, confirm=True)
        # The blocked action should not be applied
        blocked_sids = [b["surface_id"] for b in result["blocked"]]
        assert "mascotm3:geordi" in blocked_sids

    def test_apply_returns_counts(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        actions = generate_plan(accounts, policy)
        result = apply_plan(actions, accounts, confirm=True)
        assert result["total"] == len(actions)
        assert result["applied_count"] + result["blocked_count"] + result["noop_count"] == result["total"]


# ---------------------------------------------------------------------------
# Status formatting
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_includes_account_names(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        status = format_status(accounts, policy)
        assert "luna" in status
        assert "herald" in status

    def test_status_includes_surfaces(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        status = format_status(accounts, policy)
        assert "enterprise" in status
        assert "mascotm3" in status

    def test_status_includes_policy(self, accounts_with_quota):
        accounts, policy = accounts_with_quota
        status = format_status(accounts, policy)
        assert "min=" in status
        assert "target=" in status


# ---------------------------------------------------------------------------
# FleetDrain wrapper class
# ---------------------------------------------------------------------------


class TestFleetDrain:
    def test_status(self, config_file):
        fd = FleetDrain(config_file)
        fd.load()
        status = fd.status()
        assert "luna" in status

    def test_plan(self, config_file):
        fd = FleetDrain(config_file)
        actions = fd.plan()
        assert len(actions) > 0

    def test_apply_dry_run(self, config_file):
        fd = FleetDrain(config_file)
        result = fd.apply(confirm=False)
        assert result["applied_count"] == 0

    def test_lazy_load(self, config_file):
        fd = FleetDrain(config_file)
        assert not fd._loaded
        fd.status()
        assert fd._loaded


# ---------------------------------------------------------------------------
# Surface and SwitchAction
# ---------------------------------------------------------------------------


class TestSurfaceAndAction:
    def test_surface_id(self):
        s = Surface(host="enterprise", agent="geordi", ssh_target="", codex_cli_path="codex", auth_file="~/.codex/auth.json")
        assert s.id == "enterprise:geordi"

    def test_switch_action_noop(self):
        a = SwitchAction(
            surface_id="ent:geordi",
            host="ent",
            agent="geordi",
            current_account="luna",
            proposed_account="luna",
            reason="test",
            current_remaining=80,
            proposed_remaining=80,
        )
        assert a.is_noop is True

    def test_switch_action_not_noop(self):
        a = SwitchAction(
            surface_id="ent:geordi",
            host="ent",
            agent="geordi",
            current_account="luna",
            proposed_account="herald",
            reason="test",
            current_remaining=5,
            proposed_remaining=70,
        )
        assert a.is_noop is False

    def test_to_dict(self):
        a = SwitchAction(
            surface_id="ent:geordi",
            host="ent",
            agent="geordi",
            current_account="luna",
            proposed_account="herald",
            reason="low quota",
            current_remaining=5,
            proposed_remaining=70,
        )
        d = a.to_dict()
        assert d["surface_id"] == "ent:geordi"
        assert d["current_account"] == "luna"
        assert d["proposed_account"] == "herald"
