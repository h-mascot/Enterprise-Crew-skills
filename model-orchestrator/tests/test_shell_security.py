import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"

# Names that look like secrets (match SECRET_NAME pattern) but are actually
# public session identifiers, counters, or identifiers — not credentials.
NON_SECRET_NAMES = {
    "SESSION_KEY",
    "TOTAL_TOKENS",
    "TOTAL_REQUESTS",
    "TOTAL_COST",
    "USER_ID",
}

SECRET_NAME = r"(?:KEY|TOKEN|SECRET|PASSWORD|PASS|COOKIE|EMAIL|LOGIN)"

FIXED_SECRET_ASSIGNMENT = re.compile(
    rf"""
    ^\s*(?:export\s+)?
    ([A-Z_][A-Z0-9_]*{SECRET_NAME}[A-Z0-9_]*)
    \s*=\s*
    (?!(?:\$|"\$|'\$|$))
    (?!(?:""|''))
    .+
    """,
    re.VERBOSE,
)

# Env var with a non-empty inline default — only flagged when the name matches
# a secret pattern and the default is not just an empty fallback.
SECRET_ENV_FALLBACK = re.compile(
    rf"""\$\{{(?P<name>[A-Z_][A-Z0-9_]*{SECRET_NAME}[A-Z0-9_]*)\s*:-\s*(?P<default>[^}}\s][^}}]*)\}}"""
)

PRIVATE_EMAIL_LITERAL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@(?:curacel\.ai|enterprisecrew\.ai)\b")


class PublicShellSecurityTests(unittest.TestCase):
    maxDiff = None

    def test_model_orchestrator_public_shell_scripts_do_not_embed_private_defaults(self):
        findings = []
        for path in sorted(SCRIPT_DIR.glob("*.sh")):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                rel = path.relative_to(ROOT.parent)

                m = FIXED_SECRET_ASSIGNMENT.search(line)
                if m and m.group(1) not in NON_SECRET_NAMES:
                    findings.append(f"{rel}:{line_number}:fixed-secret-assignment")

                for m in SECRET_ENV_FALLBACK.finditer(line):
                    name = m.group("name")
                    default = m.group("default")
                    if name in NON_SECRET_NAMES:
                        continue
                    # Empty default is fine — it's just ${VAR:-}
                    if default.strip():
                        findings.append(f"{rel}:{line_number}:secret-env-default")

                if PRIVATE_EMAIL_LITERAL.search(line):
                    findings.append(f"{rel}:{line_number}:private-login-email")

        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
