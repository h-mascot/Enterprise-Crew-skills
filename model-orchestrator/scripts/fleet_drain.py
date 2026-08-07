#!/usr/bin/env python3
"""
fleet_drain.py — Core module for fleet-wide Codex account drain.

Provides account-aware quota policy for the model-orchestrator skill.
Loads account config, collects quota data, ranks accounts by drain policy,
and generates safe switch plans that can be reviewed before applying.

Usage as a library:
    from fleet_drain import FleetDrain
    fd = FleetDrain("config/accounts.yaml")
    plan = fd.generate_plan()
    fd.apply_plan(plan, confirm=True)

Usage as a CLI:
    python3 fleet_drain.py status
    python3 fleet_drain.py plan
    python3 fleet_drain.py apply --confirm
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config" / "accounts.yaml"
DEFAULT_STATE_DIR = SKILL_DIR / "state"


@dataclass
class Surface:
    """A single agent surface on a host where a Codex account is active."""

    host: str
    agent: str
    ssh_target: str
    codex_cli_path: str
    auth_file: str

    @property
    def id(self) -> str:
        return f"{self.host}:{self.agent}"


@dataclass
class Account:
    """A Codex account with quota info and the surfaces it runs on."""

    name: str
    email: str
    priority: int
    quota_source: str  # camofox | api | manual
    quota_file: str
    surfaces: list[Surface] = field(default_factory=list)

    # Quota fields populated at runtime
    five_hour_remaining_pct: float | None = None
    weekly_remaining_pct: float | None = None
    status: str = "unknown"  # healthy | warning | exhausted | unknown

    @property
    def effective_remaining(self) -> float:
        """Lower of 5h and weekly remaining. Used for ranking."""
        vals = [
            v
            for v in [self.five_hour_remaining_pct, self.weekly_remaining_pct]
            if v is not None
        ]
        return min(vals) if vals else 0.0

    @property
    def is_usable(self) -> bool:
        return self.status not in ("exhausted", "unknown") and self.effective_remaining > 0


@dataclass
class Policy:
    min_remaining_pct: float = 10.0
    target_remaining_pct: float = 50.0
    drain_order: str = "priority"  # priority | most_remaining | round_robin
    dry_run_default: bool = True


@dataclass
class SwitchAction:
    """A single proposed account switch on one surface."""

    surface_id: str
    host: str
    agent: str
    current_account: str
    proposed_account: str
    reason: str
    current_remaining: float | None
    proposed_remaining: float | None

    @property
    def is_noop(self) -> bool:
        return self.current_account == self.proposed_account

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "host": self.host,
            "agent": self.agent,
            "current_account": self.current_account,
            "proposed_account": self.proposed_account,
            "reason": self.reason,
            "current_remaining_pct": self.current_remaining,
            "proposed_remaining_pct": self.proposed_remaining,
        }


def load_config(config_path: str | Path) -> tuple[list[Account], Policy, dict[str, Any]]:
    """Load account config from YAML. Returns (accounts, policy, ssh_config).

    Falls back to JSON if PyYAML is unavailable and the file has .json extension.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    raw = _parse_yaml(config_path)

    accounts = []
    for acct_raw in raw.get("accounts", []):
        surfaces = []
        for s_raw in acct_raw.get("surfaces", []):
            surfaces.append(
                Surface(
                    host=s_raw["host"],
                    agent=s_raw.get("agent", "geordi"),
                    ssh_target=s_raw.get("ssh_target", ""),
                    codex_cli_path=s_raw.get("codex_cli_path", "codex"),
                    auth_file=s_raw.get("auth_file", "~/.codex/auth.json"),
                )
            )
        accounts.append(
            Account(
                name=acct_raw["name"],
                email=acct_raw.get("email", ""),
                priority=acct_raw.get("priority", 99),
                quota_source=acct_raw.get("quota_source", "manual"),
                quota_file=acct_raw.get("quota_file", ""),
                surfaces=surfaces,
            )
        )

    policy_raw = raw.get("policy", {})
    policy = Policy(
        min_remaining_pct=policy_raw.get("min_remaining_pct", 10.0),
        target_remaining_pct=policy_raw.get("target_remaining_pct", 50.0),
        drain_order=policy_raw.get("drain_order", "priority"),
        dry_run_default=policy_raw.get("dry_run_default", True),
    )

    ssh_config = raw.get("ssh", {})

    return accounts, policy, ssh_config


def _parse_yaml(path: Path) -> dict[str, Any]:
    """Parse YAML or JSON. Uses PyYAML if available, else a minimal fallback."""
    text = path.read_text()

    # Try PyYAML first
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Fallback: if it's actually JSON, parse as JSON
    if path.suffix == ".json":
        return json.loads(text)

    # Minimal YAML parser for our config schema (key: value, nested dicts, lists with -)
    return _minimal_yaml_parse(text)


