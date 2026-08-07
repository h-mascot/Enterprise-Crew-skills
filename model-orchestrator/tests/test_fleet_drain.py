"""Unit tests for fleet_drain core module."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from fleet_drain import (  # noqa: E402
    Account,
    ConfigError,
    PlanValidationError,
    REMOTE_SWITCH_SCRIPT,
    _actions_digest,
    _plan_digest,
    apply_plan,
    best_candidate,
    build_plan_artifact,
    collect_quota,
    config_digest,
    format_status,
    generate_plan,
    load_config,
    plan_to_json,
    rank_accounts,
    set_manual_quota,
    validate_plan_artifact,
)


class TestConfigLoading:
    def test_loads_accounts_surfaces_policy_and_current(self, config_file):
        accounts, policy, ssh = load_config(config_file)
        assert [a.name for a in accounts] == ["luna", "herald"]
        assert len(accounts[0].surfaces) == 2
        assert accounts[0].surfaces[0].codex_cli_path == "~/.local/bin/codex"
        assert accounts[0].surfaces[0].active_auth_path == "~/.codex/auth.json"
        assert accounts[0].surfaces[0].auth_source_path == "~/.codex/accounts/luna/auth.json"
        assert policy.min_remaining_pct == 10
        assert policy.target_remaining_pct == 50
        assert ssh["_current_assignments"]["enterprise:geordi"] == "luna"

    def test_example_config_loads(self):
        example = Path(__file__).resolve().parent.parent / "config" / "accounts.example.yaml"
        accounts, policy, ssh = load_config(example)
        assert {a.name for a in accounts} == {"luna", "herald"}
        assert policy.min_remaining_pct <= policy.target_remaining_pct
        assert ssh["_current_assignments"]["mascotm3:geordi"] == "luna"

    def test_rejects_invalid_thresholds(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(
            sample_config_yaml.replace("min_remaining_pct: 10", "min_remaining_pct: 80")
            .replace("target_remaining_pct: 50", "target_remaining_pct: 20")
        )
        with pytest.raises(ConfigError):
            load_config(cfg)

    def test_rejects_same_source_and_active_auth_path(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "auth_source_path: ~/.codex/accounts/luna/auth.json",
                "auth_source_path: ~/.codex/auth.json",
                1,
            )
        )
        with pytest.raises(ConfigError):
            load_config(cfg)

    def test_rejects_unknown_current_assignment_surface(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "  mascotm3:geordi: luna\n",
                "  mascotm3:geordi: luna\n  typo:geordi: luna\n",
            )
        )
        with pytest.raises(ConfigError, match="unknown current assignment surface"):
            load_config(cfg)

    def test_rejects_unknown_current_assignment_account(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(sample_config_yaml.replace("  enterprise:geordi: luna", "  enterprise:geordi: typo"))
        with pytest.raises(ConfigError, match="unknown current assignment account"):
            load_config(cfg)

    def test_rejects_option_like_ssh_target(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "ssh_target: enterprise@100.104.229.62",
                "ssh_target: -oProxyCommand=bad",
                1,
            )
        )
        with pytest.raises(ConfigError, match="invalid ssh_target"):
            load_config(cfg)

    def test_rejects_remote_path_traversal_components(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "auth_source_path: ~/.codex/accounts/luna/auth.json",
                "auth_source_path: ~/.codex/../auth.json",
                1,
            )
        )
        with pytest.raises(ConfigError, match="path traversal"):
            load_config(cfg)

    def test_rejects_relative_quota_file_path_traversal(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(sample_config_yaml.replace("quota_file: state/luna-quota.json", "quota_file: state/../outside.json", 1))
        with pytest.raises(ConfigError, match="path traversal"):
            load_config(cfg)


class TestQuotaAndRanking:
    def test_effective_remaining_min(self):
        acct = Account("test", "t@example.invalid", 1, "manual", "")
        acct.five_hour_remaining_pct = 80
        acct.weekly_remaining_pct = 30
        assert acct.effective_remaining == 30

    def test_collect_quota_reads_state_file(self, config_file, tmp_path):
        accounts, _, _ = load_config(config_file)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "luna-quota.json").write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 65,
                    "weekly_remaining_pct": 80,
                    "status": "healthy",
                }
            )
        )
        collect_quota(accounts, state_dir)
        luna = next(a for a in accounts if a.name == "luna")
        assert luna.five_hour_remaining_pct == 65
        assert luna.status == "healthy"

    def test_collect_quota_accepts_plain_relative_and_absolute_paths(self, tmp_path, sample_config_yaml):
        absolute_quota = tmp_path / "outside.json"
        absolute_quota.write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 21,
                    "weekly_remaining_pct": 42,
                    "status": "healthy",
                }
            )
        )
        cfg = tmp_path / "ok.yaml"
        cfg.write_text(
            sample_config_yaml.replace("quota_file: state/luna-quota.json", "quota_file: luna-quota.json", 1).replace(
                "quota_file: state/herald-quota.json", f"quota_file: {absolute_quota}", 1
            )
        )
        accounts, _, _ = load_config(cfg)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "luna-quota.json").write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 65,
                    "weekly_remaining_pct": 80,
                    "status": "healthy",
                }
            )
        )
        collect_quota(accounts, state_dir)
        luna = next(a for a in accounts if a.name == "luna")
        herald = next(a for a in accounts if a.name == "herald")
        assert luna.five_hour_remaining_pct == 65
        assert herald.five_hour_remaining_pct == 21
        assert herald.status == "healthy"

    def test_priority_and_most_remaining_ranking(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        assert rank_accounts(accounts, policy)[0].name == "luna"
        policy.drain_order = "most_remaining"
        assert rank_accounts(accounts, policy)[0].name == "luna"

    def test_best_candidate_uses_target_threshold(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        set_manual_quota(
            accounts,
            {
                "luna": {"five_hour_remaining_pct": 2, "status": "exhausted"},
                "herald": {
                    "five_hour_remaining_pct": 40,
                    "weekly_remaining_pct": 60,
                    "status": "healthy",
                },
            },
        )
        assert best_candidate(accounts, policy, exclude="luna") is None
        herald = next(a for a in accounts if a.name == "herald")
        herald.five_hour_remaining_pct = 55
        assert best_candidate(accounts, policy, exclude="luna").name == "herald"


class TestPlanGeneration:
    def test_models_each_unique_surface_once(self, accounts_with_quota):
        accounts, policy, ssh = accounts_with_quota
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        assert sorted(a.surface_id for a in actions) == [
            "enterprise:geordi",
            "mascotm3:geordi",
        ]

    def test_unknown_assignment_blocks_fail_closed(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        actions = generate_plan(accounts, policy, {})
        assert all(a.reason.startswith("BLOCKED") for a in actions)
        assert {a.current_account for a in actions} == {"UNKNOWN"}

    def test_ambiguous_declared_assignment_blocks(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "ambiguous.yaml"
        cfg.write_text(
            sample_config_yaml.replace("current_assignments:\n  enterprise:geordi: luna\n  mascotm3:geordi: luna\n\n", "")
            .replace("auth_source_path: ~/.codex/accounts/luna/auth.json", "current_account: luna\n        auth_source_path: ~/.codex/accounts/luna/auth.json", 1)
            .replace("auth_source_path: ~/.codex/accounts/herald/auth.json", "current_account: herald\n        auth_source_path: ~/.codex/accounts/herald/auth.json", 1)
        )
        accounts, policy, _ = load_config(cfg)
        set_manual_quota(
            accounts,
            {
                "luna": {"five_hour_remaining_pct": 80, "status": "healthy"},
                "herald": {"five_hour_remaining_pct": 80, "status": "healthy"},
            },
        )
        ent = next(a for a in generate_plan(accounts, policy) if a.surface_id == "enterprise:geordi")
        assert ent.reason.startswith("BLOCKED: ambiguous current account")

    def test_unknown_current_account_blocks(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        action = next(
            a
            for a in generate_plan(accounts, policy, {"enterprise:geordi": "missing"})
            if a.surface_id == "enterprise:geordi"
        )
        assert action.reason == "BLOCKED: current account not found in config"

    def test_unknown_explicit_current_surface_is_rejected(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        with pytest.raises(ConfigError, match="unknown current assignment surface"):
            generate_plan(accounts, policy, {"typo:geordi": "luna"})

    def test_current_between_min_and_target_stays_put(self, accounts_with_quota):
        accounts, policy, ssh = accounts_with_quota
        set_manual_quota(
            accounts,
            {
                "luna": {
                    "five_hour_remaining_pct": 30,
                    "weekly_remaining_pct": 70,
                    "status": "healthy",
                },
                "herald": {
                    "five_hour_remaining_pct": 90,
                    "weekly_remaining_pct": 95,
                    "status": "healthy",
                },
            },
        )
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        assert all(a.is_noop for a in actions)

    def test_exhausted_current_switches_to_target_eligible_account(self, exhausted_luna):
        accounts, policy, ssh = exhausted_luna
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        assert {a.proposed_account for a in actions} == {"herald"}
        assert all(not a.is_noop for a in actions)

    def test_same_auth_source_replacement_blocks(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "same-source.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/accounts/luna/auth.json",
            )
        )
        accounts, policy, ssh = load_config(cfg)
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

        actions = generate_plan(accounts, policy, ssh["_current_assignments"])

        assert all(action.is_blocked for action in actions)
        assert all(action.current_account == "luna" for action in actions)
        assert all(action.proposed_account == "luna" for action in actions)
        assert {
            action.current_auth_source_path for action in actions
        } == {"~/.codex/accounts/luna/auth.json"}
        assert all(
            action.reason
            == "BLOCKED: replacement auth source matches current auth source"
            for action in actions
        )

    def test_plan_json_contains_schema_digest_and_summary(self, accounts_with_quota, config_file):
        accounts, policy, ssh = accounts_with_quota
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        data = json.loads(plan_to_json(actions, accounts, policy, config_digest(config_file), ssh["_current_assignments"]))
        assert data["schema_version"] == 1
        assert data["config_digest"] == config_digest(config_file)
        assert data["summary"]["total"] == 2
        assert data["actions_digest"]
        assert data["plan_digest"]


class TestApply:
    def _switch_artifact(self, accounts, policy, ssh, digest):
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        return build_plan_artifact(actions, accounts, policy, digest, ssh["_current_assignments"])

    def _refresh_digests(self, artifact):
        artifact["actions_digest"] = _actions_digest(artifact["actions"])
        artifact["plan_digest"] = _plan_digest(artifact)

    def test_legacy_dry_run_action_list_still_blocks(self, exhausted_luna):
        accounts, policy, ssh = exhausted_luna
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        result = apply_plan(actions, accounts, confirm=False)
        assert result["applied_count"] == 0
        assert result["blocked_count"] == 2

    def test_confirmed_apply_requires_artifact(self, exhausted_luna):
        accounts, policy, ssh = exhausted_luna
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        with pytest.raises(PlanValidationError):
            apply_plan(actions, accounts, confirm=True)

    def test_confirmed_apply_requires_current_policy(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        with pytest.raises(PlanValidationError, match="current policy"):
            apply_plan(artifact, accounts, config_digest_value=config_digest(config_file), confirm=True)

    def test_artifact_round_trip_and_config_drift_rejection(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        actions = validate_plan_artifact(artifact, accounts, config_digest(config_file))
        assert len(actions) == 2
        with pytest.raises(PlanValidationError, match="config digest drift"):
            validate_plan_artifact(artifact, accounts, "bad-digest")

    def test_tampered_noop_plan_rejected_with_policy(self, accounts_with_quota, config_file):
        accounts, policy, ssh = accounts_with_quota
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        artifact = build_plan_artifact(actions, accounts, policy, config_digest(config_file), ssh["_current_assignments"])
        artifact["actions"] = json.loads(json.dumps(artifact["actions"]))
        for action in artifact["actions"]:
            action["proposed_account"] = "herald"
            action["reason"] = "tampered switch"
            action["current_remaining_pct"] = 80
            action["proposed_remaining_pct"] = 55
            action["proposed_auth_source_path"] = "~/.codex/accounts/herald/auth.json"
        artifact["summary"] = {"total": len(artifact["actions"]), "switches": len(artifact["actions"]), "blocked": 0, "noop": 0}
        artifact["actions_digest"] = _actions_digest(artifact["actions"])
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="action list drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file), policy=policy)

    def test_duplicate_surface_ids_rejected(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"].append(dict(artifact["actions"][0]))
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="duplicate surface_id"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_action_drift_rejected(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"][0]["proposed_account"] = "luna"
        with pytest.raises(PlanValidationError, match="action digest drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_top_level_reviewed_field_drift_rejected_by_plan_digest(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["policy"]["drain_order"] = "round_robin"
        with pytest.raises(PlanValidationError, match="plan digest drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_exact_top_level_schema_fields_required(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["review_note"] = "unexpected"
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="invalid plan top-level fields"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_exact_action_schema_fields_required_without_policy(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        del artifact["actions"][0]["reason"]
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="invalid action fields"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_extra_action_schema_fields_rejected_without_policy(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"][0]["review_note"] = "unexpected"
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="invalid action fields"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_same_auth_source_switch_artifact_is_rejected_before_apply(
        self, tmp_path, sample_config_yaml
    ):
        cfg = tmp_path / "same-source.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/accounts/luna/auth.json",
            )
        )
        accounts, policy, ssh = load_config(cfg)
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
        actions = generate_plan(accounts, policy, ssh["_current_assignments"])
        artifact = build_plan_artifact(
            actions, accounts, policy, config_digest(cfg), ssh["_current_assignments"]
        )
        artifact["actions"][0]["proposed_account"] = "herald"
        artifact["actions"][0]["reason"] = "tampered switch"
        artifact["actions"][0]["proposed_remaining_pct"] = 70
        artifact["summary"] = {
            "total": len(artifact["actions"]),
            "switches": 1,
            "blocked": len(artifact["actions"]) - 1,
            "noop": 0,
        }
        self._refresh_digests(artifact)

        with pytest.raises(PlanValidationError, match="auth source matches current"):
            apply_plan(
                artifact,
                accounts,
                config_digest_value=config_digest(cfg),
                policy=None,
                confirm=False,
            )

    def test_tampered_summary_rejected_after_plan_digest_recomputed(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["summary"]["switches"] = 0
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="summary snapshot drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_tampered_account_quota_snapshot_rejected_after_plan_digest_recomputed(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["accounts"][0]["five_hour_remaining_pct"] = 99
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="account quota snapshot drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_tampered_policy_rejected_after_plan_digest_recomputed(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["policy"]["target_remaining_pct"] = 1
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="policy snapshot drift"):
            validate_plan_artifact(
                artifact, accounts, config_digest(config_file), policy=policy
            )

    def test_tampered_current_assignments_rejected_after_plan_digest_recomputed(
        self, exhausted_luna, config_file
    ):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["current_assignments"]["enterprise:geordi"] = "herald"
        artifact["plan_digest"] = _plan_digest(artifact)
        with pytest.raises(PlanValidationError, match="current_assignments snapshot drift"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_unknown_account_in_artifact_rejected(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"][0]["proposed_account"] = "missing"
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="unknown proposed account"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_recomputed_digest_operational_tampering_is_rejected(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"][0]["ssh_target"] = "attacker@example.invalid"
        artifact["actions"][0]["codex_cli_path"] = "/tmp/codex"
        artifact["actions"][0]["current_auth_source_path"] = "~/.codex/accounts/attacker/auth.json"
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="ssh_target mismatch"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_missing_configured_surface_action_is_rejected(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        artifact["actions"] = artifact["actions"][:1]
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="missing configured surface"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_blocked_action_operational_tampering_is_rejected(self, accounts_with_quota, config_file):
        accounts, policy, _ = accounts_with_quota
        actions = generate_plan(accounts, policy, {})
        artifact = build_plan_artifact(actions, accounts, policy, config_digest(config_file), {})
        artifact["actions"][0]["ssh_target"] = "attacker@example.invalid"
        self._refresh_digests(artifact)
        with pytest.raises(PlanValidationError, match="ssh_target mismatch"):
            validate_plan_artifact(artifact, accounts, config_digest(config_file))

    def test_real_switch_contract_over_ssh_is_mocked(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"ok": true, "verified": true, "stage": "complete"}\n',
                stderr="",
            )

        result = apply_plan(
            artifact,
            accounts,
            config_digest_value=config_digest(config_file),
            policy=policy,
            confirm=True,
            executor=fake_run,
        )
        assert result["applied_count"] == 2
        assert result["failed_count"] == 0
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["ssh", "-o"]
        assert "StrictHostKeyChecking=yes" in cmd
        assert "bash -s --" in cmd[-1]
        assert "BatchMode=yes" in cmd
        assert kwargs["input"].startswith("#!/usr/bin/env bash")
        assert "~/.codex/accounts/luna/auth.json" in cmd[-1]
        assert "~/.codex/accounts/herald/auth.json" in cmd[-1]
        assert "luna@example.invalid" in cmd[-1]
        assert "herald@example.invalid" in cmd[-1]
        assert "token" not in json.dumps(result).lower()

    def test_failed_execution_is_not_counted_applied(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout='{"ok": false, "verified": false, "stage": "preflight", "error": "active_auth_does_not_match_declared_current"}\n',
                stderr="",
            )

        result = apply_plan(
            artifact,
            accounts,
            config_digest_value=config_digest(config_file),
            policy=policy,
            confirm=True,
            executor=fake_run,
        )
        assert result["applied_count"] == 0
        assert result["failed_count"] == 2
        assert result["ok"] is False

    def test_rollback_result_is_failed_not_applied(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout='{"ok": false, "verified": false, "stage": "verify", "error": "active_identity_mismatch", "rolled_back": true}\n',
                stderr="",
            )

        result = apply_plan(
            artifact,
            accounts,
            config_digest_value=config_digest(config_file),
            policy=policy,
            confirm=True,
            executor=fake_run,
        )
        assert result["applied_count"] == 0
        assert result["failed_count"] == 2
        assert result["failed"][0]["exec_result"]["rolled_back"] is True

    def test_rollback_failure_result_is_failed_and_not_rolled_back(self, exhausted_luna, config_file):
        accounts, policy, ssh = exhausted_luna
        artifact = self._switch_artifact(accounts, policy, ssh, config_digest(config_file))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout='{"ok": false, "verified": false, "stage": "rollback", "error": "rollback_failed_after_active_identity_mismatch", "rolled_back": false}\n',
                stderr="",
            )

        result = apply_plan(
            artifact,
            accounts,
            config_digest_value=config_digest(config_file),
            policy=policy,
            confirm=True,
            executor=fake_run,
        )
        assert result["applied_count"] == 0
        assert result["failed_count"] == 2
        assert result["failed"][0]["exec_result"]["rolled_back"] is False
        assert result["failed"][0]["exec_result"]["error"].startswith("rollback_failed_after_")

    def test_confirmed_blocked_apply_sets_result_not_ok(self, accounts_with_quota, config_file):
        accounts, policy, _ = accounts_with_quota
        actions = generate_plan(accounts, policy, {})
        artifact = build_plan_artifact(actions, accounts, policy, config_digest(config_file), {})
        result = apply_plan(
            artifact,
            accounts,
            config_digest_value=config_digest(config_file),
            policy=policy,
            confirm=True,
        )
        assert result["blocked_count"] == 2
        assert result["ok"] is False


class TestRemoteSwitchScript:
    @staticmethod
    def _mode(path):
        return path.stat().st_mode & 0o777

    def test_static_script_enforces_modes_identity_and_rollback_contract(self):
        assert "current_source_mode_not_0600" in REMOTE_SWITCH_SCRIPT
        assert "target_source_mode_not_0600" in REMOTE_SWITCH_SCRIPT
        assert "backup_mode_not_0600" in REMOTE_SWITCH_SCRIPT
        assert "installed_auth_mode_not_0600" in REMOTE_SWITCH_SCRIPT
        assert "current_identity_mismatch" in REMOTE_SWITCH_SCRIPT
        assert "rollback_failed_after_" in REMOTE_SWITCH_SCRIPT
        assert "rolled_back" in REMOTE_SWITCH_SCRIPT

    def test_static_script_cleans_backup_before_pre_mutation_failures(self):
        assert "cleanup_backup_pre_mutation" in REMOTE_SWITCH_SCRIPT
        assert (
            'chmod 0600 "$backup" || { cleanup_backup_pre_mutation; '
            'fail mutate "backup_chmod_failed"; }'
        ) in REMOTE_SWITCH_SCRIPT
        assert (
            'if [[ "$(mode_octal "$backup")" != "0600" ]]; then\n'
            "  cleanup_backup_pre_mutation\n"
            '  fail mutate "backup_mode_not_0600"\n'
            "fi"
        ) in REMOTE_SWITCH_SCRIPT

    def test_real_script_switches_home_relative_paths_in_temp_home(self, tmp_path):
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        luna_dir = codex_dir / "accounts" / "luna"
        herald_dir = codex_dir / "accounts" / "herald"
        bin_dir = home / ".local" / "bin"
        luna_dir.mkdir(parents=True)
        herald_dir.mkdir(parents=True)
        bin_dir.mkdir(parents=True)

        current_auth = luna_dir / "auth.json"
        target_auth = herald_dir / "auth.json"
        active_auth = codex_dir / "auth.json"
        invocations_log = home / "codex-invocations.log"
        codex_cli = bin_dir / "codex"
        current_bytes = json.dumps(
            {"email": "luna@example.invalid", "marker": "CURRENT_AUTH_MARKER"},
            sort_keys=True,
        ).encode("utf-8")
        target_bytes = json.dumps(
            {"email": "herald@example.invalid", "marker": "TARGET_AUTH_MARKER"},
            sort_keys=True,
        ).encode("utf-8")
        current_auth.write_bytes(current_bytes)
        target_auth.write_bytes(target_bytes)
        active_auth.write_bytes(current_bytes)
        codex_cli.write_text(
            "#!/bin/sh\n"
            "printf '%s %s\\n' \"$1\" \"$2\" >> \"$HOME/codex-invocations.log\"\n"
            "if [ \"${1:-}\" = login ] && [ \"${2:-}\" = status ]; then\n"
            "  cat \"$HOME/.codex/auth.json\"\n"
            "fi\n"
            "exit 0\n"
        )
        for path in (current_auth, target_auth, active_auth):
            path.chmod(0o600)
        codex_cli.chmod(0o700)

        env = dict(os.environ)
        env["HOME"] = str(home)
        result = subprocess.run(
            [
                "/bin/bash",
                "-s",
                "--",
                "~/.codex/accounts/luna/auth.json",
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/auth.json",
                "luna@example.invalid",
                "herald@example.invalid",
                "~/.local/bin/codex",
            ],
            input=REMOTE_SWITCH_SCRIPT,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload["ok"] is True
        assert payload["verified"] is True
        assert payload["rolled_back"] is False
        assert active_auth.read_bytes() == target_bytes
        assert self._mode(active_auth) == 0o600
        backups = list(codex_dir.glob("auth.json.fleet-drain-backup.*"))
        assert len(backups) == 1
        assert self._mode(backups[0]) == 0o600
        combined_output = result.stdout + result.stderr
        assert "CURRENT_AUTH_MARKER" not in combined_output
        assert "TARGET_AUTH_MARKER" not in combined_output
        assert invocations_log.read_text().strip() == "login status"

    def test_real_script_rolls_back_when_codex_login_status_fails(self, tmp_path):
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        luna_dir = codex_dir / "accounts" / "luna"
        herald_dir = codex_dir / "accounts" / "herald"
        bin_dir = home / ".local" / "bin"
        luna_dir.mkdir(parents=True)
        herald_dir.mkdir(parents=True)
        bin_dir.mkdir(parents=True)

        current_auth = luna_dir / "auth.json"
        target_auth = herald_dir / "auth.json"
        active_auth = codex_dir / "auth.json"
        invocations_log = home / "codex-invocations.log"
        codex_cli = bin_dir / "codex"
        current_bytes = json.dumps(
            {"email": "luna@example.invalid", "marker": "CURRENT_AUTH_MARKER"},
            sort_keys=True,
        ).encode("utf-8")
        target_bytes = json.dumps(
            {"email": "herald@example.invalid", "marker": "TARGET_AUTH_MARKER"},
            sort_keys=True,
        ).encode("utf-8")
        current_auth.write_bytes(current_bytes)
        target_auth.write_bytes(target_bytes)
        active_auth.write_bytes(current_bytes)
        codex_cli.write_text(
            "#!/bin/sh\n"
            "printf '%s %s\\n' \"$1\" \"$2\" >> \"$HOME/codex-invocations.log\"\n"
            "exit 1\n"
        )
        for path in (current_auth, target_auth, active_auth):
            path.chmod(0o600)
        codex_cli.chmod(0o700)

        env = dict(os.environ)
        env["HOME"] = str(home)
        result = subprocess.run(
            [
                "/bin/bash",
                "-s",
                "--",
                "~/.codex/accounts/luna/auth.json",
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/auth.json",
                "luna@example.invalid",
                "herald@example.invalid",
                "~/.local/bin/codex",
            ],
            input=REMOTE_SWITCH_SCRIPT,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload["ok"] is False
        assert payload["stage"] == "verify"
        assert payload["error"] == "codex_login_status_failed"
        assert payload["verified"] is False
        assert payload["rolled_back"] is True
        assert active_auth.read_bytes() == current_bytes
        assert self._mode(active_auth) == 0o600
        assert invocations_log.read_text().strip() == "login status"
        combined_output = result.stdout + result.stderr
        assert "CURRENT_AUTH_MARKER" not in combined_output
        assert "TARGET_AUTH_MARKER" not in combined_output


class TestStatus:
    def test_status_includes_account_and_surface_names(self, accounts_with_quota):
        accounts, policy, _ = accounts_with_quota
        status = format_status(accounts, policy)
        assert "luna" in status
        assert "herald" in status
        assert "enterprise:geordi" in status


class TestIdentityFailClosed:
    """Regression tests for identity-verification fail-open paths.

    These test that accounts with empty emails, auth JSON without identity
    keys, and malformed --current args all fail closed instead of silently
    proceeding.
    """

    def test_config_rejects_empty_email(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "no-email.yaml"
        cfg.write_text(
            sample_config_yaml.replace(
                "email: luna@example.invalid", "email: ''"
            )
        )
        with pytest.raises(ConfigError, match="non-empty email"):
            load_config(cfg)

    def test_config_rejects_missing_email(self, tmp_path, sample_config_yaml):
        cfg = tmp_path / "missing-email.yaml"
        lines = [
            line
            for line in sample_config_yaml.splitlines()
            if line.strip() != "email: herald@example.invalid"
        ]
        cfg.write_text("\n".join(lines))
        with pytest.raises(ConfigError, match="non-empty email"):
            load_config(cfg)

    def test_generate_plan_explicit_empty_does_not_fallthrough(
        self, tmp_path, sample_config_yaml
    ):
        """Empty string in explicit_current should not fall through to
        declared_current via ``or`` semantics."""
        cfg = tmp_path / "test.yaml"
        cfg.write_text(sample_config_yaml)
        accounts, policy, ssh = load_config(cfg)
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
        actions = generate_plan(accounts, policy, {"enterprise:geordi": ""})
        matching = [a for a in actions if a.surface_id == "enterprise:geordi"]
        assert len(matching) == 1
        action = matching[0]
        assert action.is_blocked

    def test_remote_script_rejects_absent_identity_in_target_auth(self, tmp_path):
        """Auth JSON with no email/login/username keys must fail, not pass."""
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        luna_dir = codex_dir / "accounts" / "luna"
        herald_dir = codex_dir / "accounts" / "herald"
        bin_dir = home / ".local" / "bin"
        luna_dir.mkdir(parents=True)
        herald_dir.mkdir(parents=True)
        bin_dir.mkdir(parents=True)

        current_auth = luna_dir / "auth.json"
        target_auth = herald_dir / "auth.json"
        active_auth = codex_dir / "auth.json"
        codex_cli = bin_dir / "codex"

        current_bytes = json.dumps(
            {"email": "luna@example.invalid"}, sort_keys=True
        ).encode("utf-8")
        target_bytes = json.dumps(
            {"some_other_key": "value"}, sort_keys=True
        ).encode("utf-8")

        current_auth.write_bytes(current_bytes)
        target_auth.write_bytes(target_bytes)
        active_auth.write_bytes(current_bytes)
        codex_cli.write_text('#!/bin/sh\nexit 0\n')

        for path in (current_auth, target_auth, active_auth):
            path.chmod(0o600)
        codex_cli.chmod(0o700)

        env = dict(os.environ)
        env["HOME"] = str(home)
        result = subprocess.run(
            [
                "/bin/bash",
                "-s",
                "--",
                "~/.codex/accounts/luna/auth.json",
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/auth.json",
                "luna@example.invalid",
                "herald@example.invalid",
                "~/.local/bin/codex",
            ],
            input=REMOTE_SWITCH_SCRIPT,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload["ok"] is False
        assert payload["stage"] == "preflight"
        assert "absent" in payload["error"]

    def test_remote_script_rejects_absent_identity_in_current_auth(self, tmp_path):
        """Current auth JSON with no identity keys must fail preflight."""
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        luna_dir = codex_dir / "accounts" / "luna"
        herald_dir = codex_dir / "accounts" / "herald"
        bin_dir = home / ".local" / "bin"
        luna_dir.mkdir(parents=True)
        herald_dir.mkdir(parents=True)
        bin_dir.mkdir(parents=True)

        current_auth = luna_dir / "auth.json"
        target_auth = herald_dir / "auth.json"
        active_auth = codex_dir / "auth.json"
        codex_cli = bin_dir / "codex"

        current_bytes = json.dumps(
            {"random_key": "no-identity-here"}, sort_keys=True
        ).encode("utf-8")
        target_bytes = json.dumps(
            {"email": "herald@example.invalid"}, sort_keys=True
        ).encode("utf-8")

        current_auth.write_bytes(current_bytes)
        target_auth.write_bytes(target_bytes)
        active_auth.write_bytes(current_bytes)
        codex_cli.write_text('#!/bin/sh\nexit 0\n')

        for path in (current_auth, target_auth, active_auth):
            path.chmod(0o600)
        codex_cli.chmod(0o700)

        env = dict(os.environ)
        env["HOME"] = str(home)
        result = subprocess.run(
            [
                "/bin/bash",
                "-s",
                "--",
                "~/.codex/accounts/luna/auth.json",
                "~/.codex/accounts/herald/auth.json",
                "~/.codex/auth.json",
                "luna@example.invalid",
                "herald@example.invalid",
                "~/.local/bin/codex",
            ],
            input=REMOTE_SWITCH_SCRIPT,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload["ok"] is False
        assert payload["stage"] == "preflight"
        assert "absent" in payload["error"]


class TestCliCurrentArgsValidation:
    """Regression tests for parse_current_args fail-open."""

    def test_rejects_empty_account_value(self):
        sys.path.insert(0, str(SCRIPT_DIR))
        from fleet_drain_cli import parse_current_args

        with pytest.raises(ValueError, match="empty surface or account"):
            parse_current_args(["enterprise:geordi="])

    def test_rejects_empty_surface(self):
        sys.path.insert(0, str(SCRIPT_DIR))
        from fleet_drain_cli import parse_current_args

        with pytest.raises(ValueError, match="empty surface or account"):
            parse_current_args(["=luna"])

    def test_rejects_no_equals_sign(self):
        sys.path.insert(0, str(SCRIPT_DIR))
        from fleet_drain_cli import parse_current_args

        with pytest.raises(ValueError, match="expected surface=account"):
            parse_current_args(["just_a_string"])
