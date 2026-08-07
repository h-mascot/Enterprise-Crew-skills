#!/usr/bin/env python3
"""
fleet_drain_cli.py — CLI wrapper for fleet-wide Codex account drain.

Usage:
    python3 fleet_drain_cli.py status
    python3 fleet_drain_cli.py plan [--config CONFIG] [--current SURFACE=ACCOUNT ...]
    python3 fleet_drain_cli.py apply [--config CONFIG] [--confirm] [--current SURFACE=ACCOUNT ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from scripts/ directory
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fleet_drain import (  # noqa: E402
    DEFAULT_CONFIG,
    FleetDrain,
    apply_plan,
    collect_quota,
    format_status,
    generate_plan,
    load_config,
    plan_to_json,
    set_manual_quota,
)


def parse_current_args(current_list: list[str]) -> dict[str, str]:
    """Parse --current surface=account pairs."""
    result = {}
    for item in current_list:
        if "=" not in item:
            print(f"WARNING: ignoring malformed --current '{item}' (expected surface=account)", file=sys.stderr)
            continue
        surface, account = item.split("=", 1)
        result[surface.strip()] = account.strip()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fleet-wide Codex account drain manager"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to accounts config YAML (default: config/accounts.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show all accounts, quotas, and active surfaces")

    plan_parser = sub.add_parser("plan", help="Generate a switch plan (safe, no mutations)")
    plan_parser.add_argument(
        "--current",
        action="append",
        default=[],
        metavar="SURFACE=ACCOUNT",
        help="Current account assignment (e.g. enterprise:geordi=luna). Can repeat.",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        help="Output plan as JSON",
    )

    apply_parser = sub.add_parser("apply", help="Execute a switch plan")
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually execute switches (default: dry run)",
    )
    apply_parser.add_argument(
        "--current",
        action="append",
        default=[],
        metavar="SURFACE=ACCOUNT",
        help="Current account assignment. Can repeat.",
    )
    apply_parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    args = parser.parse_args(argv)
    command = args.command or "status"

    config_path = Path(args.config)

    try:
        if command == "status":
            fd = FleetDrain(config_path)
            fd.load()
            print(fd.status())
            return 0

        elif command == "plan":
            accounts, policy, _ = load_config(config_path)
            collect_quota(accounts)
            current = parse_current_args(args.current)
            actions = generate_plan(accounts, policy, current)

            if args.json:
                print(plan_to_json(actions, accounts))
            else:
                print("=== Fleet Drain Plan ===")
                print()
                for a in actions:
                    if a.is_noop:
                        icon = "✅"
                    elif a.reason.startswith("BLOCKED"):
                        icon = "🚫"
                    else:
                        icon = "🔄"
                    print(f"{icon} {a.surface_id}: {a.current_account} → {a.proposed_account}")
                    print(f"   {a.reason}")
                print()
                switches = sum(1 for a in actions if not a.is_noop and not a.reason.startswith("BLOCKED"))
                blocked = sum(1 for a in actions if a.reason.startswith("BLOCKED"))
                noop = sum(1 for a in actions if a.is_noop)
                print(f"Summary: {switches} switches | {blocked} blocked | {noop} no-op")

            return 0

        elif command == "apply":
            accounts, policy, _ = load_config(config_path)
            collect_quota(accounts)
            current = parse_current_args(args.current)
            actions = generate_plan(accounts, policy, current)

            if not args.confirm:
                print("DRY RUN — pass --confirm to execute switches")
                print()

            result = apply_plan(actions, accounts, confirm=args.confirm)

            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print("=== Apply Result ===")
                print()
                print(f"Applied: {result['applied_count']} | Blocked: {result['blocked_count']} | No-op: {result['noop_count']}")
                if result["applied"]:
                    print("\nApplied switches:")
                    for a in result["applied"]:
                        print(f"  ✅ {a['surface_id']}: {a['current_account']} → {a['proposed_account']}")
                        if "exec_result" in a:
                            er = a["exec_result"]
                            print(f"     {'OK' if er.get('ok') else 'FAILED'}: {er.get('stdout', er.get('error', ''))}")
                if result["blocked"]:
                    print("\nBlocked:")
                    for b in result["blocked"]:
                        reason = b.get("block_reason", "unknown")
                        print(f"  🚫 {b['surface_id']}: {b.get('current_account', '?')} → {b.get('proposed_account', '?')} ({reason})")

            return 0

        else:
            parser.print_help()
            return 1

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"Hint: copy config/accounts.example.yaml to config/accounts.yaml", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
