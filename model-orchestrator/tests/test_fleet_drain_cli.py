"""CLI integration tests for fleet_drain_cli."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
CLI = SCRIPT_DIR / "fleet_drain_cli.py"


class TestCLI:
    def test_status_command(self, config_file):
        """status should print account names."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "luna" in result.stdout
        assert "herald" in result.stdout

    def test_plan_command(self, config_file):
        """plan should print plan summary."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "plan"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Plan" in result.stdout

    def test_plan_json(self, config_file):
        """plan --json should produce valid JSON."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "plan", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "actions" in data
        assert "summary" in data

    def test_apply_dry_run(self, config_file):
        """apply without --confirm should be a dry run."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file), "apply"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout

    def test_missing_config_error(self, tmp_path):
        """Should error gracefully on missing config."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(tmp_path / "missing.yaml"), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_plan_with_current(self, config_file):
        """plan with --current should accept surface=account pairs."""
        result = subprocess.run(
            [
                sys.executable, str(CLI), "--config", str(config_file), "plan",
                "--current", "enterprise:geordi=luna",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_no_args_shows_status(self, config_file):
        """No subcommand should default to status."""
        result = subprocess.run(
            [sys.executable, str(CLI), "--config", str(config_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Status" in result.stdout
