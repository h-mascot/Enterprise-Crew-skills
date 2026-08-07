"""Regression scan for tracked secret and personal-identifier leaks."""

import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


KNOWN_LEAK_HASHES = {
    # (length, sha256) pairs. Do not print or store the literal values here.
    (16, "2911b4406c0d287c1e60464e79f1ebc7ba2ecbe8b30b47a6a6c146283d8fadc6"),
    (39, "8857c3262cc8a36a6ed529519cd777f04b2853dec86b173c219669681acafca2"),
    (16, "e916f93a92c1a5062143c54397ad1ee07f0f8f85bad9c04e81431f0d59919a4c"),
    (7, "1bd3fea8c6e35a200d6cf7d35acd876a0aea00fc8ae0ca1d258fed1e0a3cc054"),
    (18, "9133dd2623c7033b7312818815cdc8d8d6e5c3358e855c1226d390a4838cfe0b"),
    (18, "fe913688c5acb2c709a850ed63db10dc8ff4a1cb4b2244b852ba5c2546be31e4"),
}

SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
    re.compile(r"Bearer\s+[0-9A-Za-z._-]{20,}"),
    re.compile(r"GOOGLE_PASS\s*=\s*['\"][^'$][^'\"]+['\"]"),
    re.compile(r"PASSWORD\s*=\s*['\"][^'$][^'\"]+['\"]", re.I),
]


def _tracked_model_orchestrator_text_files():
    result = subprocess.run(
        ["git", "ls-files", "model-orchestrator"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    for rel in result.stdout.splitlines():
        path = ROOT / rel
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif"}:
            continue
        yield path


def _tracked_scraper_files():
    result = subprocess.run(
        ["git", "ls-files", "model-orchestrator/scripts/scrape-quota*.sh"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    for rel in result.stdout.splitlines():
        yield ROOT / rel


def _contains_known_literal(text):
    for length, expected_hash in KNOWN_LEAK_HASHES:
        if len(text) < length:
            continue
        for index in range(0, len(text) - length + 1):
            candidate = text[index : index + length]
            if hashlib.sha256(candidate.encode("utf-8")).hexdigest() == expected_hash:
                return True
    return False


def test_tracked_model_orchestrator_text_has_no_obvious_secrets():
    failures = []
    for path in _tracked_model_orchestrator_text_files():
        text = path.read_text(errors="ignore")
        if _contains_known_literal(text):
            failures.append("%s: known leaked literal hash matched" % path.relative_to(ROOT))
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(
                    "%s: matched secret pattern %s"
                    % (path.relative_to(ROOT), pattern.pattern.split("\\")[0])
                )
    assert failures == []


def test_tracked_scrapers_do_not_embed_personal_camofox_user_strings():
    failures = []
    for path in _tracked_scraper_files():
        text = path.read_text(errors="ignore")
        if re.search(r"\bada\b", text):
            failures.append(
                f"{path.relative_to(ROOT)}: contains literal personal Camofox user token 'ada'"
            )
    assert failures == []


def test_openai_codex_quota_scraper_supports_account_scoped_overrides():
    script = (
        ROOT
        / "model-orchestrator"
        / "scripts"
        / "scrape-quota-openai-codex.sh"
    ).read_text()

    assert 'CAMOFOX_USER_ID="${CAMOFOX_USER_ID:-operator}"' in script
    assert 'CAMOFOX_SESSION_KEY="${CAMOFOX_SESSION_KEY:-openai-codex-quota}"' in script
    assert 'CODEX_QUOTA_FILE="${CODEX_QUOTA_FILE:-$STATE_DIR/openai-codex-quota.json}"' in script
    assert 'mkdir -p "$(dirname "$CODEX_QUOTA_FILE")"' in script
    assert '${CAMOFOX_USER_ID}' in script
    assert '${CAMOFOX_SESSION_KEY}' in script
    assert '$CODEX_QUOTA_FILE' in script
    assert 'ada' not in script
