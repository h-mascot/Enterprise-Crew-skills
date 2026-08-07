#!/usr/bin/env python3
"""
Core module for fleet-wide Codex account drain.

Planning is read-only and emits a reviewable artifact. Applying requires that
artifact, validates it against the current config digest, and performs remote
auth switches through a fail-closed SSH script.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config" / "accounts.yaml"
DEFAULT_STATE_DIR = SKILL_DIR / "state"
PLAN_SCHEMA_VERSION = 1

SSH_TARGET_RE = re.compile(r"^(?!-)(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]*$")
SAFE_REMOTE_PATH = re.compile(r"^[A-Za-z0-9_@%+=:,./~-]+$")
SAFE_ACCOUNT = re.compile(r"^[a-zA-Z0-9_.-]+$")
VALID_DRAIN_ORDERS = {"priority", "most_remaining", "round_robin"}
PLAN_V1_TOP_LEVEL_KEYS = {
    "schema_version",
    "generated_at",
    "config_digest",
    "current_assignments",
    "policy",
    "accounts",
    "actions",
    "summary",
    "actions_digest",
    "plan_digest",
}
ACTION_KEYS = {
    "surface_id",
    "host",
    "agent",
    "current_account",
    "proposed_account",
    "reason",
    "current_remaining_pct",
    "proposed_remaining_pct",
    "ssh_target",
    "codex_cli_path",
    "active_auth_path",
    "current_auth_source_path",
    "proposed_auth_source_path",
}


class ConfigError(ValueError):
    """Raised for invalid fleet drain config."""


class PlanValidationError(ValueError):
    """Raised when an apply artifact is invalid or drifted."""


@dataclass
class Surface:
    """Account-specific auth binding for one agent surface."""

    host: str
    agent: str
    ssh_target: str
    codex_cli_path: str
    active_auth_path: str
    auth_source_path: str
    current_account: Optional[str] = None

    @property
    def id(self) -> str:
        return "%s:%s" % (self.host, self.agent)

    @property
    def auth_file(self) -> str:
        """Backward-compatible alias for the active auth destination."""
        return self.active_auth_path


@dataclass
class Account:
    """A Codex account with quota info and surface auth sources."""

    name: str
    email: str
    priority: int
    quota_source: str
    quota_file: str
    surfaces: List[Surface] = field(default_factory=list)
    five_hour_remaining_pct: Optional[float] = None
    weekly_remaining_pct: Optional[float] = None
    status: str = "unknown"

    @property
    def effective_remaining(self) -> float:
        vals = [
            v
            for v in (self.five_hour_remaining_pct, self.weekly_remaining_pct)
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
    drain_order: str = "priority"
    dry_run_default: bool = True


@dataclass
class SwitchAction:
    """A single proposed action for one unique surface."""

    surface_id: str
    host: str
    agent: str
    current_account: str
    proposed_account: str
    reason: str
    current_remaining: Optional[float]
    proposed_remaining: Optional[float]
    ssh_target: str = ""
    codex_cli_path: str = ""
    active_auth_path: str = ""
    current_auth_source_path: str = ""
    proposed_auth_source_path: str = ""

    @property
    def is_noop(self) -> bool:
        return self.current_account == self.proposed_account and not self.reason.startswith("BLOCKED")

    @property
    def is_blocked(self) -> bool:
        return self.reason.startswith("BLOCKED")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "host": self.host,
            "agent": self.agent,
            "current_account": self.current_account,
            "proposed_account": self.proposed_account,
            "reason": self.reason,
            "current_remaining_pct": self.current_remaining,
            "proposed_remaining_pct": self.proposed_remaining,
            "ssh_target": self.ssh_target,
            "codex_cli_path": self.codex_cli_path,
            "active_auth_path": self.active_auth_path,
            "current_auth_source_path": self.current_auth_source_path,
            "proposed_auth_source_path": self.proposed_auth_source_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwitchAction":
        return cls(
            surface_id=str(data["surface_id"]),
            host=str(data["host"]),
            agent=str(data["agent"]),
            current_account=str(data["current_account"]),
            proposed_account=str(data["proposed_account"]),
            reason=str(data["reason"]),
            current_remaining=data["current_remaining_pct"],
            proposed_remaining=data["proposed_remaining_pct"],
            ssh_target=str(data["ssh_target"]),
            codex_cli_path=str(data["codex_cli_path"]),
            active_auth_path=str(data["active_auth_path"]),
            current_auth_source_path=str(data["current_auth_source_path"]),
            proposed_auth_source_path=str(data["proposed_auth_source_path"]),
        )


def load_config(config_path: Union[str, Path]) -> Tuple[List[Account], Policy, Dict[str, Any]]:
    """Load and validate account config from YAML or JSON."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError("Config not found: %s" % config_path)

    raw = _parse_config(config_path)
    policy = _load_policy(raw.get("policy", {}))

    accounts: List[Account] = []
    seen_accounts = set()
    for acct_raw in raw.get("accounts", []):
        name = str(acct_raw.get("name", "")).strip()
        if not name or not SAFE_ACCOUNT.match(name):
            raise ConfigError("invalid account name: %r" % name)
        if name in seen_accounts:
            raise ConfigError("duplicate account name: %s" % name)
        seen_accounts.add(name)

        surfaces = []
        for s_raw in acct_raw.get("surfaces", []):
            active_path = str(
                s_raw.get("active_auth_path")
                or s_raw.get("auth_file")
                or "~/.codex/auth.json"
            )
            source_path = str(
                s_raw.get("auth_source_path")
                or "~/.codex/accounts/%s/auth.json" % name
            )
            _validate_remote_path(active_path, "active_auth_path")
            _validate_remote_path(source_path, "auth_source_path")
            if active_path == source_path:
                raise ConfigError(
                    "auth_source_path must be distinct from active_auth_path for %s"
                    % name
                )
            surface = Surface(
                host=str(s_raw["host"]).strip(),
                agent=str(s_raw.get("agent", "geordi")).strip(),
                ssh_target=str(s_raw.get("ssh_target", "")).strip(),
                codex_cli_path=str(s_raw.get("codex_cli_path", "codex")).strip(),
                active_auth_path=active_path,
                auth_source_path=source_path,
                current_account=(
                    str(s_raw["current_account"]).strip()
                    if s_raw.get("current_account") is not None
                    else None
                ),
            )
            _validate_surface(surface)
            surfaces.append(surface)

        quota_file = str(acct_raw.get("quota_file", ""))
        if quota_file and not Path(quota_file).is_absolute() and any(
            part == ".." for part in Path(quota_file).parts
        ):
            raise ConfigError("quota_file contains path traversal")

        email = str(acct_raw.get("email", "")).strip()
        if not email:
            raise ConfigError(
                "account %r must have a non-empty email for identity verification"
                % name
            )
        accounts.append(
            Account(
                name=name,
                email=email,
                priority=int(acct_raw.get("priority", 99)),
                quota_source=str(acct_raw.get("quota_source", "manual")),
                quota_file=quota_file,
                surfaces=surfaces,
            )
        )

    _validate_unique_account_surfaces(accounts)
    surface_ids = set(_surface_index(accounts))
    account_names = {account.name for account in accounts}
    ssh_config = dict(raw.get("ssh", {}))
    current_assignments = raw.get("current_assignments", {}) or {}
    if not isinstance(current_assignments, dict):
        raise ConfigError("current_assignments must be a mapping")
    parsed_current_assignments = {
        str(k).strip(): str(v).strip() for k, v in current_assignments.items()
    }
    _validate_current_assignments(
        parsed_current_assignments,
        surface_ids,
        account_names,
        require_known_account=True,
    )
    ssh_config["_current_assignments"] = parsed_current_assignments
    return accounts, policy, ssh_config


