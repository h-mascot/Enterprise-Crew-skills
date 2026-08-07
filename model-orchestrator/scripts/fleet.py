#!/usr/bin/env python3
"""CLI wrapper for the model-orchestrator fleet controller."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

import fleet_controller as fleet  # noqa: E402


def load_config(path: str | None) -> tuple[dict, Path]:
    if not path:
        path = os.environ.get("MODEL_ORCHESTRATOR_FLEET_CONFIG")
    if not path:
        default = ROOT / "config" / "fleet.json"
        if default.exists():
            path = str(default)
    if not path:
        raise SystemExit("fleet config required: pass --config or set MODEL_ORCHESTRATOR_FLEET_CONFIG")
    config_path = Path(path).expanduser().resolve()
    return fleet.load_json(config_path), config_path


def resolve_path(raw: str | None, config_path: Path, fallback: str) -> Path:
    if raw:
        path = Path(raw).expanduser()
    else:
        path = Path(fallback).expanduser()
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def state_path(args: argparse.Namespace, config: dict, config_path: Path) -> Path:
    return resolve_path(args.state, config_path, config.get("state_file", "../state/fleet-state.json"))


def receipt_dir(args: argparse.Namespace, config: dict, config_path: Path) -> Path:
    return resolve_path(args.receipt_dir, config_path, config.get("receipt_dir", "../state/fleet-receipts"))


def lock_path(args: argparse.Namespace, config: dict, config_path: Path, state_file: Path) -> Path:
    raw = getattr(args, "lock_file", None) or config.get("lock_file")
    if raw:
        return resolve_path(raw, config_path, str(state_file) + ".lock")
    return Path(str(state_file) + ".lock")


def load_state(path: Path) -> dict:
    return fleet.load_json(path, default={"hosts": {}})


def build_plan(args: argparse.Namespace, config: dict, state: dict) -> dict:
    statuses = fleet.read_statuses(config)
    return fleet.plan_fleet(config, statuses, state=state)


def default_receipt_path(receipts: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return receipts / f"fleet-apply-{stamp}-{os.getpid()}.json"


def lock_failure_receipt(receipt_path: Path | None, now: str) -> dict:
    receipt = {
        "ok": False,
        "applied_at": now,
        "planned_at": None,
        "attempted_actions": [],
        "failed_action_index": None,
        "failed_action": None,
        "completed_action_count": 0,
        "planned_action_count": 0,
        "lock_acquired": False,
        "error": "apply lock is already held",
    }
    if receipt_path is not None:
        fleet.write_json(receipt_path, receipt)
    return receipt


def command_plan(args: argparse.Namespace) -> int:
    config, config_path = load_config(args.config)
    state = load_state(state_path(args, config, config_path))
    plan = build_plan(args, config, state)
    print(json.dumps(fleet.public_plan(plan), indent=2, sort_keys=True))
    return 0


def command_apply(args: argparse.Namespace) -> int:
    if not args.apply:
        raise SystemExit("refusing to apply without explicit --apply")
    config, config_path = load_config(args.config)
    state_file = state_path(args, config, config_path)
    apply_lock = lock_path(args, config, config_path, state_file)
    receipts = receipt_dir(args, config, config_path)
    if args.receipt:
        receipt_path = Path(args.receipt).expanduser().resolve()
    else:
        receipt_path = default_receipt_path(receipts)

    apply_lock.parent.mkdir(parents=True, exist_ok=True)
    with apply_lock.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result = lock_failure_receipt(receipt_path, fleet.utc_now_iso())
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
        try:
            state = load_state(state_file)
            plan = build_plan(args, config, state)
            result = fleet.apply_plan(plan, state=state, state_path=state_file, receipt_path=receipt_path, use_lock=False)
            result["lock_acquired"] = True
            fleet.write_json(receipt_path, result)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def command_status(args: argparse.Namespace) -> int:
    config, config_path = load_config(args.config)
    state = load_state(state_path(args, config, config_path))
    statuses = fleet.read_statuses(config)
    max_age = int((config.get("policy") or {}).get("max_status_age_seconds", fleet.DEFAULT_MAX_STATUS_AGE_SECONDS))
    parsed = {
        host_id: fleet.parse_keyring_status(payload, max_age_seconds=max_age)
        for host_id, payload in statuses.items()
    }
    output = {
        "mode": "status",
        "state": state,
        "hosts": [
            {
                "host": host_id,
                "active_alias": status.active_alias,
                "auto_switch": status.auto_switch,
                "accounts": {
                    alias: {
                        "floor_percent": account.floor_percent,
                        "limit5h_remaining_percent": account.limit5h_remaining_percent,
                        "limit_week_remaining_percent": account.limit_week_remaining_percent,
                        "checked_at": account.checked_at,
                        "stale": account.stale,
                        "confidence": account.confidence,
                        "health": account.health,
                        "manual_only": account.manual_only,
                        "active": account.active,
                    }
                    for alias, account in status.accounts.items()
                },
            }
            for host_id, status in parsed.items()
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Plan or apply Codex fleet account drain policy.")
    sub = p.add_subparsers(dest="command")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", help="Fleet JSON config. Defaults to MODEL_ORCHESTRATOR_FLEET_CONFIG.")
        sp.add_argument("--state", help="Persistent hysteresis state path.")
        sp.add_argument("--receipt-dir", help="Directory for apply receipts.")

    plan = sub.add_parser("plan", help="Plan actions only. This is the default behavior.")
    common(plan)
    plan.set_defaults(func=command_plan)

    apply = sub.add_parser("apply", help="Apply a freshly planned action list.")
    common(apply)
    apply.add_argument("--apply", action="store_true", help="Required acknowledgement for mutation actions.")
    apply.add_argument("--receipt", help="Write apply receipt to this path.")
    apply.add_argument("--lock-file", help="Exclusive non-blocking apply lock path.")
    apply.set_defaults(func=command_apply)

    status = sub.add_parser("status", help="Show parsed Codex account status and persisted fleet state.")
    common(status)
    status.set_defaults(func=command_status)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "plan")
    args = parser().parse_args(argv)
    if not hasattr(args, "func"):
        args = parser().parse_args(["plan", *argv])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
