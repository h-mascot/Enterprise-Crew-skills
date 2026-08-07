#!/usr/bin/env python3
"""CLI wrapper for fleet-wide Codex account drain."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fleet_drain import (  # noqa: E402
    DEFAULT_CONFIG,
    FleetDrain,
    PlanValidationError,
    load_plan_artifact,
    write_plan_artifact,
)


def parse_current_args(current_list):
    """Parse --current surface=account pairs.

    Empty surface names or empty account values are rejected as malformed
    so that a typo like ``--current enterprise:geordi=`` cannot silently
    fall back to the config-declared assignment.
    """
    result = {}
    for item in current_list:
        if "=" not in item:
            raise ValueError(
                "malformed --current %r (expected surface=account)" % item
            )
        surface, account = item.split("=", 1)
        surface = surface.strip()
        account = account.strip()
        if not surface or not account:
            raise ValueError(
                "malformed --current %r (empty surface or account)" % item
            )
        result[surface] = account
    return result


def _print_plan_text(artifact):
    print("=== Fleet Drain Plan ===")
    print()
    for action in artifact["actions"]:
        if action["reason"].startswith("BLOCKED"):
            marker = "[blocked]"
        elif action["current_account"] == action["proposed_account"]:
            marker = "[noop]"
        else:
            marker = "[switch]"
        print(
            "%s %s: %s -> %s"
            % (
                marker,
                action["surface_id"],
                action["current_account"],
                action["proposed_account"],
            )
        )
        print("   %s" % action["reason"])
    summary = artifact["summary"]
    print()
    print(
        "Summary: %(switches)s switches | %(blocked)s blocked | %(noop)s no-op"
        % summary
    )


def _print_apply_result(result):
    print("=== Apply Result ===")
    print()
    print(
        "Applied: %(applied_count)s | Failed: %(failed_count)s | "
        "Blocked: %(blocked_count)s | No-op: %(noop_count)s" % result
    )
    if result["applied"]:
        print("\nApplied switches:")
        for action in result["applied"]:
            print(
                "  [ok] %s: %s -> %s"
                % (
                    action["surface_id"],
                    action["current_account"],
                    action["proposed_account"],
                )
            )
    if result["failed"]:
        print("\nFailed:")
        for action in result["failed"]:
            exec_result = action.get("exec_result", {})
            reason = exec_result.get("error") or action.get("block_reason") or "unknown"
            print(
                "  [failed] %s: %s -> %s (%s)"
                % (
                    action["surface_id"],
                    action.get("current_account", "?"),
                    action.get("proposed_account", "?"),
                    reason,
                )
            )
    if result["blocked"]:
        print("\nBlocked:")
        for action in result["blocked"]:
            reason = action.get("block_reason") or action.get("reason", "unknown")
            print(
                "  [blocked] %s: %s -> %s (%s)"
                % (
                    action["surface_id"],
                    action.get("current_account", "?"),
                    action.get("proposed_account", "?"),
                    reason,
                )
            )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fleet-wide Codex account drain manager"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to accounts config YAML (default: config/accounts.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show accounts, quota, and configured surfaces")

    plan_parser = sub.add_parser("plan", help="Generate a reviewed switch plan")
    plan_parser.add_argument(
        "--current",
        action="append",
        default=[],
        metavar="SURFACE=ACCOUNT",
        help="Current account assignment, e.g. enterprise:geordi=luna. Can repeat.",
    )
    plan_parser.add_argument("--json", action="store_true", help="Print artifact JSON")
    plan_parser.add_argument(
        "--out",
        metavar="PATH",
        help="Write the plan artifact to PATH for a later apply",
    )

    apply_parser = sub.add_parser("apply", help="Apply an existing plan artifact")
    apply_parser.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Plan artifact created by the plan command",
    )
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually execute switches (default is dry run)",
    )
    apply_parser.add_argument("--json", action="store_true", help="Print JSON result")

    args = parser.parse_args(argv)
    command = args.command or "status"
    config_path = Path(args.config)

    try:
        if command == "status":
            fd = FleetDrain(config_path)
            fd.load()
            print(fd.status())
            return 0

        if command == "plan":
            fd = FleetDrain(config_path)
            fd.load()
            try:
                current = parse_current_args(args.current)
            except ValueError as exc:
                print("ERROR: %s" % exc, file=sys.stderr)
                return 2
            artifact = fd.plan_artifact(current)
            if args.out:
                write_plan_artifact(artifact, args.out)
            if args.json:
                print(json.dumps(artifact, indent=2, sort_keys=True))
            else:
                _print_plan_text(artifact)
                if args.out:
                    print()
                    print("Wrote plan artifact: %s" % args.out)
            return 0

        if command == "apply":
            if not args.confirm and not args.json:
                print("DRY RUN - pass --confirm to execute switches")
                print()
            artifact = load_plan_artifact(args.plan)
            fd = FleetDrain(config_path)
            fd.load()
            result = fd.apply(artifact, confirm=args.confirm)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                _print_apply_result(result)
            if args.confirm and (
                result.get("failed_count", 0) or result.get("blocked_count", 0)
            ):
                return 4
            return 0

        parser.print_help()
        return 1

    except FileNotFoundError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        print("Hint: copy config/accounts.example.yaml to config/accounts.yaml", file=sys.stderr)
        return 2
    except PlanValidationError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 4
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