def _parse_config(path: Path) -> Dict[str, Any]:
    text = path.read_text()
    if path.suffix == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ConfigError("YAML config requires PyYAML: %s" % path) from exc
    return yaml.safe_load(text) or {}


def _load_policy(raw: Dict[str, Any]) -> Policy:
    policy = Policy(
        min_remaining_pct=float(raw.get("min_remaining_pct", 10.0)),
        target_remaining_pct=float(raw.get("target_remaining_pct", 50.0)),
        drain_order=str(raw.get("drain_order", "priority")),
        dry_run_default=bool(raw.get("dry_run_default", True)),
    )
    if policy.min_remaining_pct < 0 or policy.min_remaining_pct > 100:
        raise ConfigError("min_remaining_pct must be between 0 and 100")
    if policy.target_remaining_pct < 0 or policy.target_remaining_pct > 100:
        raise ConfigError("target_remaining_pct must be between 0 and 100")
    if policy.min_remaining_pct > policy.target_remaining_pct:
        raise ConfigError("min_remaining_pct must be <= target_remaining_pct")
    if policy.drain_order not in VALID_DRAIN_ORDERS:
        raise ConfigError("invalid drain_order: %s" % policy.drain_order)
    return policy


def _validate_remote_path(path: str, field: str) -> None:
    if not path:
        raise ConfigError("%s is required" % field)
    if "\n" in path or "\r" in path or "\x00" in path:
        raise ConfigError("%s contains unsafe characters" % field)
    if not (path.startswith("~/") or path.startswith("/")):
        raise ConfigError("%s must be absolute or start with ~/" % field)
    if not SAFE_REMOTE_PATH.match(path):
        raise ConfigError("%s contains unsafe characters" % field)
    path_tail = path[2:] if path.startswith("~/") else path[1:]
    if any(part == ".." for part in path_tail.split("/")):
        raise ConfigError("%s contains path traversal" % field)


def _validate_surface(surface: Surface) -> None:
    if not surface.host or ":" in surface.host:
        raise ConfigError("surface host must be a non-empty friendly id")
    if not surface.agent or ":" in surface.agent:
        raise ConfigError("surface agent must be a non-empty friendly id")
    if not surface.ssh_target:
        raise ConfigError("ssh_target is required for %s" % surface.id)
    if not surface.codex_cli_path:
        raise ConfigError("codex_cli_path is required for %s" % surface.id)
    _validate_remote_path(surface.codex_cli_path, "codex_cli_path")
    if not SSH_TARGET_RE.match(surface.ssh_target):
        raise ConfigError("invalid ssh_target for %s" % surface.id)


def _validate_unique_account_surfaces(accounts: List[Account]) -> None:
    for account in accounts:
        seen = set()
        for surface in account.surfaces:
            if surface.id in seen:
                raise ConfigError(
                    "account %s declares surface %s more than once"
                    % (account.name, surface.id)
                )
            seen.add(surface.id)


def _validate_current_assignments(
    current_assignments: Dict[str, str],
    surface_ids: set,
    account_names: set,
    require_known_account: bool,
) -> None:
    for surface_id, account_name in current_assignments.items():
        if surface_id not in surface_ids:
            raise ConfigError("unknown current assignment surface: %s" % surface_id)
        if require_known_account and account_name not in account_names:
            raise ConfigError("unknown current assignment account: %s" % account_name)


def config_digest(config_path: Union[str, Path]) -> str:
    return hashlib.sha256(Path(config_path).read_bytes()).hexdigest()


