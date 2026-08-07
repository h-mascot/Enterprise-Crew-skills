import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))


def load_fleet():
    import fleet_controller

    return fleet_controller


def keyring_status(
    active="primary",
    primary_floor=80,
    alternate_floor=90,
    alternate_alias="alternate",
    observed_at="2026-08-07T12:00:00Z",
    alternate_observed_at="2026-08-07T12:00:00Z",
    alternate_confidence="exact",
    alternate_health="healthy",
    alternate_manual_only=False,
):
    return {
        "state": {"activeAlias": active, "autoSwitch": True, "updatedAt": "2026-08-07T08:00:00Z"},
        "aliases": [
            {
                "alias": "primary",
                "limit5hRemainingPercent": primary_floor,
                "limitWeekRemainingPercent": primary_floor,
                "quotaObservedAt": observed_at,
                "confidence": "exact" if primary_floor is not None else "estimated",
                "health": "degraded",
                "manualOnly": False,
                "active": active == "primary",
            },
            {
                "alias": alternate_alias,
                "limit5hRemainingPercent": alternate_floor,
                "limitWeekRemainingPercent": alternate_floor,
                "quotaObservedAt": alternate_observed_at,
                "confidence": alternate_confidence,
                "health": alternate_health,
                "manualOnly": alternate_manual_only,
                "active": active == alternate_alias,
            },
        ],
    }


def actual_keyring_status_without_quota_timestamps(active="primary-codex", primary_floor=25, standby_floor=None):
    return {
        "state": {
            "activeAlias": active,
            "autoSwitch": True,
            "updatedAt": "2026-08-07T08:00:00Z",
        },
        "aliases": [
            {
                "alias": "primary-codex",
                "limit5hRemainingPercent": primary_floor,
                "limitWeekRemainingPercent": primary_floor,
                "confidence": "exact",
                "health": "healthy",
                "manualOnly": False,
                "active": active == "primary-codex",
            },
            {
                "alias": "standby-codex",
                "limit5hRemainingPercent": standby_floor,
                "limitWeekRemainingPercent": standby_floor,
                "confidence": "estimated",
                "health": "unknown",
                "manualOnly": False,
                "active": active == "standby-codex",
            },
        ],
    }


def agent(agent_id, transport=None, timeout_seconds=None):
    configured = {
        "id": agent_id,
        "drain_command": ["agentctl", "admission", "close", "--agent", "{agent_id}"],
        "resume_command": ["agentctl", "admission", "open", "--agent", "{agent_id}"],
        "fallback_command": ["agentctl", "route", "--agent", "{agent_id}", "--provider", "anthropic"],
        "restore_command": ["agentctl", "route", "--agent", "{agent_id}", "--provider", "codex"],
    }
    if transport is not None:
        configured["transport"] = transport
    if timeout_seconds is not None:
        configured["timeout_seconds"] = timeout_seconds
    return configured


def config_for(host=None, **policy):
    merged_policy = {
        "drain_threshold_percent": 20,
        "recovery_threshold_percent": 35,
        "max_status_age_seconds": 900,
        "allow_unknown_quota": False,
        "default_action_timeout_seconds": 60,
        "switch_cooldown_seconds": 600,
    }
    merged_policy.update(policy)
    return {
        "policy": merged_policy,
        "hosts": [
            host
            or {
                "id": "enterprise-geordi",
                "transport": {"type": "local"},
                "agents": [agent("book"), agent("ada")],
            }
        ],
    }


