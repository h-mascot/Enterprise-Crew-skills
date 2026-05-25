#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import re
import sys


REQUIRED_PACK_SECTIONS = [
    "Source Log",
    "Conference Brief",
    "Agenda Targets",
    "Speaker and Attendee Map",
    "Pitch Deck Prep",
    "Meeting Scheduler",
    "Contact Enrichment Queue",
    "Session Notes Capture",
    "Follow-up Plan",
    "Open Gaps",
]

REQUIRED_SKILL_FILES = [
    "SKILL.md",
    "references/runbook.md",
    "references/templates.md",
    "references/source-checklist.md",
]


def has_heading(text: str, heading: str) -> bool:
    pattern = rf"^#+\s+{re.escape(heading)}\s*$"
    return re.search(pattern, text, re.MULTILINE) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate conference-prep skill and prep pack completeness.")
    parser.add_argument("--skill-dir", default="skills/conference-prep", help="Path to conference-prep skill directory.")
    parser.add_argument("--pack", required=True, help="Path to generated conference prep pack markdown.")
    parser.add_argument("--json", action="store_true", help="Emit JSON result.")
    args = parser.parse_args()

    root = Path(args.skill_dir)
    pack = Path(args.pack)
    failures = []

    for rel in REQUIRED_SKILL_FILES:
        p = root / rel
        if not p.exists() or p.stat().st_size == 0:
            failures.append(f"missing_or_empty:{p}")

    skill = root / "SKILL.md"
    if skill.exists():
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            failures.append("skill_frontmatter_missing")
        if "name: conference-prep" not in text:
            failures.append("skill_name_not_conference_prep")
        if "pitch" not in text.lower() or "follow-up" not in text.lower():
            failures.append("skill_description_missing_required_scope")

    if not pack.exists() or pack.stat().st_size == 0:
        failures.append(f"missing_or_empty:{pack}")
        pack_text = ""
    else:
        pack_text = pack.read_text(encoding="utf-8")

    for section in REQUIRED_PACK_SECTIONS:
        if not has_heading(pack_text, section):
            failures.append(f"pack_missing_section:{section}")

    if pack_text and pack_text.count("http") < 2:
        failures.append("pack_has_too_few_source_urls")

    result = {
        "status": "pass" if not failures else "fail",
        "skill_dir": str(root),
        "pack": str(pack),
        "failures": failures,
        "required_sections": REQUIRED_PACK_SECTIONS,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"conference-prep validation: {result['status']}")
        for failure in failures:
            print(f"- {failure}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