def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """Minimal YAML parser for the accounts config schema.

    Handles the subset of YAML used by accounts.example.yaml:
    - Top-level keys with nested dicts
    - Lists of dicts (accounts, surfaces)
    - Scalar values (string, int, float, bool)
    - Comments (#)
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0

    # Stack of (indent_level, dict_ref) for nested dicts
    stack: list[tuple[int, dict[str, Any]]] = [(0, result)]

    while i < len(lines):
        raw_line = lines[i]
        # Strip comments
        line = raw_line.split("#")[0].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to matching indent
        while stack and stack[-1][0] > indent:
            stack.pop()
        if not stack:
            stack = [(0, result)]

        current = stack[-1][1]
        current_indent = stack[-1][0]

        # List item
        if stripped.startswith("- "):
            item_text = stripped[2:].strip()
            # Find the parent key that holds this list
            # The list belongs to the dict at the top of the stack
            # We need to detect list context — look at parent key
            # For our schema, lists are under "accounts:", "surfaces:"
            # This is a simplification; for robustness use PyYAML.
            raise ValueError(
                "List parsing requires PyYAML. Install with: pip install pyyaml"
            )

        # Key: value
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val == "":
                # Could be a nested dict or a list
                # Peek ahead
                next_i = i + 1
                while next_i < len(lines):
                    next_raw = lines[next_i].split("#")[0].rstrip()
                    if next_raw.strip():
                        break
                    next_i += 1

                if next_i < len(lines):
                    next_line = next_raw
                    next_indent = len(next_line) - len(next_line.lstrip())
                    next_stripped = next_line.strip()

                    if next_stripped.startswith("- "):
                        # It's a list — need PyYAML
                        raise ValueError(
                            f"List under '{key}' requires PyYAML. Install: pip install pyyaml"
                        )
                    elif next_indent > indent:
                        # Nested dict
                        new_dict: dict[str, Any] = {}
                        current[key] = new_dict
                        stack.append((indent, new_dict))
                    else:
                        current[key] = {}
                else:
                    current[key] = {}
            else:
                current[key] = _parse_scalar(val)

        i += 1

    return result


def _parse_scalar(val: str) -> Any:
    """Parse a YAML scalar value."""
    val = val.strip()
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    if val.lower() in ("null", "~", ""):
        return None
    # Try int
    try:
        return int(val)
    except ValueError:
        pass
    # Try float
    try:
        return float(val)
    except ValueError:
        pass
    # Strip quotes
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    return val


# ---------------------------------------------------------------------------
# Quota collection
# ---------------------------------------------------------------------------


def collect_quota(accounts: list[Account], state_dir: Path | None = None) -> None:
    """Populate quota fields on each account from its quota_file.

    Does not run scrapers — reads pre-populated state files. The scrape
    scripts (scrape-quota-openai-codex.sh) are expected to have run first.
    """
    state_dir = state_dir or DEFAULT_STATE_DIR

    for acct in accounts:
        if not acct.quota_file:
            continue

        raw_path = Path(acct.quota_file)
        if raw_path.is_absolute():
            qpath = raw_path
        elif raw_path.parts and raw_path.parts[0] == "state":
            # Strip leading "state/" since state_dir already points there
            qpath = state_dir / Path(*raw_path.parts[1:])
        else:
            qpath = state_dir / raw_path

        if not qpath.exists():
            continue

        try:
            data = json.loads(qpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("error"):
            acct.status = "unknown"
            continue

        acct.five_hour_remaining_pct = data.get("five_hour_remaining_pct")
        acct.weekly_remaining_pct = data.get("weekly_remaining_pct")
        acct.status = data.get("status", "unknown")


def set_manual_quota(
    accounts: list[Account], quotas: dict[str, dict[str, float | None]]
) -> None:
    """Set quota data manually for testing or manual override.

    quotas = {"luna": {"five_hour_remaining_pct": 45, "weekly_remaining_pct": 80, "status": "healthy"}}
    """
    for acct in accounts:
        q = quotas.get(acct.name)
        if not q:
            continue
        acct.five_hour_remaining_pct = q.get("five_hour_remaining_pct")
        acct.weekly_remaining_pct = q.get("weekly_remaining_pct")
        acct.status = q.get("status", "unknown")


# ---------------------------------------------------------------------------
# Ranking and policy
# ---------------------------------------------------------------------------


def rank_accounts(accounts: list[Account], policy: Policy) -> list[Account]:
    """Rank accounts by the configured drain order. Best first."""
    if policy.drain_order == "most_remaining":
        return sorted(accounts, key=lambda a: -a.effective_remaining)
    elif policy.drain_order == "round_robin":
        # Round-robin doesn't really rank; just return usable accounts in order
        return [a for a in accounts if a.is_usable] or list(accounts)
    else:  # priority
        return sorted(accounts, key=lambda a: (a.priority, -a.effective_remaining))


def best_candidate(
    accounts: list[Account], policy: Policy, exclude: str | None = None,
    surface_id: str | None = None,
) -> Account | None:
    """Return the best account to switch to, excluding a named account.

    If surface_id is given, only accounts that have that surface are candidates.
    """
    ranked = rank_accounts(accounts, policy)
    for acct in ranked:
        if exclude and acct.name == exclude:
            continue
        if not acct.is_usable:
            continue
        if acct.effective_remaining < policy.min_remaining_pct:
            continue
        # If we're looking for a specific surface, verify the candidate covers it
        if surface_id:
            if not any(s.id == surface_id for s in acct.surfaces):
                continue
        return acct
    return None


def generate_plan(
    accounts: list[Account],
    policy: Policy,
    current_assignments: dict[str, str] | None = None,
) -> list[SwitchAction]:
    """Generate a list of proposed switch actions.

    current_assignments maps surface_id -> current_account_name.
    If a surface is not in current_assignments, its current account is unknown.
    """
    current_assignments = current_assignments or {}
    actions: list[SwitchAction] = []

    for acct in accounts:
        for surface in acct.surfaces:
            sid = surface.id
            current_name = current_assignments.get(sid, acct.name)

            # Find the current account object
            current_acct = next(
                (a for a in accounts if a.name == current_name), None
            )
            current_remaining = (
                current_acct.effective_remaining if current_acct else None
            )

            # Check if current account needs switching
            needs_switch = False
            reason = ""

            if current_acct is None:
                needs_switch = True
                reason = "current account not found in config"
            elif not current_acct.is_usable:
                needs_switch = True
                reason = f"account '{current_name}' not usable (status={current_acct.status})"
            elif current_acct.effective_remaining < policy.min_remaining_pct:
                needs_switch = True
                reason = (
                    f"account '{current_name}' below min_remaining_pct "
                    f"({current_acct.effective_remaining:.0f}% < {policy.min_remaining_pct:.0f}%)"
                )

            if not needs_switch:
                actions.append(
                    SwitchAction(
                        surface_id=sid,
                        host=surface.host,
                        agent=surface.agent,
                        current_account=current_name,
                        proposed_account=current_name,
                        reason="sufficient quota, no switch needed",
                        current_remaining=current_remaining,
                        proposed_remaining=current_remaining,
                    )
                )
                continue

            # Find best alternative (must cover this specific surface)
            candidate = best_candidate(accounts, policy, exclude=current_name, surface_id=sid)
            if candidate is None:
                actions.append(
                    SwitchAction(
                        surface_id=sid,
                        host=surface.host,
                        agent=surface.agent,
                        current_account=current_name,
                        proposed_account=current_name,
                        reason=f"BLOCKED: no alternative account available ({reason})",
                        current_remaining=current_remaining,
                        proposed_remaining=None,
                    )
                )
            else:
                actions.append(
                    SwitchAction(
                        surface_id=sid,
                        host=surface.host,
                        agent=surface.agent,
                        current_account=current_name,
                        proposed_account=candidate.name,
                        reason=reason,
                        current_remaining=current_remaining,
                        proposed_remaining=candidate.effective_remaining,
                    )
                )

    return actions


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_plan(
    actions: list[SwitchAction], accounts: list[Account], confirm: bool = False
) -> dict[str, Any]:
    """Execute switch actions. Returns a result dict with applied/blocked counts.

    Only non-noop actions where proposed != current are executed.
    Requires confirm=True to actually run SSH commands.
    """
    account_map = {a.name: a for a in accounts}
    applied = []
    blocked = []
    noop = []

    for action in actions:
        # BLOCKED actions take priority over noop check (proposed==current when blocked)
        if action.reason.startswith("BLOCKED"):
            blocked.append(action.to_dict())
            continue

        if action.is_noop:
            noop.append(action.to_dict())
            continue

        if not confirm:
            blocked.append({**action.to_dict(), "block_reason": "dry_run"})
            continue

        target_acct = account_map.get(action.proposed_account)
        if not target_acct:
            blocked.append(
                {**action.to_dict(), "block_reason": "target_account_not_found"}
            )
            continue

        # Find the surface on the target account
        surface = next(
            (s for s in target_acct.surfaces if s.id == action.surface_id), None
        )
        if not surface:
            blocked.append(
                {**action.to_dict(), "block_reason": "surface_not_on_target_account"}
            )
            continue

        # Execute the switch via SSH
        result = _execute_switch(surface, target_acct)
        applied.append({**action.to_dict(), "exec_result": result})

    return {
        "applied": applied,
        "blocked": blocked,
        "noop": noop,
        "total": len(actions),
        "applied_count": len(applied),
        "blocked_count": len(blocked),
        "noop_count": len(noop),
    }


def _execute_switch(surface: Surface, account: Account) -> dict[str, Any]:
    """Execute an account switch on a surface via SSH.

    This reads the target account's auth file and writes it to the surface's
    auth_file path. In production, this would coordinate with the Codex CLI
    auth flow. For safety, this is a stub that logs intent.

    SECURITY: This function never logs or returns secret values.
    """
    ssh_target = surface.ssh_target
    if not ssh_target:
        return {"ok": False, "error": "no ssh_target configured"}

    # The actual switch mechanism depends on Codex CLI auth flow.
    # For now, we log the intent and return a planned action.
    # Real implementation would:
    # 1. Copy the target account's auth.json to the surface
    # 2. Verify the Codex CLI picks up the new auth
    # 3. Optionally restart any Codex daemon

    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",
        ssh_target,
        f"test -f {surface.auth_file} && echo AUTH_FILE_EXISTS || echo AUTH_FILE_MISSING",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip() if proc.returncode != 0 else "",
            "command": " ".join(cmd[:5]) + " ... <redacted>",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ssh_timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": "ssh_not_found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Status output
# ---------------------------------------------------------------------------


def format_status(accounts: list[Account], policy: Policy) -> str:
    """Format a human-readable status string."""
    lines = ["=== Fleet Account Drain Status ===", ""]

    for acct in accounts:
        icon = {
            "healthy": "🟢",
            "warning": "🟡",
            "exhausted": "🔴",
            "unknown": "⚪",
        }.get(acct.status, "⚪")

        h5 = (
            f"{acct.five_hour_remaining_pct:.0f}%"
            if acct.five_hour_remaining_pct is not None
            else "?"
        )
        wk = (
            f"{acct.weekly_remaining_pct:.0f}%"
            if acct.weekly_remaining_pct is not None
            else "?"
        )

        lines.append(f"{icon} {acct.name} (P{acct.priority}) — {acct.email}")
        lines.append(f"   5h: {h5} remaining | weekly: {wk} remaining | status: {acct.status}")
        for s in acct.surfaces:
            lines.append(f"   → {s.id}")

    lines.append("")
    lines.append(f"Policy: {policy.drain_order} | min={policy.min_remaining_pct:.0f}% | target={policy.target_remaining_pct:.0f}%")

    return "\n".join(lines)


def plan_to_json(actions: list[SwitchAction], accounts: list[Account]) -> str:
    """Serialize a plan to JSON."""
    plan = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accounts": [
            {
                "name": a.name,
                "priority": a.priority,
                "five_hour_remaining_pct": a.five_hour_remaining_pct,
                "weekly_remaining_pct": a.weekly_remaining_pct,
                "effective_remaining": a.effective_remaining,
                "status": a.status,
                "usable": a.is_usable,
            }
            for a in accounts
        ],
        "actions": [a.to_dict() for a in actions],
        "summary": {
            "total": len(actions),
            "switches": sum(1 for a in actions if not a.is_noop and not a.reason.startswith("BLOCKED")),
            "blocked": sum(1 for a in actions if a.reason.startswith("BLOCKED")),
            "noop": sum(1 for a in actions if a.is_noop),
        },
    }
    return json.dumps(plan, indent=2)


# ---------------------------------------------------------------------------
# FleetDrain convenience class
# ---------------------------------------------------------------------------


class FleetDrain:
    """Convenience wrapper that holds config + provides plan/apply/status."""

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG
        self.accounts: list[Account] = []
        self.policy = Policy()
        self.ssh_config: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        self.accounts, self.policy, self.ssh_config = load_config(self.config_path)
        collect_quota(self.accounts)
        self._loaded = True

    def status(self) -> str:
        if not self._loaded:
            self.load()
        return format_status(self.accounts, self.policy)

    def plan(self, current_assignments: dict[str, str] | None = None) -> list[SwitchAction]:
        if not self._loaded:
            self.load()
        return generate_plan(self.accounts, self.policy, current_assignments)

    def apply(self, actions: list[SwitchAction] | None = None, confirm: bool = False) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        if actions is None:
            actions = self.plan()
        return apply_plan(actions, self.accounts, confirm=confirm)
