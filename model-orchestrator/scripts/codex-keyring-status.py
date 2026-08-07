#!/usr/bin/env python3
"""Secret-safe codex-keyring status sensor.

Runs `codex-keyring status --json`, enriches aliases from local metadata files,
and emits a codex-keyring-compatible JSON status object without auth payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


SAFE_STATE_FIELDS = {
    "activeAlias",
    "active_alias",
    "currentAlias",
    "current_alias",
    "active",
    "current",
    "autoSwitch",
    "auto_switch",
}
SAFE_ALIAS_FIELDS = {
    "alias",
    "active",
    "isActive",
    "is_active",
    "quotaObservedAt",
    "quota_observed_at",
    "quotaCheckedAt",
    "quota_checked_at",
    "checkedAt",
    "checked_at",
    "statusCheckedAt",
    "status_checked_at",
    "observedAt",
    "observed_at",
    "quotaSource",
    "quota_source",
    "limit5hRemainingPercent",
    "limit5h_remaining_percent",
    "limitWeekRemainingPercent",
    "limit_week_remaining_percent",
    "health",
    "confidence",
    "manualOnly",
    "manual_only",
}
NORMALIZED_FIELDS = {
    "quota_observed_at": "quotaObservedAt",
    "quota_checked_at": "quotaCheckedAt",
    "checked_at": "checkedAt",
    "observed_at": "observedAt",
    "status_checked_at": "statusCheckedAt",
    "quota_source": "quotaSource",
    "limit5h_remaining_percent": "limit5hRemainingPercent",
    "limit_week_remaining_percent": "limitWeekRemainingPercent",
    "manual_only": "manualOnly",
    "is_active": "active",
    "isActive": "active",
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Emit codex-keyring status enriched with safe quota metadata.")
    p.add_argument("--keyring-binary", default=os.environ.get("CODEX_KEYRING_BINARY", "codex-keyring"))
    p.add_argument("--stats-dir", default=os.environ.get("CODEX_KEYRING_STATS_DIR", "~/.codex-keyring/stats"))
    p.add_argument("--timeout-seconds", type=int, default=30)
    p.add_argument("--json", action="store_true", help="Emit JSON. Accepted for status_command symmetry.")
    return p


def load_keyring_status(keyring_binary: str, timeout_seconds: int) -> dict[str, Any]:
    proc = subprocess.run(
        [keyring_binary, "status", "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"codex-keyring status failed with exit {proc.returncode}")
    payload = json.loads(proc.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("codex-keyring status returned a non-object payload")
    return payload


def alias_from_entry(alias_key: str | None, details: Any) -> tuple[str | None, Mapping[str, Any]]:
    if not isinstance(details, Mapping):
        return None, {}
    alias = details.get("alias") or details.get("name") or details.get("id") or alias_key
    if not alias:
        return None, {}
    return str(alias), details


def iter_alias_entries(raw_aliases: Any) -> list[tuple[str, Mapping[str, Any]]]:
    entries: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(raw_aliases, Mapping):
        for key, details in raw_aliases.items():
            alias, parsed = alias_from_entry(str(key), details)
            if alias:
                entries.append((alias, parsed))
    elif isinstance(raw_aliases, list):
        for details in raw_aliases:
            alias, parsed = alias_from_entry(None, details)
            if alias:
                entries.append((alias, parsed))
    return entries


def safe_copy(mapping: Mapping[str, Any], allowed: set[str], *, include_active: bool = True) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in mapping.items():
        if key not in allowed:
            continue
        normalized = NORMALIZED_FIELDS.get(key, key)
        if normalized == "active" and not include_active:
            continue
        safe[normalized] = value
    return safe


def load_stats(stats_dir: Path, known_aliases: set[str]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not stats_dir.exists():
        return stats
    for candidate in sorted(stats_dir.iterdir()):
        if not candidate.is_file() or candidate.suffix != ".json":
            continue
        try:
            payload = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        alias = payload.get("alias")
        if not isinstance(alias, str) or alias not in known_aliases:
            continue
        metadata = safe_copy(payload, SAFE_ALIAS_FIELDS, include_active=False)
        metadata.pop("alias", None)
        stats[alias] = metadata
    return stats


def sanitized_status(payload: Mapping[str, Any], stats_dir: Path) -> dict[str, Any]:
    raw_state = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
    alias_entries = iter_alias_entries(payload.get("aliases"))
    known_aliases = {alias for alias, _details in alias_entries}
    metadata_by_alias = load_stats(stats_dir, known_aliases)

    aliases = []
    for alias, details in alias_entries:
        entry = {"alias": alias}
        entry.update(safe_copy(details, SAFE_ALIAS_FIELDS))
        entry["alias"] = alias
        entry.update(metadata_by_alias.get(alias, {}))
        aliases.append(entry)

    return {
        "state": safe_copy(raw_state, SAFE_STATE_FIELDS),
        "aliases": aliases,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        raw = load_keyring_status(args.keyring_binary, args.timeout_seconds)
        safe = sanitized_status(raw, Path(args.stats_dir).expanduser())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(safe, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