class FleetControllerTests(unittest.TestCase):
    def test_account_floor_uses_lower_known_codex_keyring_shape(self):
        fleet = load_fleet()
        status = fleet.parse_keyring_status(
            {
                "state": {"activeAlias": "alias-a"},
                "aliases": {
                    "alias-a": {
                        "limit5hRemainingPercent": 83,
                        "limitWeekRemainingPercent": 27,
                        "checkedAt": "2026-08-07T12:00:00Z",
                    }
                },
            },
            now="2026-08-07T12:05:00Z",
            max_age_seconds=900,
        )

        self.assertEqual(status.active_alias, "alias-a")
        self.assertEqual(status.accounts["alias-a"].floor_percent, 27)
        self.assertEqual(status.accounts["alias-a"].checked_at, "2026-08-07T12:00:00Z")
        self.assertFalse(status.accounts["alias-a"].stale)

    def test_actual_keyring_list_shape_without_quota_timestamps_blocks_primary_at_25(self):
        fleet = load_fleet()
        cfg = config_for()

        plan = fleet.plan_fleet(
            cfg,
            {"enterprise-geordi": actual_keyring_status_without_quota_timestamps(primary_floor=25)},
            state={},
            now="2026-08-07T12:05:00Z",
        )

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")
        self.assertNotIn("fallback", json.dumps(plan))
        self.assertNotIn("keyring_switch", json.dumps(plan))

    def test_read_statuses_path_does_not_invent_quota_timestamp(self):
        fleet = load_fleet()
        captured = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                captured.append((list(argv), timeout))
                return fleet.CommandResult(
                    returncode=0,
                    stdout=json.dumps(actual_keyring_status_without_quota_timestamps(primary_floor=25)),
                )

        cfg = config_for()
        statuses = fleet.read_statuses(cfg, executor=FakeExecutor())
        plan = fleet.plan_fleet(
            cfg,
            statuses,
            state={},
            now="2026-08-07T12:05:00Z",
        )

        self.assertIn("enterprise-geordi", statuses)
        self.assertNotIn("quotaStatusAt", statuses["enterprise-geordi"])
        self.assertEqual(captured[0][0][-2:], ["status", "--json"])
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")
        self.assertEqual(plan["hosts"][0]["status_reason"], "stale_quota")

    def test_actual_list_shape_preserves_keyring_metadata(self):
        fleet = load_fleet()

        status = fleet.parse_keyring_status(
            {
                "state": {"activeAlias": "primary-codex"},
                "aliases": [
                    {
                        "alias": "primary-codex",
                        "limit5hRemainingPercent": 25,
                        "limitWeekRemainingPercent": 25,
                        "quotaObservedAt": "2026-08-07T12:00:00Z",
                        "confidence": "exact",
                        "health": "degraded",
                        "manualOnly": False,
                        "active": True,
                    }
                ],
            },
            now="2026-08-07T12:05:00Z",
            max_age_seconds=900,
        )

        account = status.accounts["primary-codex"]
        self.assertEqual(account.confidence, "exact")
        self.assertEqual(account.health, "degraded")
        self.assertFalse(account.manual_only)
        self.assertTrue(account.active)

    def test_low_active_switch_plan_drains_all_agents_and_resumes_without_worker_restart(self):
        fleet = load_fleet()
        cfg = config_for()
        statuses = {"enterprise-geordi": keyring_status(primary_floor=19, alternate_floor=75)}
        plan = fleet.plan_fleet(cfg, statuses, state={}, now="2026-08-07T12:05:00Z")

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertEqual(
            kinds,
            [
                "agent_drain",
                "agent_drain",
                "keyring_auto_off",
                "keyring_switch",
                "agent_resume",
                "agent_resume",
            ],
        )
        self.assertEqual({a.get("agent") for a in plan["actions"] if a.get("agent")}, {"book", "ada"})
        self.assertEqual(plan["actions"][3]["target_alias"], "alternate")
        self.assertTrue(all(action["timeout_seconds"] > 0 for action in plan["actions"]))
        joined = " ".join(" ".join(a["argv"]) for a in plan["actions"])
        self.assertNotIn("kill", joined)
        self.assertNotIn("restart", joined)

    def test_live_keyring_active_current_and_ready_standby_switches_under_default_policy(self):
        fleet = load_fleet()
        cfg = config_for()
        statuses = {
            "enterprise-geordi": {
                "state": {
                    "activeAlias": "primary-codex",
                    "autoSwitch": True,
                    "updatedAt": "2026-08-07T12:00:00Z",
                },
                "aliases": [
                    {
                        "alias": "primary-codex",
                        "active": True,
                        "confidence": "exact",
                        "health": "active",
                        "manualOnly": False,
                        "quotaObservedAt": "2026-08-07T12:00:00Z",
                        "limit5hRemainingPercent": 15,
                        "limitWeekRemainingPercent": 15,
                    },
                    {
                        "alias": "standby-codex",
                        "active": False,
                        "confidence": "exact",
                        "health": "ready",
                        "manualOnly": False,
                        "quotaObservedAt": "2026-08-07T12:00:00Z",
                        "limit5hRemainingPercent": 80,
                        "limitWeekRemainingPercent": 80,
                    },
                ],
            }
        }

        plan = fleet.plan_fleet(cfg, statuses, state={}, now="2026-08-07T12:05:00Z")

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertIn("keyring_switch", kinds)
        switch = next(action for action in plan["actions"] if action["kind"] == "keyring_switch")
        self.assertEqual(switch["target_alias"], "standby-codex")
        self.assertEqual(plan["planned_state"]["hosts"]["enterprise-geordi"]["active_alias"], "standby-codex")

    def test_no_alternate_routes_every_declared_agent_to_fallback_then_resumes(self):
        fleet = load_fleet()
        host = {
            "id": "mascotm3-geordi",
            "transport": {"type": "local"},
            "agents": [agent("spock"), agent("scotty"), agent("zora")],
        }
        cfg = config_for(host=host)
        statuses = {"mascotm3-geordi": keyring_status(primary_floor=4, alternate_floor=18)}
        plan = fleet.plan_fleet(cfg, statuses, state={}, now="2026-08-07T12:05:00Z")

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertEqual(
            kinds,
            [
                "agent_drain",
                "agent_drain",
                "agent_drain",
                "agent_fallback",
                "agent_fallback",
                "agent_fallback",
                "agent_resume",
                "agent_resume",
                "agent_resume",
            ],
        )
        self.assertNotIn("keyring_switch", kinds)
        self.assertEqual({a["agent"] for a in plan["actions"] if a["kind"] == "agent_fallback"}, {"spock", "scotty", "zora"})

    def test_fallback_hysteresis_restores_only_at_recovery_threshold(self):
        fleet = load_fleet()
        cfg = config_for()
        state = {"hosts": {"enterprise-geordi": {"mode": "fallback"}}}

        below = fleet.plan_fleet(
            cfg,
            {"enterprise-geordi": keyring_status(primary_floor=34, alternate_floor=20)},
            state=state,
            now="2026-08-07T12:05:00Z",
        )
        self.assertEqual(below["actions"], [])
        self.assertEqual(below["hosts"][0]["decision"], "wait_for_recovery")

        recovered = fleet.plan_fleet(
            cfg,
            {"enterprise-geordi": keyring_status(primary_floor=35, alternate_floor=80)},
            state=state,
            now="2026-08-07T12:05:00Z",
        )
        self.assertEqual(
            [a["kind"] for a in recovered["actions"]],
            ["agent_drain", "agent_drain", "agent_restore", "agent_restore", "agent_resume", "agent_resume"],
        )

    def test_fallback_hysteresis_switches_to_healthy_alternate_before_restore(self):
        fleet = load_fleet()
        cfg = config_for()
        state = {"hosts": {"enterprise-geordi": {"mode": "fallback", "active_alias": "primary"}}}

        plan = fleet.plan_fleet(
            cfg,
            {"enterprise-geordi": keyring_status(primary_floor=25, alternate_floor=80)},
            state=state,
            now="2026-08-07T12:05:00Z",
        )

        self.assertEqual(
            [a["kind"] for a in plan["actions"]],
            [
                "agent_drain",
                "agent_drain",
                "keyring_auto_off",
                "keyring_switch",
                "agent_restore",
                "agent_restore",
                "agent_resume",
                "agent_resume",
            ],
        )
        self.assertEqual(plan["actions"][3]["target_alias"], "alternate")
        self.assertEqual(plan["planned_state"]["hosts"]["enterprise-geordi"]["mode"], "codex")
        self.assertEqual(plan["planned_state"]["hosts"]["enterprise-geordi"]["active_alias"], "alternate")
        self.assertEqual(plan["hosts"][0]["decision"], "switch_account_restore_codex")

    def test_unknown_stale_or_missing_active_status_blocks_without_fallback(self):
        fleet = load_fleet()
        cases = {
            "missing": {"state": {"activeAlias": "missing"}, "aliases": []},
            "unknown": keyring_status(primary_floor=None, alternate_floor=80),
            "stale": keyring_status(
                primary_floor=25,
                alternate_floor=80,
                observed_at="2026-08-07T11:00:00Z",
            ),
        }

        for name, payload in cases.items():
            with self.subTest(name=name):
                plan = fleet.plan_fleet(
                    config_for(),
                    {"enterprise-geordi": payload},
                    state={},
                    now="2026-08-07T12:05:00Z",
                )
                self.assertEqual(plan["actions"], [])
                self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")

    def test_stale_unknown_manual_or_non_exact_alternates_are_never_selected(self):
        fleet = load_fleet()
        blocked_alternates = [
            keyring_status(primary_floor=3, alternate_floor=None),
            keyring_status(primary_floor=3, alternate_floor=80, alternate_observed_at="2026-08-07T11:00:00Z"),
            keyring_status(primary_floor=3, alternate_floor=80, alternate_confidence="estimated"),
            keyring_status(primary_floor=3, alternate_floor=80, alternate_manual_only=True),
            keyring_status(primary_floor=3, alternate_floor=80, alternate_health="unknown"),
        ]

        for payload in blocked_alternates:
            plan = fleet.plan_fleet(
                config_for(),
                {"enterprise-geordi": payload},
                state={},
                now="2026-08-07T12:05:00Z",
            )
            kinds = [a["kind"] for a in plan["actions"]]
            self.assertIn("agent_fallback", kinds)
            self.assertNotIn("keyring_switch", kinds)

    def test_quota_percent_outside_zero_to_one_hundred_is_unknown_and_blocks_active(self):
        fleet = load_fleet()

        for value in (-1, 101):
            with self.subTest(value=value):
                plan = fleet.plan_fleet(
                    config_for(),
                    {"enterprise-geordi": keyring_status(primary_floor=value, alternate_floor=80)},
                    state={},
                    now="2026-08-07T12:05:00Z",
                )
                self.assertEqual(plan["actions"], [])
                self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")
                self.assertEqual(plan["hosts"][0]["status_reason"], "unknown_quota")

    def test_non_finite_quota_percent_is_unknown_and_blocks_active(self):
        fleet = load_fleet()

        for value in ("NaN", "inf", "-inf"):
            with self.subTest(value=value):
                self.assertIsNone(fleet.parse_percent(value))
                plan = fleet.plan_fleet(
                    config_for(),
                    {"enterprise-geordi": keyring_status(primary_floor=value, alternate_floor=80)},
                    state={},
                    now="2026-08-07T12:05:00Z",
                )
                self.assertEqual(plan["actions"], [])
                self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")
                self.assertEqual(plan["hosts"][0]["status_reason"], "unknown_quota")

    def test_quota_percent_outside_zero_to_one_hundred_is_not_eligible_alternate(self):
        fleet = load_fleet()

        for value in (-1, 101):
            with self.subTest(value=value):
                plan = fleet.plan_fleet(
                    config_for(),
                    {"enterprise-geordi": keyring_status(primary_floor=3, alternate_floor=value)},
                    state={},
                    now="2026-08-07T12:05:00Z",
                )
                kinds = [a["kind"] for a in plan["actions"]]
                self.assertIn("agent_fallback", kinds)
                self.assertNotIn("keyring_switch", kinds)

    def test_non_finite_quota_percent_is_not_eligible_alternate(self):
        fleet = load_fleet()

        for value in ("NaN", "inf", "-inf"):
            with self.subTest(value=value):
                plan = fleet.plan_fleet(
                    config_for(),
                    {"enterprise-geordi": keyring_status(primary_floor=3, alternate_floor=value)},
                    state={},
                    now="2026-08-07T12:05:00Z",
                )
                kinds = [a["kind"] for a in plan["actions"]]
                self.assertIn("agent_fallback", kinds)
                self.assertNotIn("keyring_switch", kinds)

    def test_non_finite_policy_thresholds_are_rejected(self):
        fleet = load_fleet()

        for key in ("drain_threshold_percent", "recovery_threshold_percent"):
            for value in ("NaN", "inf", "-inf", float("nan"), float("inf"), float("-inf")):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        fleet.validate_config(config_for(**{key: value}))

    def test_policy_thresholds_are_configurable_with_valid_hysteresis(self):
        fleet = load_fleet()

        policies = [
            {"drain_threshold_percent": 25, "recovery_threshold_percent": 40},
            {"drain_threshold_percent": 10, "recovery_threshold_percent": 30},
            {"drain_threshold_percent": 0, "recovery_threshold_percent": 100},
        ]
        for policy in policies:
            with self.subTest(policy=policy):
                fleet.validate_config(config_for(**policy))

    def test_top_level_quota_timestamp_does_not_make_alias_quota_fresh(self):
        fleet = load_fleet()
        payload = keyring_status(primary_floor=10, alternate_floor=80)
        payload["quotaObservedAt"] = "2026-08-07T12:00:00Z"
        payload["state"]["quotaObservedAt"] = "2026-08-07T12:00:00Z"
        for account in payload["aliases"]:
            account.pop("quotaObservedAt", None)

        plan = fleet.plan_fleet(
            config_for(),
            {"enterprise-geordi": payload},
            state={},
            now="2026-08-07T12:05:00Z",
        )

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")
        self.assertEqual(plan["hosts"][0]["status_reason"], "stale_quota")

    def test_top_level_quota_timestamp_does_not_make_alternate_quota_fresh(self):
        fleet = load_fleet()
        payload = keyring_status(primary_floor=10, alternate_floor=80)
        payload["quotaObservedAt"] = "2026-08-07T12:00:00Z"
        payload["state"]["quotaObservedAt"] = "2026-08-07T12:00:00Z"
        payload["aliases"][1].pop("quotaObservedAt", None)

        plan = fleet.plan_fleet(
            config_for(),
            {"enterprise-geordi": payload},
            state={},
            now="2026-08-07T12:05:00Z",
        )

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertEqual(plan["hosts"][0]["decision"], "fallback")
        self.assertIn("agent_fallback", kinds)
        self.assertNotIn("keyring_switch", kinds)

    def test_future_quota_observation_is_stale(self):
        fleet = load_fleet()

        active_future = fleet.plan_fleet(
            config_for(),
            {
                "enterprise-geordi": keyring_status(
                    primary_floor=10,
                    alternate_floor=80,
                    observed_at="2026-08-07T12:30:00Z",
                )
            },
            state={},
            now="2026-08-07T12:05:00Z",
        )
        self.assertEqual(active_future["actions"], [])
        self.assertEqual(active_future["hosts"][0]["status_reason"], "stale_quota")

        alternate_future = fleet.plan_fleet(
            config_for(),
            {
                "enterprise-geordi": keyring_status(
                    primary_floor=3,
                    alternate_floor=80,
                    alternate_observed_at="2026-08-07T12:30:00Z",
                )
            },
            state={},
            now="2026-08-07T12:05:00Z",
        )
        kinds = [a["kind"] for a in alternate_future["actions"]]
        self.assertIn("agent_fallback", kinds)
        self.assertNotIn("keyring_switch", kinds)

    def test_agent_transport_can_differ_from_quota_host_transport(self):
        fleet = load_fleet()
        host = {
            "id": "enterprise-shared-quota",
            "transport": {"type": "local"},
            "agents": [
                agent("book"),
                agent("ada", transport={"type": "ssh", "host": "ada.example.invalid", "user": "ops"}),
            ],
        }
        plan = fleet.plan_fleet(
            config_for(host=host),
            {"enterprise-shared-quota": keyring_status(primary_floor=5, alternate_floor=10)},
            state={},
            now="2026-08-07T12:05:00Z",
        )

        ada_actions = [action for action in plan["actions"] if action.get("agent") == "ada"]
        book_actions = [action for action in plan["actions"] if action.get("agent") == "book"]
        self.assertTrue(ada_actions)
        self.assertTrue(all(action["argv"][0] == "ssh" for action in ada_actions))
        self.assertTrue(all(action["argv"][0] == "agentctl" for action in book_actions))
        self.assertEqual(plan["actions"][0]["argv"][0], "agentctl")

    def test_switch_cooldown_blocks_repeat_mutation_until_exhausted(self):
        fleet = load_fleet()
        state = {"hosts": {"enterprise-geordi": {"mode": "codex", "switched_at": "2026-08-07T12:00:00Z"}}}

        waiting = fleet.plan_fleet(
            config_for(),
            {"enterprise-geordi": keyring_status(primary_floor=10, alternate_floor=80)},
            state=state,
            now="2026-08-07T12:05:00Z",
        )
        self.assertEqual(waiting["actions"], [])
        self.assertEqual(waiting["hosts"][0]["decision"], "wait_switch_cooldown")

        exhausted = fleet.plan_fleet(
            config_for(),
            {"enterprise-geordi": keyring_status(primary_floor=0, alternate_floor=80)},
            state=state,
            now="2026-08-07T12:05:00Z",
        )
        self.assertIn("keyring_switch", [a["kind"] for a in exhausted["actions"]])

    def test_status_command_overrides_default_keyring_status_command(self):
        fleet = load_fleet()
        captured = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                captured.append((list(argv), timeout))
                return fleet.CommandResult(returncode=0, stdout=json.dumps(keyring_status()))

        host = {
            "id": "enterprise-geordi",
            "transport": {"type": "local"},
            "status_command": ["python3", "{skill_dir}/scripts/codex-keyring-status.py", "--json"],
            "agents": [agent("book")],
        }
        statuses = fleet.read_statuses(config_for(host=host), executor=FakeExecutor())

        self.assertIn("enterprise-geordi", statuses)
        self.assertEqual(captured[0][0], ["python3", str(ROOT / "scripts" / "codex-keyring-status.py"), "--json"])
        self.assertGreater(captured[0][1], 0)

    def test_string_false_auto_switch_parses_as_false(self):
        fleet = load_fleet()

        status = fleet.parse_keyring_status(
            {
                "state": {"activeAlias": "primary", "autoSwitch": "false"},
                "aliases": [
                    {
                        "alias": "primary",
                        "active": True,
                        "limit5hRemainingPercent": 80,
                        "limitWeekRemainingPercent": 80,
                        "quotaObservedAt": "2026-08-07T12:00:00Z",
                    }
                ],
            },
            now="2026-08-07T12:05:00Z",
            max_age_seconds=900,
        )

        self.assertIs(status.auto_switch, False)

    def test_apply_verify_argv_accepts_snake_case_state_and_string_false(self):
        fleet = load_fleet()

        class FakeExecutor:
            def run(self, argv, timeout=None):
                if list(argv) == ["codex-keyring", "status", "--json"]:
                    return fleet.CommandResult(
                        returncode=0,
                        stdout=json.dumps({"state": {"active_alias": "alternate", "auto_switch": "false"}}),
                    )
                return fleet.CommandResult(returncode=0)

        plan = {
            "actions": [
                {
                    "kind": "keyring_switch",
                    "host": "h1",
                    "target_alias": "alternate",
                    "argv": ["codex-keyring", "switch", "alternate"],
                    "verify_argv": ["codex-keyring", "status", "--json"],
                    "verify_expected_active_alias": "alternate",
                    "verify_expected_auto_switch": False,
                }
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex", "active_alias": "alternate"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(plan, state={}, executor=FakeExecutor(), state_path=state_path)
            persisted = json.loads(state_path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(result["attempted_actions"][0]["status"], "ok")
        self.assertEqual(persisted["hosts"]["h1"]["active_alias"], "alternate")
        self.assertNotIn("partial_failure", persisted)

    def test_apply_keyring_verify_accepts_snake_case_state_and_string_false(self):
        fleet = load_fleet()

        class FakeExecutor:
            def run(self, argv, timeout=None):
                return fleet.CommandResult(
                    returncode=0,
                    stdout=json.dumps({"state": {"active_alias": "alternate", "auto_switch": "false"}}),
                )

        plan = {
            "actions": [
                {
                    "kind": "keyring_verify",
                    "host": "h1",
                    "argv": ["codex-keyring", "status", "--json"],
                    "expected_active_alias": "alternate",
                    "expected_auto_switch": False,
                }
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex", "active_alias": "alternate"}}},
        }

        result = fleet.apply_plan(plan, state={}, executor=FakeExecutor())

        self.assertTrue(result["ok"])
        self.assertEqual(result["attempted_actions"][0]["status"], "ok")

    def test_ssh_keyring_commands_quote_remote_arguments(self):
        fleet = load_fleet()
        host = {
            "id": "remote",
            "transport": {"type": "ssh", "host": "codex.example.invalid", "user": "codex", "port": 2222},
        }

        argv = fleet.build_keyring_argv(host, ["switch", "alias with spaces; rm -rf /"])

        self.assertEqual(argv[:3], ["ssh", "-p", "2222"])
        self.assertEqual(argv[3], "codex@codex.example.invalid")
        self.assertEqual(argv[-1], "codex-keyring switch 'alias with spaces; rm -rf /'")

    def test_apply_partial_failure_persists_truth_and_blocks_future_plans(self):
        fleet = load_fleet()
        executed = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                executed.append((list(argv), timeout))
                if len(executed) == 2:
                    return fleet.CommandResult(returncode=2)
                return fleet.CommandResult(returncode=0)

        plan = {
            "actions": [
                {"kind": "agent_drain", "host": "h1", "agent": "book", "argv": ["agentctl", "--token", "SECRET_TOKEN", "close"]},
                {"kind": "keyring_auto_off", "host": "h1", "argv": ["codex-keyring", "auto", "off"]},
                {"kind": "keyring_switch", "host": "h1", "target_alias": "alternate", "argv": ["codex-keyring", "switch", "alternate"]},
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex", "active_alias": "alternate"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(plan, state={"hosts": {"h1": {"mode": "codex"}}}, executor=FakeExecutor(), receipt_path=receipt_path, state_path=state_path)
            receipt_text = receipt_path.read_text()
            persisted = json.loads(state_path.read_text())

        self.assertFalse(result["ok"])
        self.assertEqual(len(executed), 2)
        self.assertEqual(result["completed_action_count"], 1)
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 1)
        self.assertEqual(persisted["hosts"]["h1"]["mode"], "partial_failure")
        self.assertNotIn("SECRET_TOKEN", receipt_text)
        self.assertIn("<redacted>", receipt_text)

        blocked = fleet.plan_fleet(
            config_for(host={"id": "h1", "transport": {"type": "local"}, "agents": [agent("book")]}),
            {"h1": keyring_status()},
            state=persisted,
            now="2026-08-07T12:05:00Z",
        )
        self.assertEqual(blocked["actions"], [])
        self.assertEqual(blocked["hosts"][0]["decision"], "blocked_partial_failure")

    def test_apply_first_action_failure_persists_partial_failure_when_state_path_exists(self):
        fleet = load_fleet()

        class FailingExecutor:
            def run(self, argv, timeout=None):
                return fleet.CommandResult(returncode=9)

        plan = {
            "actions": [
                {"kind": "agent_drain", "host": "h1", "agent": "book", "argv": ["agentctl", "close"]},
                {"kind": "agent_resume", "host": "h1", "agent": "book", "argv": ["agentctl", "open"]},
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(
                plan,
                state={"hosts": {"h1": {"mode": "codex"}}},
                executor=FailingExecutor(),
                state_path=state_path,
            )
            persisted = json.loads(state_path.read_text())

        self.assertFalse(result["ok"])
        self.assertEqual(result["completed_action_count"], 0)
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 0)
        self.assertEqual(persisted["hosts"]["h1"]["mode"], "partial_failure")

    def test_apply_executor_oserror_after_prior_success_records_partial_failure(self):
        fleet = load_fleet()
        executed = []

        class LaunchFailingExecutor:
            def run(self, argv, timeout=None):
                executed.append(list(argv))
                if len(executed) == 2:
                    raise OSError("permission denied for SECRET_TOKEN")
                return fleet.CommandResult(returncode=0)

        plan = {
            "actions": [
                {"kind": "agent_drain", "host": "h1", "agent": "book", "argv": ["agentctl", "--token", "SECRET_TOKEN", "close"]},
                {"kind": "keyring_auto_off", "host": "h1", "argv": ["codex-keyring", "auto", "off"]},
                {"kind": "keyring_switch", "host": "h1", "target_alias": "alternate", "argv": ["codex-keyring", "switch", "alternate"]},
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex", "active_alias": "alternate"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(
                plan,
                state={"hosts": {"h1": {"mode": "codex"}}},
                executor=LaunchFailingExecutor(),
                receipt_path=receipt_path,
                state_path=state_path,
            )
            receipt_text = receipt_path.read_text()
            persisted = json.loads(state_path.read_text())

        self.assertFalse(result["ok"])
        self.assertEqual(executed, [["agentctl", "--token", "SECRET_TOKEN", "close"], ["codex-keyring", "auto", "off"]])
        self.assertEqual(result["completed_action_count"], 1)
        self.assertEqual(result["attempted_actions"][0]["status"], "ok")
        self.assertEqual(result["failed_action_index"], 1)
        self.assertEqual(result["failed_action"]["status"], "failed")
        self.assertEqual(result["failed_action"]["error_class"], "OSError")
        self.assertIn("executor raised OSError", result["failed_action"]["error"])
        self.assertNotIn("SECRET_TOKEN", receipt_text)
        self.assertIn("<redacted>", receipt_text)
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 1)
        self.assertEqual(persisted["hosts"]["h1"]["mode"], "partial_failure")

    def test_apply_executor_oserror_on_first_action_fails_closed(self):
        fleet = load_fleet()
        executed = []

        class LaunchFailingExecutor:
            def run(self, argv, timeout=None):
                executed.append(list(argv))
                raise FileNotFoundError("missing SECRET_TOKEN launcher")

        plan = {
            "actions": [
                {"kind": "agent_drain", "host": "h1", "agent": "book", "argv": ["agentctl", "--token", "SECRET_TOKEN", "close"]},
                {"kind": "agent_resume", "host": "h1", "agent": "book", "argv": ["agentctl", "open"]},
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(
                plan,
                state={"hosts": {"h1": {"mode": "codex"}}},
                executor=LaunchFailingExecutor(),
                receipt_path=receipt_path,
                state_path=state_path,
            )
            receipt_text = receipt_path.read_text()
            persisted = json.loads(state_path.read_text())

        self.assertFalse(result["ok"])
        self.assertEqual(executed, [["agentctl", "--token", "SECRET_TOKEN", "close"]])
        self.assertEqual(result["completed_action_count"], 0)
        self.assertEqual(result["failed_action_index"], 0)
        self.assertEqual(result["failed_action"]["status"], "failed")
        self.assertEqual(result["failed_action"]["error_class"], "FileNotFoundError")
        self.assertIn("executor raised FileNotFoundError", result["failed_action"]["error"])
        self.assertNotIn("SECRET_TOKEN", receipt_text)
        self.assertIn("<redacted>", receipt_text)
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 0)
        self.assertEqual(persisted["hosts"]["h1"]["mode"], "partial_failure")

    def test_apply_uses_default_timeout_when_action_omits_timeout(self):
        fleet = load_fleet()
        timeouts = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                timeouts.append(timeout)
                return fleet.CommandResult(returncode=0)

        plan = {"actions": [{"kind": "noop", "host": "h1", "argv": ["true"]}], "planned_state": {"hosts": {}}}

        with tempfile.TemporaryDirectory() as tmp:
            result = fleet.apply_plan(plan, state={}, executor=FakeExecutor(), state_path=Path(tmp) / "state.json")

        self.assertTrue(result["ok"])
        self.assertEqual(timeouts, [60])

    def test_apply_lock_fails_closed_before_mutation(self):
        fleet = load_fleet()
        executed = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                executed.append(list(argv))
                return fleet.CommandResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "fleet.lock"
            lock_path.touch()
            with lock_path.open("r+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = fleet.apply_plan(
                    {"actions": [{"kind": "agent_drain", "host": "h1", "argv": ["agentctl", "close"]}]},
                    state={},
                    executor=FakeExecutor(),
                    receipt_path=Path(tmp) / "receipt.json",
                    lock_path=lock_path,
                )
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertFalse(result["ok"])
        self.assertFalse(result["lock_acquired"])
        self.assertEqual(executed, [])

    def test_remote_command_receipt_redacts_embedded_secret_argument(self):
        fleet = load_fleet()
        redacted = fleet.redact_argv(
            ["ssh", "agent@example.invalid", "bash -lc 'agentctl route --api-key=SECRET_TOKEN --provider codex'"]
        )

        rendered = " ".join(redacted)
        self.assertNotIn("SECRET_TOKEN", rendered)
        self.assertIn("<redacted>", rendered)

    def test_public_plan_redacts_values_after_bare_secret_keys(self):
        fleet = load_fleet()
        plan = {
            "actions": [
                {
                    "kind": "agent_drain",
                    "host": "h1",
                    "argv": [
                        "agentctl",
                        "password",
                        "hunter2",
                        "authorization",
                        "TOKEN",
                        "api_key",
                        "short-secret",
                        "route",
                        "codex",
                    ],
                },
                {
                    "kind": "agent_restore",
                    "host": "h1",
                    "argv": [
                        "ssh",
                        "agent@example.invalid",
                        "agentctl auth password hunter2 authorization TOKEN api_key short-secret route codex",
                    ],
                },
            ]
        }

        rendered = json.dumps(fleet.public_plan(plan))

        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("TOKEN", rendered)
        self.assertNotIn("short-secret", rendered)
        self.assertIn("codex", rendered)
        self.assertIn("<redacted>", rendered)

    def test_apply_receipt_redacts_values_after_bare_secret_keys(self):
        fleet = load_fleet()

        class FakeExecutor:
            def run(self, argv, timeout=None):
                return fleet.CommandResult(returncode=0)

        plan = {
            "actions": [
                {
                    "kind": "agent_drain",
                    "host": "h1",
                    "argv": ["agentctl", "authorization", "TOKEN", "password", "hunter2", "--label", "visible"],
                }
            ],
            "planned_state": {"hosts": {"h1": {"mode": "codex"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            fleet.apply_plan(plan, state={}, executor=FakeExecutor(), receipt_path=receipt_path)
            receipt_text = receipt_path.read_text()

        self.assertNotIn("TOKEN", receipt_text)
        self.assertNotIn("hunter2", receipt_text)
        self.assertIn("visible", receipt_text)
        self.assertIn("<redacted>", receipt_text)

    def test_apply_empty_action_argv_fails_closed_with_receipt(self):
        fleet = load_fleet()
        executed = []

        class FakeExecutor:
            def run(self, argv, timeout=None):
                executed.append(list(argv))
                return fleet.CommandResult(returncode=0)

        plan = {
            "actions": [{"kind": "agent_drain", "host": "h1", "agent": "book", "argv": []}],
            "planned_state": {"hosts": {"h1": {"mode": "codex"}}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            receipt_path = Path(tmp) / "receipt.json"
            state_path = Path(tmp) / "state.json"
            result = fleet.apply_plan(
                plan,
                state={"hosts": {"h1": {"mode": "codex"}}},
                executor=FakeExecutor(),
                receipt_path=receipt_path,
                state_path=state_path,
            )
            receipt = json.loads(receipt_path.read_text())
            persisted = json.loads(state_path.read_text())

        self.assertFalse(result["ok"])
        self.assertEqual(executed, [])
        self.assertEqual(result["failed_action_index"], 0)
        self.assertEqual(result["failed_action"]["status"], "failed")
        self.assertEqual(result["failed_action"]["error_class"], "ValueError")
        self.assertIn("non-empty argv array", result["failed_action"]["error"])
        self.assertEqual(receipt["failed_action"]["argv"], [])
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 0)

    def test_configuration_validation_fails_closed(self):
        fleet = load_fleet()
        valid = config_for()
        invalid_configs = [
            {**valid, "hosts": []},
            {**valid, "hosts": [{"id": "h", "transport": {"type": "local"}, "agents": []}, {"id": "h", "transport": {"type": "local"}, "agents": []}]},
            {**valid, "hosts": [{"transport": {"type": "local"}, "agents": [agent("book")]}]},
            {**valid, "hosts": [{"id": "h", "transport": {"type": "local"}, "agents": []}]},
            {**valid, "hosts": [{"id": "h", "transport": {"type": "local"}, "agents": [{"id": "disabled", "enabled": False}]}]},
            {**valid, "hosts": [{"id": "h", "transport": {"type": "bogus"}, "agents": []}]},
            {**valid, "hosts": [{"id": "h", "transport": {"type": "local"}, "status_command": "codex-keyring-status", "agents": [agent("book")]}]},
            config_for(drain_threshold_percent=101),
            config_for(recovery_threshold_percent=20),
            config_for(max_status_age_seconds=-1),
            config_for(default_action_timeout_seconds=0),
            config_for(switch_cooldown_seconds=-1),
            config_for(alternate_required_confidence="estimated"),
            config_for(alternate_health_allowlist=["unknown"]),
            {
                "policy": valid["policy"],
                "hosts": [
                    {
                        "id": "h",
                        "transport": {"type": "local"},
                        "agents": [agent("book"), agent("book")],
                    }
                ],
            },
            {
                "policy": valid["policy"],
                "hosts": [
                    {
                        "id": "h",
                        "transport": {"type": "local"},
                        "agents": [{"id": "book", "drain_command": "agentctl"}],
                    }
                ],
            },
        ]

        for cfg in invalid_configs:
            with self.subTest(cfg=cfg):
                with self.assertRaises(ValueError):
                    fleet.validate_config(cfg)

    def test_configuration_validation_rejects_empty_executable_commands(self):
        fleet = load_fleet()
        valid = config_for()
        command_keys = (
            "drain_command",
            "resume_command",
            "fallback_command",
            "restore_command",
            "refresh_command",
        )
        invalid_configs = [
            {
                "policy": valid["policy"],
                "hosts": [
                    {
                        "id": "h",
                        "transport": {"type": "local"},
                        "status_command": [],
                        "agents": [agent("book")],
                    }
                ],
            },
            {
                "policy": valid["policy"],
                "hosts": [
                    {
                        "id": "h",
                        "transport": {"type": "local"},
                        "keyring_binary": "",
                        "agents": [agent("book")],
                    }
                ],
            },
        ]
        for command_key in command_keys:
            configured_agent = agent("book")
            configured_agent[command_key] = []
            invalid_configs.append(
                {
                    "policy": valid["policy"],
                    "hosts": [
                        {
                            "id": "h",
                            "transport": {"type": "local"},
                            "agents": [configured_agent],
                        }
                    ],
                }
            )

        for cfg in invalid_configs:
            with self.subTest(cfg=cfg):
                with self.assertRaises(ValueError):
                    fleet.validate_config(cfg)

    def test_disabled_arbitrary_agents_remain_data_when_host_has_enabled_agent(self):
        fleet = load_fleet()

        cfg = config_for(
            host={
                "id": "h",
                "transport": {"type": "local"},
                "agents": [
                    agent("book"),
                    {"id": "future-agent", "enabled": False, "note": "private config will define commands later"},
                ],
            }
        )

        fleet.validate_config(cfg)

    def test_schema_version_accepts_supported_v1_and_rejects_unknown_public_shape(self):
        fleet = load_fleet()
        valid = config_for()

        fleet.validate_config(valid)
        fleet.validate_config({**valid, "schema_version": "2026-08-07.codex-fleet.v1"})
        fleet.validate_config(config_for(alternate_health_allowlist=["ready", "healthy", "ok"]))
        fleet.validate_config(config_for(alternate_health_allowlist=["active", "ready"]))
        with self.assertRaises(ValueError):
            fleet.validate_config({**valid, "schema_version": "v2"})

    def test_public_example_schema_and_topology_validate(self):
        fleet = load_fleet()
        example = json.loads((ROOT / "config" / "fleet.example.json").read_text())

        fleet.validate_config(example)
        host_ids = {host["id"] for host in example["hosts"]}
        self.assertIn("enterprise-shared-codex-quota", host_ids)
        self.assertIn("geordi-mascotm3-codex", host_ids)
        self.assertNotIn("geordi-enterprise-codex", host_ids)
        enterprise = next(host for host in example["hosts"] if host["id"] == "enterprise-shared-codex-quota")
        mascot = next(host for host in example["hosts"] if host["id"] == "geordi-mascotm3-codex")
        agent_enabled = {agent["id"]: agent.get("enabled", True) for agent in enterprise["agents"]}
        self.assertTrue(agent_enabled["book"])
        self.assertTrue(agent_enabled["ada"])
        self.assertTrue(agent_enabled["geordi-enterprise"])
        self.assertFalse(agent_enabled["spock"])
        self.assertFalse(agent_enabled["scotty"])
        self.assertFalse(agent_enabled["zora"])
        self.assertFalse(agent_enabled["midas"])
        self.assertFalse(agent_enabled["entitybuilder"])
        self.assertEqual([agent["id"] for agent in mascot["agents"] if agent.get("enabled", True)], ["geordi-mascotm3"])
        self.assertIn("{skill_dir}/scripts/codex-keyring-status.py", enterprise["status_command"])
        self.assertNotIn("quota-sensor", json.dumps(example))

    def test_public_mascotm3_low_no_alternate_falls_back_and_resumes(self):
        fleet = load_fleet()
        example = json.loads((ROOT / "config" / "fleet.example.json").read_text())
        mascot = next(host for host in example["hosts"] if host["id"] == "geordi-mascotm3-codex")
        cfg = {**example, "hosts": [mascot]}

        plan = fleet.plan_fleet(
            cfg,
            {"geordi-mascotm3-codex": keyring_status(primary_floor=4, alternate_floor=18)},
            state={},
            now="2026-08-07T12:05:00Z",
        )

        self.assertEqual(
            [action["kind"] for action in plan["actions"]],
            ["agent_drain", "agent_fallback", "agent_resume"],
        )
        self.assertEqual({action.get("agent") for action in plan["actions"]}, {"geordi-mascotm3"})

    def test_read_statuses_validates_before_running_commands(self):
        fleet = load_fleet()

        class ExplodingExecutor:
            def run(self, argv, timeout=None):
                raise AssertionError("status command should not run")

        with self.assertRaises(ValueError):
            fleet.read_statuses(config_for(drain_threshold_percent=200), executor=ExplodingExecutor())

    def test_default_status_read_does_not_invent_quota_freshness(self):
        fleet = load_fleet()

        class FakeExecutor:
            def run(self, argv, timeout=None):
                return fleet.CommandResult(
                    returncode=0,
                    stdout=json.dumps(actual_keyring_status_without_quota_timestamps(primary_floor=15)),
                )

        statuses = fleet.read_statuses(config_for(), executor=FakeExecutor())
        plan = fleet.plan_fleet(config_for(), statuses, state={}, now="2026-08-07T12:05:00Z")

        self.assertNotIn("quotaStatusAt", statuses["enterprise-geordi"])
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["hosts"][0]["decision"], "blocked_status")

    def test_plan_cli_dry_run_does_not_mutate_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_keyring = tmp_path / "codex-keyring"
            fake_keyring.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:] == ['status', '--json']:\n"
                "    print(json.dumps(json.load(open(sys.argv[0] + '.status'))))\n"
                "else:\n"
                "    open(sys.argv[0] + '.log', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            )
            fake_keyring.chmod(0o755)
            (tmp_path / "codex-keyring.status").write_text(json.dumps(keyring_status(primary_floor=10, alternate_floor=80)))

            cfg = config_for()
            cfg["hosts"][0]["keyring_binary"] = str(fake_keyring)
            config_path = tmp_path / "fleet.json"
            config_path.write_text(json.dumps(cfg))
            state_path = tmp_path / "state.json"
            receipt_dir = tmp_path / "receipts"

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "fleet.py"),
                    "plan",
                    "--config",
                    str(config_path),
                    "--state",
                    str(state_path),
                    "--receipt-dir",
                    str(receipt_dir),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            output = json.loads(proc.stdout)
            self.assertEqual(output["mode"], "plan")
            self.assertFalse(state_path.exists())
            self.assertFalse(receipt_dir.exists())
            self.assertFalse((tmp_path / "codex-keyring.log").exists())


if __name__ == "__main__":
    unittest.main()
