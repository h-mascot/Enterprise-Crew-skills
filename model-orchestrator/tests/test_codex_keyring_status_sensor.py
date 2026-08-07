import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SENSOR = ROOT / "scripts" / "codex-keyring-status.py"


KEYRING_SCRIPT = """#!/usr/bin/env python3
import json

print(json.dumps({
    "state": {"activeAlias": "primary-codex", "autoSwitch": True, "updatedAt": "2026-08-07T08:00:00Z"},
    "aliases": [
        {"alias": "primary-codex", "active": True},
        {"alias": "standby-codex", "active": False}
    ],
    "auth": {"token": "SECRET_TOKEN"}
}))
"""


class CodexKeyringStatusSensorTests(unittest.TestCase):
    def test_sensor_enriches_by_parsed_alias_and_strips_secret_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keyring = root / "codex-keyring"
            stats = root / "stats"
            stats.mkdir()
            keyring.write_text(KEYRING_SCRIPT)
            keyring.chmod(0o755)

            (stats / "not-the-alias-name.json").write_text(
                json.dumps(
                    {
                        "alias": "primary-codex",
                        "quotaObservedAt": "2026-08-07T12:00:00Z",
                        "quotaSource": "fixture",
                        "limit5hRemainingPercent": 25,
                        "limitWeekRemainingPercent": 30,
                        "health": "degraded",
                        "confidence": "exact",
                        "manualOnly": False,
                        "active": False,
                        "token": "SECRET_TOKEN",
                    }
                )
            )
            (stats / "standby.json").write_text(
                json.dumps(
                    {
                        "alias": "standby-codex",
                        "quotaObservedAt": "2026-08-07T12:01:00Z",
                        "quotaSource": "fixture",
                        "limit5hRemainingPercent": 80,
                        "limitWeekRemainingPercent": 75,
                        "health": "healthy",
                        "confidence": "exact",
                        "manualOnly": False,
                        "authorization": "SECRET_TOKEN",
                    }
                )
            )
            (stats / "ignored.json").write_text(json.dumps({"alias": "../primary-codex", "limit5hRemainingPercent": 1}))

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SENSOR),
                    "--keyring-binary",
                    str(keyring),
                    "--stats-dir",
                    str(stats),
                    "--json",
                ],
                cwd=str(ROOT.parent),
                env=dict(os.environ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        rendered = json.dumps(payload)
        self.assertNotIn("SECRET_TOKEN", rendered)
        self.assertNotIn("auth", payload)
        aliases = {entry["alias"]: entry for entry in payload["aliases"]}
        self.assertTrue(aliases["primary-codex"]["active"])
        self.assertEqual(aliases["primary-codex"]["quotaObservedAt"], "2026-08-07T12:00:00Z")
        self.assertEqual(aliases["primary-codex"]["limit5hRemainingPercent"], 25)
        self.assertEqual(aliases["primary-codex"]["limitWeekRemainingPercent"], 30)
        self.assertEqual(aliases["primary-codex"]["quotaSource"], "fixture")
        self.assertEqual(aliases["standby-codex"]["quotaObservedAt"], "2026-08-07T12:01:00Z")
        self.assertNotIn("../primary-codex", aliases)


if __name__ == "__main__":
    unittest.main()
