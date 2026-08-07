"""CLI integration tests for fleet_drain_cli."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
CLI = SCRIPT_DIR / "fleet_drain_cli.py"
sys.path.insert(0, str(SCRIPT_DIR))

from fleet_drain import _actions_digest, _plan_digest  # noqa: E402


class TestCLI:
    def test_status_command(self, config_file):
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "luna" in result.stdout
        assert "herald" in result.stdout

    def test_plan_json_is_artifact(self, config_file):
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "plan", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["schema_version"] == 1
        assert "config_digest" in data
        assert "actions_digest" in data
        assert "plan_digest" in data

    def test_plan_out_then_apply_dry_run(self, config_file, tmp_path):
        plan_path = tmp_path / "plan.json"
        plan = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--out",
                str(plan_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert plan.returncode == 0
        assert plan_path.exists()

        apply = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "apply",
                "--plan",
                str(plan_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert apply.returncode == 0
        assert "DRY RUN" in apply.stdout

    def test_plan_out_then_apply_json_dry_run_stdout_is_parseable(self, config_file, tmp_path):
        plan_path = tmp_path / "plan.json"
        plan = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--out",
                str(plan_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert plan.returncode == 0

        apply = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "apply",
                "--plan",
                str(plan_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert apply.returncode == 0
        data = json.loads(apply.stdout)
        assert data["total"] == 2
        assert "DRY RUN" not in apply.stdout

    def test_plan_out_then_confirmed_apply_json_noop_stdout_is_parseable(
        self, config_file, tmp_path
    ):
        luna_quota = tmp_path / "luna-quota.json"
        herald_quota = tmp_path / "herald-quota.json"
        luna_quota.write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 80,
                    "weekly_remaining_pct": 90,
                    "status": "healthy",
                }
            )
        )
        herald_quota.write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 70,
                    "weekly_remaining_pct": 80,
                    "status": "healthy",
                }
            )
        )
        config_file.write_text(
            config_file.read_text()
            .replace("state/luna-quota.json", str(luna_quota))
            .replace("state/herald-quota.json", str(herald_quota))
        )

        plan_path = tmp_path / "plan.json"
        plan = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--out",
                str(plan_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert plan.returncode == 0

        apply = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "apply",
                "--plan",
                str(plan_path),
                "--confirm",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert apply.returncode == 0
        data = json.loads(apply.stdout)
        assert data["noop_count"] == 2
        assert "Applied:" not in apply.stdout

    def test_apply_requires_plan_path(self, config_file):
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "apply"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "--plan" in result.stderr

    def test_missing_config_error(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(tmp_path / "missing.yaml"),
                "status",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_no_args_shows_status(self, config_file):
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Status" in result.stdout

    def test_confirmed_apply_failures_return_nonzero_without_ssh(self, config_file, tmp_path):
        luna_quota = tmp_path / "luna-quota.json"
        herald_quota = tmp_path / "herald-quota.json"
        luna_quota.write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 80,
                    "weekly_remaining_pct": 90,
                    "status": "healthy",
                }
            )
        )
        herald_quota.write_text(
            json.dumps(
                {
                    "five_hour_remaining_pct": 2,
                    "weekly_remaining_pct": 5,
                    "status": "exhausted",
                }
            )
        )
        config_file.write_text(
            config_file.read_text()
            .replace("state/luna-quota.json", str(luna_quota))
            .replace("state/herald-quota.json", str(herald_quota))
        )
        plan_path = tmp_path / "plan.json"
        subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--current",
                "enterprise:geordi=herald",
                "--out",
                str(plan_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        artifact = json.loads(plan_path.read_text())
        switch = next(a for a in artifact["actions"] if a["surface_id"] == "enterprise:geordi")
        switch["ssh_target"] = ""
        artifact["actions_digest"] = _actions_digest(artifact["actions"])
        artifact["plan_digest"] = _plan_digest(artifact)
        plan_path.write_text(json.dumps(artifact))

        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "apply",
                "--plan",
                str(plan_path),
                "--confirm",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 4
        assert "ssh_target mismatch" in result.stderr

    def test_confirmed_apply_with_blocked_actions_returns_nonzero(self, config_file, tmp_path):
        plan_path = tmp_path / "plan.json"
        subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--current",
                "enterprise:geordi=missing",
                "--out",
                str(plan_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "apply",
                "--plan",
                str(plan_path),
                "--confirm",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 4
        assert "Blocked:" in result.stdout

    def test_plan_rejects_unknown_explicit_surface(self, config_file):
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--config",
                str(config_file),
                "plan",
                "--current",
                "typo:geordi=luna",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "unknown current assignment surface" in result.stderr
