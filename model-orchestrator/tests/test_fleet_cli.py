import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "fleet.py"


KEYRING_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(sys.argv[0] + '.status')
log_path = Path(sys.argv[0] + '.log')
args = sys.argv[1:]
status = json.loads(state_path.read_text())
with log_path.open('a') as handle:
    handle.write(' '.join(args) + '\\n')
if args == ['status', '--json']:
    print(json.dumps(status))
elif args == ['auto', 'off']:
    status['state']['autoSwitch'] = False
    state_path.write_text(json.dumps(status))
elif len(args) == 2 and args[0] == 'switch':
    if not os.environ.get('FAKE_KEYRING_NOOP_SWITCH'):
        status['state']['activeAlias'] = args[1]
        for account in status['aliases']:
            account['active'] = account['alias'] == args[1]
        state_path.write_text(json.dumps(status))
else:
    raise SystemExit(2)
"""


AGENTCTL_SCRIPT = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ['FAKE_AGENTCTL_LOG']).open('a') as handle:
    handle.write(' '.join(args) + '\\n')
fail_match = os.environ.get('FAKE_AGENTCTL_FAIL_MATCH')
if fail_match and fail_match in ' '.join(args):
    raise SystemExit(7)
"""


def status_payload(primary_floor, alternate_floor, active="primary"):
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "state": {"activeAlias": active, "autoSwitch": True},
        "aliases": [
            {
                "alias": "primary",
                "active": active == "primary",
                "confidence": "exact",
                "health": "degraded",
                "manualOnly": False,
                "quotaObservedAt": observed_at,
                "limit5hRemainingPercent": primary_floor,
                "limitWeekRemainingPercent": primary_floor,
            },
            {
                "alias": "alternate",
                "active": active == "alternate",
                "confidence": "exact",
                "health": "healthy",
                "manualOnly": False,
                "quotaObservedAt": observed_at,
                "limit5hRemainingPercent": alternate_floor,
                "limitWeekRemainingPercent": alternate_floor,
            },
        ],
    }


def untimestamped_keyring_payload(active="primary-codex", primary_floor=25):
    return {
        "state": {"activeAlias": active, "autoSwitch": True, "updatedAt": "2026-08-07T08:00:00Z"},
        "aliases": [
            {
                "alias": "primary-codex",
                "active": active == "primary-codex",
                "confidence": "exact",
                "health": "healthy",
                "manualOnly": False,
                "limit5hRemainingPercent": primary_floor,
                "limitWeekRemainingPercent": primary_floor,
            }
        ],
    }


class FleetCliFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.keyring = self.tmp / "codex-keyring"
        self.agentctl = self.tmp / "agentctl"
        self.agent_log = self.tmp / "agentctl.log"
        self.state = self.tmp / "fleet-state.json"
        self.receipt = self.tmp / "receipt.json"
        self.config = self.tmp / "fleet.json"
        self.keyring.write_text(KEYRING_SCRIPT)
        self.agentctl.write_text(AGENTCTL_SCRIPT)
        self.keyring.chmod(0o755)
        self.agentctl.chmod(0o755)
        self.config.write_text(
            json.dumps(
                {
                    "policy": {
                        "drain_threshold_percent": 20,
                        "recovery_threshold_percent": 35,
                        "max_status_age_seconds": 900,
                        "allow_unknown_quota": False,
                    },
                    "hosts": [
                        {
                            "id": "fixture-host",
                            "transport": {"type": "local"},
                            "keyring_binary": str(self.keyring),
                            "agents": [
                                {
                                    "id": "book",
                                    "drain_command": [str(self.agentctl), "admission", "close", "{agent_id}"],
                                    "resume_command": [str(self.agentctl), "admission", "open", "{agent_id}"],
                                    "fallback_command": [str(self.agentctl), "route", "{agent_id}", "anthropic"],
                                    "restore_command": [str(self.agentctl), "route", "{agent_id}", "codex", "{active_alias}"],
                                    "refresh_command": [str(self.agentctl), "auth", "refresh", "{agent_id}"],
                                }
                            ],
                        }
                    ],
                }
            )
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_status(self, primary_floor, alternate_floor, active="primary"):
        Path(str(self.keyring) + ".status").write_text(
            json.dumps(status_payload(primary_floor, alternate_floor, active=active))
        )

    def run_cli(self, *args, fail_match=None, receipt=True, receipt_dir=None, noop_switch=False):
        env = {
            "FAKE_AGENTCTL_LOG": str(self.agent_log),
        }
        if fail_match:
            env["FAKE_AGENTCTL_FAIL_MATCH"] = fail_match
        if noop_switch:
            env["FAKE_KEYRING_NOOP_SWITCH"] = "1"
        command = [
            sys.executable,
            str(CLI),
            *args,
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        ]
        if receipt:
            command.extend(["--receipt", str(self.receipt)])
        if receipt_dir:
            command.extend(["--receipt-dir", str(receipt_dir)])
        return subprocess.run(
            command,
            cwd=str(ROOT.parent),
            env={**dict(os.environ), **env},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_plan(self):
        command = [
            sys.executable,
            str(CLI),
            "plan",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        ]
        return subprocess.run(
            command,
            cwd=str(ROOT.parent),
            env=dict(os.environ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_cli_plan_blocks_untimestamped_default_keyring_payload(self):
        Path(str(self.keyring) + ".status").write_text(json.dumps(untimestamped_keyring_payload()))

        proc = self.run_plan()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["actions"], [])
        self.assertEqual(output["hosts"][0]["decision"], "blocked_status")
        self.assertEqual(output["hosts"][0]["status_reason"], "stale_quota")
        self.assertFalse(self.state.exists())
        self.assertIn("status --json", Path(str(self.keyring) + ".log").read_text().splitlines())

    def test_apply_switch_closes_switches_and_reopens_admission(self):
        self.write_status(10, 80)

        proc = self.run_cli("apply", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        persisted = json.loads(self.state.read_text())
        self.assertEqual(persisted["hosts"]["fixture-host"]["active_alias"], "alternate")
        self.assertEqual(json.loads(Path(str(self.keyring) + ".status").read_text())["state"]["autoSwitch"], False)
        self.assertEqual(
            self.agent_log.read_text().splitlines(),
            ["admission close book", "auth refresh book", "admission open book"],
        )
        receipt = json.loads(self.receipt.read_text())
        self.assertIs(receipt["lock_acquired"], True)
        self.assertIn("status --json", Path(str(self.keyring) + ".log").read_text().splitlines())

    def test_apply_routes_to_fallback_then_reopens_admission(self):
        self.write_status(10, 18)

        proc = self.run_cli("apply", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self.state.read_text())["hosts"]["fixture-host"]["mode"], "fallback")
        self.assertEqual(
            self.agent_log.read_text().splitlines(),
            ["admission close book", "route book anthropic", "auth refresh book", "admission open book"],
        )

    def test_apply_recovers_fallback_with_drain_restore_and_resume(self):
        self.write_status(35, 20)
        self.state.write_text(json.dumps({"hosts": {"fixture-host": {"mode": "fallback", "active_alias": "primary"}}}))

        proc = self.run_cli("apply", "--apply")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self.state.read_text())["hosts"]["fixture-host"]["mode"], "codex")
        self.assertEqual(
            self.agent_log.read_text().splitlines(),
            ["admission close book", "route book codex primary", "auth refresh book", "admission open book"],
        )

    def test_apply_failure_stops_before_resume_and_persists_partial_failure(self):
        self.write_status(10, 18)

        proc = self.run_cli("apply", "--apply", fail_match="route book anthropic")

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.agent_log.read_text().splitlines(), ["admission close book", "route book anthropic"])
        persisted = json.loads(self.state.read_text())
        self.assertEqual(persisted["hosts"]["fixture-host"]["mode"], "partial_failure")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["failed_action"]["kind"], "agent_fallback")
        self.assertEqual(receipt["completed_action_count"], 1)

    def test_apply_switch_verification_blocks_refresh_and_resume_on_mismatch(self):
        self.write_status(10, 80)

        proc = self.run_cli("apply", "--apply", noop_switch=True)

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.agent_log.read_text().splitlines(), ["admission close book"])
        persisted = json.loads(self.state.read_text())
        self.assertEqual(persisted["hosts"]["fixture-host"]["mode"], "partial_failure")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["failed_action"]["kind"], "keyring_switch")
        self.assertEqual(receipt["failed_action"]["verification"]["status"], "failed")

    def test_apply_first_action_failure_persists_partial_failure(self):
        self.write_status(10, 18)

        proc = self.run_cli("apply", "--apply", fail_match="admission close book")

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.agent_log.read_text().splitlines(), ["admission close book"])
        persisted = json.loads(self.state.read_text())
        self.assertEqual(persisted["hosts"]["fixture-host"]["mode"], "partial_failure")
        self.assertEqual(persisted["partial_failure"]["failed_action_index"], 0)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["failed_action"]["kind"], "agent_drain")
        self.assertEqual(receipt["completed_action_count"], 0)

    def test_second_apply_transaction_cannot_read_status_while_lock_held(self):
        self.write_status(10, 80)
        lock_path = Path(str(self.state) + ".lock")
        lock_path.touch()

        with lock_path.open("r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                proc = self.run_cli("apply", "--apply")
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        self.assertEqual(proc.returncode, 1)
        self.assertFalse(self.agent_log.exists())
        self.assertFalse(Path(str(self.keyring) + ".log").exists())
        self.assertIn("apply lock is already held", proc.stdout)

    def test_default_receipt_names_are_collision_resistant(self):
        self.write_status(50, 80)
        receipts = self.tmp / "receipts"

        first = self.run_cli("apply", "--apply", receipt=False, receipt_dir=receipts)
        second = self.run_cli("apply", "--apply", receipt=False, receipt_dir=receipts)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(list(receipts.glob("fleet-apply-*.json"))), 2)

    def test_partial_failure_plan_does_not_read_unavailable_host_status(self):
        cfg = json.loads(self.config.read_text())
        cfg["hosts"][0]["keyring_binary"] = str(self.tmp / "missing-keyring")
        self.config.write_text(json.dumps(cfg))
        self.state.write_text(json.dumps({"partial_failure": {"failed_action_index": 0}, "hosts": {"fixture-host": {"mode": "partial_failure"}}}))

        proc = self.run_plan()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["actions"], [])
        self.assertEqual(output["hosts"][0]["decision"], "blocked_partial_failure")

    def test_status_command_skill_dir_placeholder_works_from_different_cwd(self):
        self.write_status(50, 80)
        cfg = json.loads(self.config.read_text())
        cfg["hosts"][0]["status_command"] = [
            sys.executable,
            "{skill_dir}/scripts/codex-keyring-status.py",
            "--json",
            "--keyring-binary",
            str(self.keyring),
            "--stats-dir",
            str(self.tmp / "missing-stats"),
        ]
        self.config.write_text(json.dumps(cfg))

        command = [
            sys.executable,
            str(CLI),
            "plan",
            "--config",
            str(self.config),
            "--state",
            str(self.state),
        ]
        proc = subprocess.run(
            command,
            cwd="/private/tmp",
            env={**dict(os.environ), "FAKE_AGENTCTL_LOG": str(self.agent_log)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["actions"], [])


    def test_cli_plan_short_circuits_partial_failure_without_status_read(self):
        self.state.write_text(json.dumps({
            "hosts": {"fixture-host": {"mode": "partial_failure", "active_alias": "primary"}},
            "partial_failure": {"failed_action_index": 0},
        }))

        proc = self.run_plan()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["actions"], [])
        self.assertEqual(output["hosts"][0]["decision"], "blocked_partial_failure")
        self.assertFalse(Path(str(self.keyring) + ".log").exists(),
                         "status command must not run when partial_failure is present")


if __name__ == "__main__":
    unittest.main()