def collect_quota(accounts: List[Account], state_dir: Optional[Path] = None) -> None:
    """Populate quota fields from pre-existing state files."""
    state_dir = state_dir or DEFAULT_STATE_DIR
    for acct in accounts:
        if not acct.quota_file:
            continue
        raw_path = Path(acct.quota_file)
        if raw_path.is_absolute():
            qpath = raw_path
        elif raw_path.parts and raw_path.parts[0] == "state":
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


def set_manual_quota(accounts: List[Account], quotas: Dict[str, Dict[str, Any]]) -> None:
    for acct in accounts:
        q = quotas.get(acct.name)
        if not q:
            continue
        acct.five_hour_remaining_pct = q.get("five_hour_remaining_pct")
        acct.weekly_remaining_pct = q.get("weekly_remaining_pct")
        acct.status = q.get("status", "unknown")


def rank_accounts(accounts: List[Account], policy: Policy) -> List[Account]:
    if policy.drain_order == "most_remaining":
        return sorted(accounts, key=lambda a: (-a.effective_remaining, a.priority, a.name))
    if policy.drain_order == "round_robin":
        return [a for a in accounts if a.is_usable] or list(accounts)
    return sorted(accounts, key=lambda a: (a.priority, -a.effective_remaining, a.name))


def best_candidate(
    accounts: List[Account],
    policy: Policy,
    exclude: Optional[str] = None,
    surface_id: Optional[str] = None,
) -> Optional[Account]:
    """Return a replacement account meeting target_remaining_pct."""
    for acct in rank_accounts(accounts, policy):
        if exclude and acct.name == exclude:
            continue
        if not acct.is_usable:
            continue
        if acct.effective_remaining < policy.target_remaining_pct:
            continue
        if surface_id and not any(s.id == surface_id for s in acct.surfaces):
            continue
        return acct
    return None


def generate_plan(
    accounts: List[Account],
    policy: Policy,
    current_assignments: Optional[Dict[str, str]] = None,
) -> List[SwitchAction]:
    """Generate at most one action per unique surface.

    Current account assignment must be explicit via current_assignments or a
    non-conflicting config declaration. Unknown or ambiguous current state
    yields BLOCKED actions and never infers from account ownership.
    """
    explicit_current = current_assignments or {}
    surface_index = _surface_index(accounts)
    _validate_current_assignments(
        explicit_current,
        set(surface_index),
        {account.name for account in accounts},
        require_known_account=False,
    )
    declared_current, declaration_errors = _declared_current_assignments(accounts)
    account_map = {a.name: a for a in accounts}
    actions: List[SwitchAction] = []

    for sid in sorted(surface_index):
        bindings = surface_index[sid]
        base_surface = bindings[0][1]
        surface_error = _surface_consistency_error(bindings)
        explicit_val = explicit_current.get(sid)
        current_name = (
            explicit_val
            if explicit_val is not None
            else declared_current.get(sid)
        )

        if surface_error:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    current_name or "UNKNOWN",
                    "BLOCKED: %s" % surface_error,
                )
            )
            continue
        if sid in declaration_errors and sid not in explicit_current:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    "AMBIGUOUS",
                    "BLOCKED: %s" % declaration_errors[sid],
                )
            )
            continue
        if not current_name:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    "UNKNOWN",
                    "BLOCKED: current account assignment is not declared",
                )
            )
            continue

        current_acct = account_map.get(current_name)
        if current_acct is None:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    current_name,
                    "BLOCKED: current account not found in config",
                )
            )
            continue

        current_surface = _surface_for_account(current_acct, sid)
        if current_surface is None:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    current_name,
                    "BLOCKED: current account has no auth source for surface",
                )
            )
            continue

        current_remaining = current_acct.effective_remaining
        reason = ""
        needs_switch = False
        if not current_acct.is_usable:
            needs_switch = True
            reason = "account '%s' not usable (status=%s)" % (
                current_name,
                current_acct.status,
            )
        elif current_remaining < policy.min_remaining_pct:
            needs_switch = True
            reason = (
                "account '%s' below min_remaining_pct (%.0f%% < %.0f%%)"
                % (current_name, current_remaining, policy.min_remaining_pct)
            )

        if not needs_switch:
            actions.append(
                SwitchAction(
                    surface_id=sid,
                    host=base_surface.host,
                    agent=base_surface.agent,
                    current_account=current_name,
                    proposed_account=current_name,
                    reason=(
                        "sufficient quota, no switch needed "
                        "(target_remaining_pct only gates replacements)"
                    ),
                    current_remaining=current_remaining,
                    proposed_remaining=current_remaining,
                    ssh_target=base_surface.ssh_target,
                    codex_cli_path=base_surface.codex_cli_path,
                    active_auth_path=base_surface.active_auth_path,
                    current_auth_source_path=current_surface.auth_source_path,
                    proposed_auth_source_path=current_surface.auth_source_path,
                )
            )
            continue

        candidate = best_candidate(accounts, policy, exclude=current_name, surface_id=sid)
        if candidate is None:
            actions.append(
                SwitchAction(
                    surface_id=sid,
                    host=base_surface.host,
                    agent=base_surface.agent,
                    current_account=current_name,
                    proposed_account=current_name,
                    reason=(
                        "BLOCKED: no replacement account at or above "
                        "target_remaining_pct (%.0f%%); %s"
                        % (policy.target_remaining_pct, reason)
                    ),
                    current_remaining=current_remaining,
                    proposed_remaining=None,
                    ssh_target=base_surface.ssh_target,
                    codex_cli_path=base_surface.codex_cli_path,
                    active_auth_path=base_surface.active_auth_path,
                    current_auth_source_path=current_surface.auth_source_path,
                    proposed_auth_source_path=current_surface.auth_source_path,
                )
            )
            continue

        candidate_surface = _surface_for_account(candidate, sid)
        if candidate_surface is None:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    current_name,
                    "BLOCKED: replacement has no auth source for surface",
                    current_surface.auth_source_path,
                )
            )
            continue
        if current_surface.auth_source_path == candidate_surface.auth_source_path:
            actions.append(
                _blocked_action(
                    sid,
                    base_surface,
                    current_name,
                    "BLOCKED: replacement auth source matches current auth source",
                    current_surface.auth_source_path,
                )
            )
            continue
        actions.append(
            SwitchAction(
                surface_id=sid,
                host=base_surface.host,
                agent=base_surface.agent,
                current_account=current_name,
                proposed_account=candidate.name,
                reason=reason,
                current_remaining=current_remaining,
                proposed_remaining=candidate.effective_remaining,
                ssh_target=base_surface.ssh_target,
                codex_cli_path=base_surface.codex_cli_path,
                active_auth_path=base_surface.active_auth_path,
                current_auth_source_path=current_surface.auth_source_path,
                proposed_auth_source_path=candidate_surface.auth_source_path,
            )
        )

    return actions


def _surface_index(accounts: List[Account]) -> Dict[str, List[Tuple[Account, Surface]]]:
    index: Dict[str, List[Tuple[Account, Surface]]] = {}
    for account in accounts:
        for surface in account.surfaces:
            index.setdefault(surface.id, []).append((account, surface))
    return index


def _declared_current_assignments(
    accounts: List[Account],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    values: Dict[str, set] = {}
    for account in accounts:
        for surface in account.surfaces:
            if surface.current_account:
                values.setdefault(surface.id, set()).add(surface.current_account)
    assignments: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for sid, declared in values.items():
        if len(declared) == 1:
            assignments[sid] = next(iter(declared))
        else:
            errors[sid] = "ambiguous current account declarations: %s" % ", ".join(
                sorted(declared)
            )
    return assignments, errors


def _surface_consistency_error(bindings: List[Tuple[Account, Surface]]) -> Optional[str]:
    first = bindings[0][1]
    for _, surface in bindings[1:]:
        if surface.ssh_target != first.ssh_target:
            return "surface has conflicting ssh_target values"
        if surface.codex_cli_path != first.codex_cli_path:
            return "surface has conflicting codex_cli_path values"
        if surface.active_auth_path != first.active_auth_path:
            return "surface has conflicting active_auth_path values"
    return None


def _surface_for_account(account: Account, surface_id: str) -> Optional[Surface]:
    return next((s for s in account.surfaces if s.id == surface_id), None)


def _blocked_action(
    sid: str,
    surface: Surface,
    current_account: str,
    reason: str,
    current_auth_source_path: str = "",
) -> SwitchAction:
    return SwitchAction(
        surface_id=sid,
        host=surface.host,
        agent=surface.agent,
        current_account=current_account,
        proposed_account=current_account,
        reason=reason,
        current_remaining=None,
        proposed_remaining=None,
        ssh_target=surface.ssh_target,
        codex_cli_path=surface.codex_cli_path,
        active_auth_path=surface.active_auth_path,
        current_auth_source_path=current_auth_source_path,
        proposed_auth_source_path=current_auth_source_path,
    )


def build_plan_artifact(
    actions: List[SwitchAction],
    accounts: List[Account],
    policy: Policy,
    digest: str,
    current_assignments: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    action_dicts = [a.to_dict() for a in actions]
    artifact = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config_digest": digest,
        "current_assignments": (
            dict(current_assignments)
            if current_assignments is not None
            else _assignments_from_actions(actions)
        ),
        "policy": _policy_snapshot(policy),
        "accounts": _accounts_snapshot(accounts),
        "actions": action_dicts,
        "summary": _summary(actions),
    }
    artifact["actions_digest"] = _actions_digest(action_dicts)
    artifact["plan_digest"] = _plan_digest(artifact)
    return artifact


def plan_to_json(
    actions: List[SwitchAction],
    accounts: List[Account],
    policy: Optional[Policy] = None,
    digest: str = "",
    current_assignments: Optional[Dict[str, str]] = None,
) -> str:
    policy = policy or Policy()
    artifact = build_plan_artifact(
        actions, accounts, policy, digest, current_assignments=current_assignments
    )
    return json.dumps(artifact, indent=2, sort_keys=True)


def load_plan_artifact(path: Union[str, Path]) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def write_plan_artifact(artifact: Dict[str, Any], path: Union[str, Path]) -> None:
    Path(path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")


def validate_plan_artifact(
    artifact: Dict[str, Any],
    accounts: List[Account],
    digest: str,
    policy: Optional[Policy] = None,
) -> List[SwitchAction]:
    if not isinstance(artifact, dict):
        raise PlanValidationError("plan artifact must be an object")
    if artifact.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise PlanValidationError("unsupported plan schema version")
    _validate_plan_v1_top_level_fields(artifact)
    actions_raw = artifact.get("actions")
    if not isinstance(actions_raw, list):
        raise PlanValidationError("plan actions must be a list")
    expected_actions_digest = artifact.get("actions_digest")
    if expected_actions_digest != _actions_digest(actions_raw):
        raise PlanValidationError("action digest drift")
    if artifact.get("plan_digest") != _plan_digest(artifact):
        raise PlanValidationError("plan digest drift")
    if artifact.get("config_digest") != digest:
        raise PlanValidationError("config digest drift")

    expected_actions: Optional[List[Dict[str, Any]]] = None
    if policy is not None:
        current_assignments = artifact.get("current_assignments")
        if not isinstance(current_assignments, dict):
            raise PlanValidationError("current_assignments snapshot must be an object")
        try:
            expected_actions = [
                action.to_dict()
                for action in generate_plan(accounts, policy, current_assignments)
            ]
        except ConfigError as exc:
            raise PlanValidationError(str(exc)) from exc

    surface_index = _surface_index(accounts)
    expected_surface_ids = set(surface_index)
    account_names = {a.name for a in accounts}
    seen = set()
    actions = []
    for raw in actions_raw:
        if not isinstance(raw, dict):
            raise PlanValidationError("plan action must be an object")
        _validate_action_v1_fields(raw)
        action = SwitchAction.from_dict(raw)
        if action.surface_id not in surface_index:
            raise PlanValidationError("unknown surface_id in plan: %s" % action.surface_id)
        if action.surface_id in seen:
            raise PlanValidationError("duplicate surface_id in plan: %s" % action.surface_id)
        seen.add(action.surface_id)
        if not action.is_blocked:
            if action.current_account not in account_names:
                raise PlanValidationError(
                    "unknown current account in plan: %s" % action.current_account
                )
            if action.proposed_account not in account_names:
                raise PlanValidationError(
                    "unknown proposed account in plan: %s" % action.proposed_account
                )
        _validate_action_matches_config(action, accounts, surface_index[action.surface_id])
        actions.append(action)
    missing = expected_surface_ids - seen
    if missing:
        raise PlanValidationError(
            "missing configured surface action: %s" % ", ".join(sorted(missing))
        )
    if expected_actions is not None and [action.to_dict() for action in actions] != expected_actions:
        raise PlanValidationError("action list drift")
    if artifact.get("summary") != _summary(actions):
        raise PlanValidationError("summary snapshot drift")
    if artifact.get("accounts") != _accounts_snapshot(accounts):
        raise PlanValidationError("account quota snapshot drift")
    if policy is not None and artifact.get("policy") != _policy_snapshot(policy):
        raise PlanValidationError("policy snapshot drift")
    _validate_current_assignments_snapshot(artifact.get("current_assignments"), actions)
    return actions


def _validate_action_matches_config(
    action: SwitchAction,
    accounts: List[Account],
    bindings: List[Tuple[Account, Surface]],
) -> None:
    surface_error = _surface_consistency_error(bindings)
    if surface_error:
        raise PlanValidationError(
            "configured surface binding is inconsistent for %s: %s"
            % (action.surface_id, surface_error)
        )
    expected_surface = bindings[0][1]
    for field_name, expected_value in (
        ("host", expected_surface.host),
        ("agent", expected_surface.agent),
        ("ssh_target", expected_surface.ssh_target),
        ("codex_cli_path", expected_surface.codex_cli_path),
        ("active_auth_path", expected_surface.active_auth_path),
    ):
        if getattr(action, field_name) != expected_value:
            raise PlanValidationError(
                "%s mismatch for %s" % (field_name, action.surface_id)
            )
    if action.surface_id != "%s:%s" % (action.host, action.agent):
        raise PlanValidationError("surface_id does not match host and agent")

    account_map = {account.name: account for account in accounts}
    _validate_action_auth_source(
        action,
        account_map,
        action.current_account,
        action.current_auth_source_path,
        "current_auth_source_path",
    )
    _validate_action_auth_source(
        action,
        account_map,
        action.proposed_account,
        action.proposed_auth_source_path,
        "proposed_auth_source_path",
    )
    if (
        not action.is_blocked
        and not action.is_noop
        and action.current_auth_source_path == action.proposed_auth_source_path
    ):
        raise PlanValidationError(
            "proposed auth source matches current auth source for %s"
            % action.surface_id
        )


def _validate_action_auth_source(
    action: SwitchAction,
    account_map: Dict[str, Account],
    account_name: str,
    artifact_path: str,
    field_name: str,
) -> None:
    account = account_map.get(account_name)
    if account is None:
        if action.is_blocked and artifact_path == "":
            return
        raise PlanValidationError("unknown account for %s: %s" % (field_name, account_name))
    surface = _surface_for_account(account, action.surface_id)
    if surface is None:
        raise PlanValidationError(
            "account %s has no configured auth source for %s"
            % (account_name, action.surface_id)
        )
    if artifact_path != surface.auth_source_path:
        raise PlanValidationError(
            "%s mismatch for %s" % (field_name, action.surface_id)
        )


def apply_plan(
    plan_artifact: Union[Dict[str, Any], List[SwitchAction]],
    accounts: List[Account],
    config_digest_value: str = "",
    policy: Optional[Policy] = None,
    confirm: bool = False,
    executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Apply a validated plan artifact.

    Passing a raw list of actions is kept for dry-run legacy tests only. A
    confirmed apply must pass a schema-versioned artifact.
    """
    if isinstance(plan_artifact, list):
        if confirm:
            raise PlanValidationError("confirmed apply requires a plan artifact")
        actions = plan_artifact
    else:
        if confirm and policy is None:
            raise PlanValidationError("confirmed apply requires current policy")
        actions = validate_plan_artifact(
            plan_artifact, accounts, config_digest_value, policy=policy
        )

    account_map = {a.name: a for a in accounts}
    applied: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    noop: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for action in actions:
        if action.is_blocked:
            blocked.append(action.to_dict())
            continue
        if action.is_noop:
            noop.append(action.to_dict())
            continue
        if not confirm:
            blocked.append(dict(action.to_dict(), block_reason="dry_run"))
            continue

        target_acct = account_map.get(action.proposed_account)
        if target_acct is None:
            failed.append(dict(action.to_dict(), block_reason="target_account_not_found"))
            continue
        current_acct = account_map.get(action.current_account)
        if current_acct is None:
            failed.append(dict(action.to_dict(), block_reason="current_account_not_found"))
            continue

        result = _execute_switch(action, current_acct, target_acct, executor=executor)
        action_result = dict(action.to_dict(), exec_result=result)
        if result.get("ok") is True and result.get("verified") is True:
            applied.append(action_result)
        else:
            failed.append(action_result)

    failed_count = len(failed)
    blocked_count = len(blocked)
    return {
        "applied": applied,
        "blocked": blocked,
        "noop": noop,
        "failed": failed,
        "total": len(actions),
        "applied_count": len(applied),
        "blocked_count": blocked_count,
        "noop_count": len(noop),
        "failed_count": failed_count,
        "ok": failed_count == 0 and (not confirm or blocked_count == 0),
    }


REMOTE_SWITCH_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail

emit() {
  local ok="$1"; shift
  local stage="$1"; shift
  local error="${1:-}"
  local verified="${2:-false}"
  local rolled_back="${3:-false}"
  python3 - "$ok" "$stage" "$error" "$verified" "$rolled_back" <<'PY'
import json, sys
ok, stage, error, verified, rolled_back = sys.argv[1:6]
payload = {
    "ok": ok == "true",
    "stage": stage,
    "verified": verified == "true",
    "rolled_back": rolled_back == "true",
}
if error:
    payload["error"] = error
print(json.dumps(payload, sort_keys=True))
PY
}

fail() {
  emit false "$1" "$2" false "${3:-false}"
  exit 1
}

mode_octal() {
  python3 - "$1" <<'PY'
import os, stat, sys
print(format(stat.S_IMODE(os.stat(sys.argv[1]).st_mode), "04o"))
PY
}

require_mode_0600() {
  local path="$1"
  local stage="$2"
  local error="$3"
  if [[ "$(mode_octal "$path")" != "0600" ]]; then
    fail "$stage" "$error"
  fi
}

reject_path_traversal() {
  local path="$1"
  local part
  IFS='/' read -r -a parts <<< "$path"
  for part in "${parts[@]}"; do
    if [[ "$part" == ".." ]]; then
      fail validate "path_traversal"
    fi
  done
}

expand_path() {
  local raw="$1"
  case "$raw" in
    "~/"*) printf '%s/%s' "$HOME" "${raw:2}" ;;
    /*) printf '%s' "$raw" ;;
    *) fail validate "path_must_be_absolute_or_home_relative" ;;
  esac
}

validate_json_file() {
  python3 - "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], "r") as handle:
        json.load(handle)
except Exception:
    sys.exit(1)
PY
}

identity_status() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, expected = sys.argv[1], sys.argv[2]
if not expected:
    print("skipped")
    sys.exit(0)
try:
    with open(path, "r") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
found = []
def walk(value, key=""):
    if isinstance(value, dict):
        for k, v in value.items():
            walk(v, k)
    elif isinstance(value, list):
        for item in value:
            walk(item, key)
    elif isinstance(value, str):
        lk = key.lower()
        if lk in ("email", "user_email", "account_email", "login", "username"):
            found.append(value)
walk(data)
if not found:
    print("absent")
elif expected in found:
    print("matched")
else:
    print("mismatch")
PY
}

current_src="$(expand_path "$1")"
target_src="$(expand_path "$2")"
active_dest="$(expand_path "$3")"
current_expected_email="${4:-}"
target_expected_email="${5:-}"
codex_cli="$(expand_path "$6")"

reject_path_traversal "$current_src"
reject_path_traversal "$target_src"
reject_path_traversal "$active_dest"
reject_path_traversal "$codex_cli"

[[ -f "$current_src" ]] || fail preflight "current_source_missing"
[[ -f "$target_src" ]] || fail preflight "target_source_missing"
[[ -f "$active_dest" ]] || fail preflight "active_auth_missing"
[[ -x "$codex_cli" ]] || fail preflight "codex_cli_not_executable"
validate_json_file "$current_src" || fail preflight "current_source_invalid_json"
validate_json_file "$target_src" || fail preflight "target_source_invalid_json"
validate_json_file "$active_dest" || fail preflight "active_auth_invalid_json"
require_mode_0600 "$current_src" preflight "current_source_mode_not_0600"
require_mode_0600 "$target_src" preflight "target_source_mode_not_0600"

cmp -s "$current_src" "$active_dest" || fail preflight "active_auth_does_not_match_declared_current"

current_identity="$(identity_status "$current_src" "$current_expected_email")" || fail preflight "current_identity_check_failed"
if [[ "$current_identity" == "mismatch" ]]; then
  fail preflight "current_identity_mismatch"
fi
if [[ "$current_identity" == "absent" ]]; then
  fail preflight "current_identity_absent"
fi
target_identity="$(identity_status "$target_src" "$target_expected_email")" || fail preflight "target_identity_check_failed"
if [[ "$target_identity" == "mismatch" ]]; then
  fail preflight "target_identity_mismatch"
fi
if [[ "$target_identity" == "absent" ]]; then
  fail preflight "target_identity_absent"
fi

backup="${active_dest}.fleet-drain-backup.$(date -u +%Y%m%dT%H%M%SZ).$$"
tmp="$(mktemp "${active_dest}.tmp.XXXXXX")"
cleanup_backup_pre_mutation() {
  rm -f "$backup" "$tmp"
}
cp -p "$active_dest" "$backup" || { cleanup_backup_pre_mutation; fail mutate "backup_failed"; }
chmod 0600 "$backup" || { cleanup_backup_pre_mutation; fail mutate "backup_chmod_failed"; }
if [[ "$(mode_octal "$backup")" != "0600" ]]; then
  cleanup_backup_pre_mutation
  fail mutate "backup_mode_not_0600"
fi
cp "$target_src" "$tmp" || { rm -f "$tmp"; fail mutate "copy_target_failed"; }
chmod 0600 "$tmp" || { rm -f "$tmp"; fail mutate "chmod_failed"; }
mv "$tmp" "$active_dest" || { rm -f "$tmp"; fail mutate "install_failed"; }

rollback_restore() {
  [[ -f "$backup" ]] || return 1
  mv "$backup" "$active_dest" >/dev/null 2>&1 || return 1
  chmod 0600 "$active_dest" >/dev/null 2>&1 || return 1
  cmp -s "$current_src" "$active_dest" || return 1
  [[ "$(mode_octal "$active_dest")" == "0600" ]] || return 1
}

fail_after_mutation() {
  local stage="$1"
  local error="$2"
  if rollback_restore; then
    fail "$stage" "$error" true
  fi
  emit false rollback "rollback_failed_after_${error}" false false
  exit 1
}

chmod 0600 "$active_dest" || fail_after_mutation verify "installed_auth_chmod_failed"
if [[ "$(mode_octal "$active_dest")" != "0600" ]]; then
  fail_after_mutation verify "installed_auth_mode_not_0600"
fi
if ! validate_json_file "$active_dest"; then
  fail_after_mutation verify "installed_auth_invalid_json"
fi
if ! cmp -s "$target_src" "$active_dest"; then
  fail_after_mutation verify "active_auth_does_not_match_target"
fi
active_identity="$(identity_status "$active_dest" "$target_expected_email")" || {
  fail_after_mutation verify "active_identity_check_failed"
}
if [[ "$active_identity" == "mismatch" ]]; then
  fail_after_mutation verify "active_identity_mismatch"
fi
if [[ "$active_identity" == "absent" ]]; then
  fail_after_mutation verify "active_identity_absent"
fi

if ! "$codex_cli" login status >/dev/null 2>&1; then
  fail_after_mutation verify "codex_login_status_failed"
fi

emit true complete "" true false
'''


def _execute_switch(
    action: SwitchAction,
    current_account: Account,
    target_account: Account,
    executor: Optional[Any] = None,
) -> Dict[str, Any]:
    if not action.ssh_target or not SSH_TARGET_RE.match(action.ssh_target):
        return {
            "ok": False,
            "verified": False,
            "stage": "local_validate",
            "error": "unsafe_or_missing_ssh_target",
        }
    for field_name, value in (
        ("current_auth_source_path", action.current_auth_source_path),
        ("proposed_auth_source_path", action.proposed_auth_source_path),
        ("active_auth_path", action.active_auth_path),
        ("codex_cli_path", action.codex_cli_path),
    ):
        try:
            _validate_remote_path(value, field_name)
        except ConfigError:
            return {
                "ok": False,
                "verified": False,
                "stage": "local_validate",
                "error": "unsafe_or_missing_%s" % field_name,
            }
    if action.current_auth_source_path == action.proposed_auth_source_path:
        return {
            "ok": False,
            "verified": False,
            "stage": "local_validate",
            "error": "same_auth_source_path",
        }

    remote_args = [
        action.current_auth_source_path,
        action.proposed_auth_source_path,
        action.active_auth_path,
        current_account.email,
        target_account.email,
        action.codex_cli_path,
    ]
    remote_command = "bash -s -- %s" % " ".join(shlex.quote(arg) for arg in remote_args)
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "BatchMode=yes",
        action.ssh_target,
        remote_command,
    ]
    run = executor or subprocess.run
    try:
        proc = run(
            cmd,
            input=REMOTE_SWITCH_SCRIPT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "verified": False, "stage": "ssh", "error": "ssh_timeout"}
    except FileNotFoundError:
        return {"ok": False, "verified": False, "stage": "ssh", "error": "ssh_not_found"}
    except Exception as exc:
        return {"ok": False, "verified": False, "stage": "ssh", "error": str(exc)}

    parsed = _parse_remote_result(proc.stdout)
    if parsed is None:
        return {
            "ok": False,
            "verified": False,
            "stage": "ssh",
            "error": "invalid_remote_result",
            "returncode": proc.returncode,
        }
    parsed["returncode"] = proc.returncode
    if proc.returncode != 0:
        parsed["ok"] = False
    return parsed


def _parse_remote_result(stdout: str) -> Optional[Dict[str, Any]]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def format_status(accounts: List[Account], policy: Policy) -> str:
    lines = ["=== Fleet Account Drain Status ===", ""]
    icons = {"healthy": "[ok]", "warning": "[warn]", "exhausted": "[down]", "unknown": "[?]"}
    for acct in accounts:
        h5 = "%0.0f%%" % acct.five_hour_remaining_pct if acct.five_hour_remaining_pct is not None else "?"
        wk = "%0.0f%%" % acct.weekly_remaining_pct if acct.weekly_remaining_pct is not None else "?"
        lines.append("%s %s (P%s)" % (icons.get(acct.status, "[?]"), acct.name, acct.priority))
        if acct.email:
            lines.append("   email: %s" % acct.email)
        lines.append("   5h: %s remaining | weekly: %s remaining | status: %s" % (h5, wk, acct.status))
        for surface in acct.surfaces:
            current = " current=%s" % surface.current_account if surface.current_account else ""
            lines.append("   -> %s source=%s%s" % (surface.id, surface.auth_source_path, current))
    lines.append("")
    lines.append(
        "Policy: %s | min=%0.0f%% | target=%0.0f%%"
        % (policy.drain_order, policy.min_remaining_pct, policy.target_remaining_pct)
    )
    return "\n".join(lines)


def _summary(actions: Iterable[SwitchAction]) -> Dict[str, int]:
    actions_list = list(actions)
    return {
        "total": len(actions_list),
        "switches": sum(1 for a in actions_list if not a.is_noop and not a.is_blocked),
        "blocked": sum(1 for a in actions_list if a.is_blocked),
        "noop": sum(1 for a in actions_list if a.is_noop),
    }


def _policy_snapshot(policy: Policy) -> Dict[str, Any]:
    return {
        "min_remaining_pct": policy.min_remaining_pct,
        "target_remaining_pct": policy.target_remaining_pct,
        "drain_order": policy.drain_order,
        "dry_run_default": policy.dry_run_default,
    }


def _accounts_snapshot(accounts: List[Account]) -> List[Dict[str, Any]]:
    return [
        {
            "name": account.name,
            "priority": account.priority,
            "five_hour_remaining_pct": account.five_hour_remaining_pct,
            "weekly_remaining_pct": account.weekly_remaining_pct,
            "effective_remaining": account.effective_remaining,
            "status": account.status,
            "usable": account.is_usable,
        }
        for account in accounts
    ]


def _assignments_from_actions(actions: Iterable[SwitchAction]) -> Dict[str, str]:
    assignments = {}
    for action in actions:
        if action.current_account in ("UNKNOWN", "AMBIGUOUS"):
            continue
        assignments[action.surface_id] = action.current_account
    return assignments


def _validate_plan_v1_top_level_fields(artifact: Dict[str, Any]) -> None:
    actual = set(artifact)
    missing = PLAN_V1_TOP_LEVEL_KEYS - actual
    extra = actual - PLAN_V1_TOP_LEVEL_KEYS
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing %s" % ", ".join(sorted(missing)))
        if extra:
            parts.append("unknown %s" % ", ".join(sorted(extra)))
        raise PlanValidationError("invalid plan top-level fields: %s" % "; ".join(parts))


def _validate_action_v1_fields(action: Dict[str, Any]) -> None:
    actual = set(action)
    missing = ACTION_KEYS - actual
    extra = actual - ACTION_KEYS
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing %s" % ", ".join(sorted(missing)))
        if extra:
            parts.append("unknown %s" % ", ".join(sorted(str(key) for key in extra)))
        raise PlanValidationError("invalid action fields: %s" % "; ".join(parts))


def _validate_current_assignments_snapshot(
    current_assignments: Any, actions: List[SwitchAction]
) -> None:
    if not isinstance(current_assignments, dict):
        raise PlanValidationError("current_assignments snapshot must be an object")
    action_map = {action.surface_id: action for action in actions}
    expected = _assignments_from_actions(actions)
    for surface_id, account_name in current_assignments.items():
        if not isinstance(surface_id, str) or not isinstance(account_name, str):
            raise PlanValidationError("current_assignments snapshot drift")
        action = action_map.get(surface_id)
        if action is None or action.current_account != account_name:
            raise PlanValidationError("current_assignments snapshot drift")
    if current_assignments != expected:
        raise PlanValidationError("current_assignments snapshot drift")


def _actions_digest(actions: List[Dict[str, Any]]) -> str:
    canonical = json.dumps(actions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_digest(artifact: Dict[str, Any]) -> str:
    canonical_artifact = dict(artifact)
    canonical_artifact.pop("plan_digest", None)
    canonical = json.dumps(canonical_artifact, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FleetDrain:
    """Convenience wrapper that holds config, plan, apply, and status."""

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG
        self.accounts: List[Account] = []
        self.policy = Policy()
        self.ssh_config: Dict[str, Any] = {}
        self.current_assignments: Dict[str, str] = {}
        self.digest = ""
        self._loaded = False

    def load(self) -> None:
        self.accounts, self.policy, self.ssh_config = load_config(self.config_path)
        self.current_assignments = dict(self.ssh_config.get("_current_assignments", {}))
        self.digest = config_digest(self.config_path)
        collect_quota(self.accounts)
        self._loaded = True

    def status(self) -> str:
        if not self._loaded:
            self.load()
        return format_status(self.accounts, self.policy)

    def plan(self, current_assignments: Optional[Dict[str, str]] = None) -> List[SwitchAction]:
        if not self._loaded:
            self.load()
        merged = dict(self.current_assignments)
        if current_assignments:
            merged.update(current_assignments)
        return generate_plan(self.accounts, self.policy, merged)

    def plan_artifact(
        self, current_assignments: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        if not self._loaded:
            self.load()
        merged = dict(self.current_assignments)
        if current_assignments:
            merged.update(current_assignments)
        actions = generate_plan(self.accounts, self.policy, merged)
        return build_plan_artifact(actions, self.accounts, self.policy, self.digest, merged)

    def apply(
        self,
        plan_artifact: Dict[str, Any],
        confirm: bool = False,
        executor: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if not self._loaded:
            self.load()
        return apply_plan(
            plan_artifact,
            self.accounts,
            config_digest_value=self.digest,
            policy=self.policy,
            confirm=confirm,
            executor=executor,
        )


if __name__ == "__main__":
    from fleet_drain_cli import main

    sys.exit(main())
