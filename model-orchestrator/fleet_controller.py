#!/usr/bin/env python3
"""Fleet-wide Codex account drain policy and executor.

The controller is intentionally small and standard-library only. It plans by
default, executes only through an explicit apply path, and treats host/agent
commands as argv arrays all the way down.
"""

from __future__ import annotations

import copy
import dataclasses
import fcntl
import json
import math
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_DRAIN_THRESHOLD = 20
DEFAULT_RECOVERY_THRESHOLD = 35
DEFAULT_MAX_STATUS_AGE_SECONDS = 900
DEFAULT_ACTION_TIMEOUT_SECONDS = 60
DEFAULT_SWITCH_COOLDOWN_SECONDS = 600
DEFAULT_ALTERNATE_HEALTH_ALLOWLIST = ("ready", "healthy", "ok")
SUPPORTED_SCHEMA_VERSION = "2026-08-07.codex-fleet.v1"
SKILL_DIR = Path(__file__).resolve().parent
ALLOWED_TRANSPORT_TYPES = {"local", "ssh"}
ALLOWED_HEALTH_VALUES = {"active", "ready", "healthy", "ok", "degraded"}
ALLOWED_CONFIDENCE_VALUES = {"exact"}
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_FLAG_RE = re.compile(r"(token|secret|password|passwd|api[-_]?key|authorization|credential)", re.I)
SECRET_KEY_RE = re.compile(
    r"^(?:[A-Za-z0-9_.]+[-_])?(token|secret|password|passwd|api[-_]?key|authorization|credential)(?:[-_][A-Za-z0-9_.]+)?$",
    re.I,
)
SECRET_VALUE_RE = re.compile(r"^(sk-[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]+|[A-Za-z0-9_/-]{40,}={0,2})$")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api[-_]?key|authorization|credential)(=)([^&\s]+)"
)
STATE_ACTIVE_ALIAS_FIELDS = ("activeAlias", "active_alias", "currentAlias", "current_alias", "active", "current")
STATE_AUTO_SWITCH_FIELDS = ("autoSwitch", "auto_switch")
ALIAS_QUOTA_TIMESTAMP_FIELDS = (
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
)
@dataclasses.dataclass(frozen=True)
class AccountQuota:
    alias: str
    limit5h_remaining_percent: float | None
    limit_week_remaining_percent: float | None
    floor_percent: float | None
    checked_at: str | None = None
    stale: bool = False
    health: str = "unknown"
    confidence: str = "unknown"
    manual_only: bool = False
    active: bool = False


@dataclasses.dataclass(frozen=True)
class HostQuotaStatus:
    active_alias: str | None
    auto_switch: bool | None
    accounts: dict[str, AccountQuota]


@dataclasses.dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SubprocessExecutor:
    def run(self, argv: Sequence[str], timeout: int | None = None) -> CommandResult:
        proc = subprocess.run(
            list(argv),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return CommandResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


EXPECTED_EXECUTOR_EXCEPTIONS = (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError)


def _timeout_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def command_exception_result(exc: subprocess.SubprocessError | OSError, context: str) -> tuple[CommandResult, str, str]:
    error_class = exc.__class__.__name__
    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            CommandResult(
                returncode=124,
                stdout=_timeout_stream(exc.stdout),
                stderr=f"{context} timed out",
            ),
            error_class,
            f"executor raised {error_class} while running {context}",
        )
    return (
        CommandResult(returncode=getattr(exc, "returncode", None) or 1),
        error_class,
        f"executor raised {error_class} while running {context}",
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_percent(value: Any) -> float | None:
    if value in (None, "", "unknown", "?"):
        return None
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if parsed < 0 or parsed > 100:
        return None
    return parsed


def lower_known_floor(*values: Any) -> float | None:
    known = [pct for pct in (parse_percent(v) for v in values) if pct is not None]
    return min(known) if known else None


def _first_present(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _bool_value_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return None


def _bool_value(value: Any, default: bool = False) -> bool:
    parsed = _bool_value_or_none(value)
    if parsed is not None:
        return parsed
    return default


def _iter_aliases(raw_aliases: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(raw_aliases, Mapping):
        result = []
        for alias, details in raw_aliases.items():
            if isinstance(details, Mapping):
                result.append((str(details.get("alias") or details.get("name") or alias), details))
        return result
    if isinstance(raw_aliases, list):
        result = []
        for details in raw_aliases:
            if not isinstance(details, Mapping):
                continue
            alias = details.get("alias") or details.get("name") or details.get("id")
            if alias:
                result.append((str(alias), details))
        return result
    return []


def parse_keyring_status(payload: Mapping[str, Any], now: str | None = None, max_age_seconds: int | None = None) -> HostQuotaStatus:
    state = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
    active_alias = _first_present(state, STATE_ACTIVE_ALIAS_FIELDS)
    auto_switch = _first_present(state, STATE_AUTO_SWITCH_FIELDS)

    now_dt = parse_iso8601(now) if now else datetime.now(timezone.utc)
    accounts: dict[str, AccountQuota] = {}
    active_from_alias = None
    for alias, details in _iter_aliases(payload.get("aliases")):
        five_hour = parse_percent(_first_present(details, ["limit5hRemainingPercent", "limit5h_remaining_percent"]))
        weekly = parse_percent(_first_present(details, ["limitWeekRemainingPercent", "limit_week_remaining_percent"]))
        floor = lower_known_floor(five_hour, weekly)
        checked_at = _first_present(
            details,
            ALIAS_QUOTA_TIMESTAMP_FIELDS,
        )
        checked_dt = parse_iso8601(str(checked_at)) if checked_at else None
        stale = False
        if max_age_seconds is not None:
            stale = checked_dt is None or checked_dt > now_dt or (now_dt - checked_dt).total_seconds() > max_age_seconds
        is_active = _bool_value(_first_present(details, ["active", "isActive", "is_active"]), default=False)
        if is_active:
            active_from_alias = alias
        accounts[alias] = AccountQuota(
            alias=alias,
            limit5h_remaining_percent=five_hour,
            limit_week_remaining_percent=weekly,
            floor_percent=floor,
            checked_at=str(checked_at) if checked_at else None,
            stale=stale,
            health=str(details.get("health") or "unknown"),
            confidence=str(details.get("confidence") or "unknown"),
            manual_only=_bool_value(_first_present(details, ["manualOnly", "manual_only", "manual"]), default=False),
            active=is_active,
        )

    return HostQuotaStatus(
        active_alias=str(active_alias or active_from_alias) if (active_alias or active_from_alias) else None,
        auto_switch=_bool_value(auto_switch) if auto_switch is not None else None,
        accounts=accounts,
    )


def keyring_state_matches(payload: Any, expected_active_alias: Any, expected_auto_switch: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    state = payload.get("state")
    if not isinstance(state, Mapping):
        return False
    actual_alias = _first_present(state, STATE_ACTIVE_ALIAS_FIELDS)
    actual_auto = _bool_value_or_none(_first_present(state, STATE_AUTO_SWITCH_FIELDS))
    expected_auto = _bool_value_or_none(expected_auto_switch)
    if actual_alias is None or expected_active_alias is None or actual_auto is None or expected_auto is None:
        return False
    return str(actual_alias) == str(expected_active_alias) and actual_auto is expected_auto


def policy_value(config: Mapping[str, Any], key: str, default: Any) -> Any:
    return (config.get("policy") or {}).get(key, default)


def _as_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _as_positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _as_non_negative_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _ensure_argv_array(value: Any, name: str) -> None:
    if not is_non_empty_argv_array(value):
        raise ValueError(f"{name} must be a non-empty argv array")


def _ensure_command_string(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty command string")


def is_non_empty_argv_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, (str, int, float, bool)) for item in value)


def normalized_argv(value: Any) -> list[str] | None:
    if not is_non_empty_argv_array(value):
        return None
    return [str(item) for item in value]


def _validate_transport(transport: Any, owner: str) -> None:
    if transport is None:
        return
    if not isinstance(transport, Mapping):
        raise ValueError(f"transport for {owner} must be an object")
    transport_type = transport.get("type", "local")
    if transport_type not in ALLOWED_TRANSPORT_TYPES:
        raise ValueError(f"unsupported transport type for {owner}: {transport_type}")
    if transport_type == "ssh" and not transport.get("host"):
        raise ValueError(f"ssh transport for {owner} requires host")
    if "ssh_args" in transport and not isinstance(transport["ssh_args"], list):
        raise ValueError(f"ssh_args for {owner} must be a list")


def validate_config(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise ValueError("config must be an object")
    schema_version = config.get("schema_version")
    if schema_version is not None and schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {schema_version}")
    policy = config.get("policy") or {}
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be an object")
    for path_key in ("state_file", "receipt_dir", "lock_file"):
        if path_key in config and not isinstance(config[path_key], str):
            raise ValueError(f"{path_key} must be a string")

    drain = _as_float(policy.get("drain_threshold_percent", DEFAULT_DRAIN_THRESHOLD), "drain_threshold_percent")
    recovery = _as_float(policy.get("recovery_threshold_percent", DEFAULT_RECOVERY_THRESHOLD), "recovery_threshold_percent")
    if drain < 0 or drain > 100:
        raise ValueError("drain_threshold_percent must be between 0 and 100")
    if recovery < 0 or recovery > 100:
        raise ValueError("recovery_threshold_percent must be between 0 and 100")
    if recovery <= drain:
        raise ValueError("recovery_threshold_percent must be greater than drain_threshold_percent")

    _as_non_negative_int(policy.get("max_status_age_seconds", DEFAULT_MAX_STATUS_AGE_SECONDS), "max_status_age_seconds")
    _as_positive_int(policy.get("default_action_timeout_seconds", DEFAULT_ACTION_TIMEOUT_SECONDS), "default_action_timeout_seconds")
    _as_non_negative_int(policy.get("switch_cooldown_seconds", DEFAULT_SWITCH_COOLDOWN_SECONDS), "switch_cooldown_seconds")

    confidence = policy.get("alternate_required_confidence", "exact")
    if confidence not in ALLOWED_CONFIDENCE_VALUES:
        raise ValueError("alternate_required_confidence must be exact")
    health_allowlist = policy.get("alternate_health_allowlist", DEFAULT_ALTERNATE_HEALTH_ALLOWLIST)
    if not isinstance(health_allowlist, (list, tuple)) or not health_allowlist:
        raise ValueError("alternate_health_allowlist must be a non-empty list")
    invalid_health = set(str(value) for value in health_allowlist) - ALLOWED_HEALTH_VALUES
    if invalid_health:
        raise ValueError(f"invalid alternate health values: {sorted(invalid_health)}")

    hosts = config.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("hosts must be a non-empty list")
    host_ids: set[str] = set()
    for host in hosts:
        if not isinstance(host, Mapping):
            raise ValueError("host entries must be objects")
        host_id = str(host.get("id") or "")
        if not host_id:
            raise ValueError("host id is required")
        if host_id in host_ids:
            raise ValueError(f"duplicate host id: {host_id}")
        host_ids.add(host_id)
        _validate_transport(host.get("transport") or {"type": "local"}, f"host {host_id}")
        if "keyring_binary" in host:
            _ensure_command_string(host["keyring_binary"], f"keyring_binary for host {host_id}")
        if "status_command" in host:
            _ensure_argv_array(host["status_command"], f"status_command for host {host_id}")
        if "status_timeout_seconds" in host:
            _as_positive_int(host["status_timeout_seconds"], f"status_timeout_seconds for host {host_id}")
        if "action_timeout_seconds" in host:
            _as_positive_int(host["action_timeout_seconds"], f"action_timeout_seconds for host {host_id}")

        agents = host.get("agents") or []
        if not isinstance(agents, list):
            raise ValueError(f"agents for host {host_id} must be a list")
        agent_ids: set[str] = set()
        enabled_agent_count = 0
        for configured_agent in agents:
            if not isinstance(configured_agent, Mapping):
                raise ValueError(f"agent entries for host {host_id} must be objects")
            if not configured_agent.get("enabled", True):
                continue
            enabled_agent_count += 1
            agent_id = str(configured_agent.get("id") or "")
            if not agent_id:
                raise ValueError(f"agent id is required on host {host_id}")
            if agent_id in agent_ids:
                raise ValueError(f"duplicate agent id on host {host_id}: {agent_id}")
            agent_ids.add(agent_id)
            _validate_transport(configured_agent.get("transport"), f"agent {agent_id} on host {host_id}")
            for command_key in ("drain_command", "resume_command", "fallback_command", "restore_command"):
                _ensure_argv_array(configured_agent.get(command_key), f"{command_key} for agent {agent_id} on host {host_id}")
            if "refresh_command" in configured_agent:
                _ensure_argv_array(configured_agent["refresh_command"], f"refresh_command for agent {agent_id} on host {host_id}")
            for timeout_key in ("timeout_seconds", "agent_drain_timeout_seconds", "agent_resume_timeout_seconds", "agent_fallback_timeout_seconds", "agent_restore_timeout_seconds", "agent_refresh_timeout_seconds"):
                if timeout_key in configured_agent:
                    _as_positive_int(configured_agent[timeout_key], f"{timeout_key} for agent {agent_id} on host {host_id}")
        if enabled_agent_count == 0:
            raise ValueError(f"host {host_id} must have at least one enabled agent")


def default_action_timeout(config: Mapping[str, Any]) -> int:
    return int(policy_value(config, "default_action_timeout_seconds", DEFAULT_ACTION_TIMEOUT_SECONDS))


def action_timeout(config: Mapping[str, Any], host: Mapping[str, Any] | None = None, agent: Mapping[str, Any] | None = None, kind: str | None = None) -> int:
    candidates: list[tuple[Mapping[str, Any] | None, str]] = []
    if kind:
        candidates.append((agent, f"{kind}_timeout_seconds"))
        candidates.append((host, f"{kind}_timeout_seconds"))
        candidates.append((config.get("policy") or {}, f"{kind}_timeout_seconds"))
    candidates.extend(
        [
            (agent, "timeout_seconds"),
            (host, "action_timeout_seconds"),
            (config.get("policy") or {}, "default_action_timeout_seconds"),
        ]
    )
    for mapping, key in candidates:
        if isinstance(mapping, Mapping) and key in mapping:
            return _as_positive_int(mapping[key], key)
    return DEFAULT_ACTION_TIMEOUT_SECONDS


def active_status_block_reason(active_alias: str | None, account: AccountQuota | None, config: Mapping[str, Any]) -> str | None:
    allow_unknown = bool(policy_value(config, "allow_unknown_quota", False))
    if not active_alias:
        return "missing_active_alias"
    if account is None:
        return "missing_active_account"
    if allow_unknown:
        return None
    if account.floor_percent is None:
        return "unknown_quota"
    if account.stale:
        return "stale_quota"
    if account.confidence != "exact":
        return "inexact_quota"
    return None


def is_account_eligible(account: AccountQuota | None, config: Mapping[str, Any], threshold: float | None = None) -> bool:
    allow_unknown = bool(policy_value(config, "allow_unknown_quota", False))
    if account is None:
        return False
    if account.stale or account.floor_percent is None:
        return allow_unknown
    return threshold is None or account.floor_percent >= threshold


def is_account_above_drain(account: AccountQuota | None, config: Mapping[str, Any]) -> bool:
    allow_unknown = bool(policy_value(config, "allow_unknown_quota", False))
    drain = float(policy_value(config, "drain_threshold_percent", DEFAULT_DRAIN_THRESHOLD))
    if account is None:
        return False
    if account.stale or account.floor_percent is None:
        return allow_unknown
    return account.floor_percent > drain


def is_account_recovered(account: AccountQuota | None, config: Mapping[str, Any]) -> bool:
    recovery = float(policy_value(config, "recovery_threshold_percent", DEFAULT_RECOVERY_THRESHOLD))
    return is_account_eligible(account, config, threshold=recovery)


def is_alternate_eligible(account: AccountQuota | None, config: Mapping[str, Any], threshold: float | None = None) -> bool:
    if account is None or account.stale or account.floor_percent is None:
        return False
    required_confidence = str(policy_value(config, "alternate_required_confidence", "exact"))
    health_allowlist = {str(value) for value in policy_value(config, "alternate_health_allowlist", DEFAULT_ALTERNATE_HEALTH_ALLOWLIST)}
    if account.confidence != required_confidence:
        return False
    if account.manual_only:
        return False
    if account.health not in health_allowlist:
        return False
    return threshold is None or account.floor_percent >= threshold


def expand_argv(argv: Sequence[Any], context: Mapping[str, Any]) -> list[str]:
    expanded: list[str] = []
    for raw_arg in argv:
        arg = str(raw_arg)

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                raise ValueError(f"unknown placeholder {{{key}}} in configured argv")
            return str(context[key])

        expanded.append(PLACEHOLDER_RE.sub(replace, arg))
    return expanded


def build_transport_argv(host: Mapping[str, Any], argv: Sequence[str]) -> list[str]:
    transport = host.get("transport") or {"type": "local"}
    transport_type = transport.get("type", "local")
    if transport_type == "local":
        return list(argv)
    if transport_type != "ssh":
        raise ValueError(f"unsupported transport type for host {host.get('id')}: {transport_type}")

    remote_host = transport.get("host")
    if not remote_host:
        raise ValueError(f"ssh transport for host {host.get('id')} requires host")
    destination = str(remote_host)
    if transport.get("user"):
        destination = f"{transport['user']}@{destination}"

    ssh_argv = ["ssh"]
    if transport.get("port"):
        ssh_argv.extend(["-p", str(transport["port"])])
    for extra_arg in transport.get("ssh_args", []):
        ssh_argv.append(str(extra_arg))
    ssh_argv.append(destination)
    ssh_argv.append(shlex.join([str(part) for part in argv]))
    return ssh_argv


def build_keyring_argv(host: Mapping[str, Any], subargs: Sequence[str]) -> list[str]:
    keyring_binary = host.get("keyring_binary", "codex-keyring")
    _ensure_command_string(keyring_binary, f"keyring_binary for host {host.get('id')}")
    return build_transport_argv(host, [keyring_binary, *[str(arg) for arg in subargs]])


def build_status_argv(host: Mapping[str, Any]) -> list[str]:
    if "status_command" in host:
        context = {
            "host_id": str(host.get("id") or ""),
            "skill_dir": str(SKILL_DIR),
        }
        return build_transport_argv(host, expand_argv(host["status_command"], context))
    return build_keyring_argv(host, ["status", "--json"])


def build_agent_argv(host: Mapping[str, Any], agent: Mapping[str, Any], argv: Sequence[str]) -> list[str]:
    if "transport" not in agent:
        return build_transport_argv(host, argv)
    agent_host = dict(host)
    agent_host["transport"] = agent["transport"]
    return build_transport_argv(agent_host, argv)


def agent_context(host: Mapping[str, Any], agent: Mapping[str, Any], active_alias: str | None, target_alias: str | None = None) -> dict[str, str]:
    agent_id = str(agent.get("id") or agent.get("name") or "")
    if not agent_id:
        raise ValueError(f"agent without id on host {host.get('id')}")
    return {
        "host_id": str(host.get("id")),
        "agent_id": agent_id,
        "agent_name": str(agent.get("name") or agent_id),
        "active_alias": str(active_alias or ""),
        "target_alias": str(target_alias or ""),
    }


def agent_action(
    kind: str,
    config: Mapping[str, Any],
    host: Mapping[str, Any],
    agent: Mapping[str, Any],
    command_key: str,
    active_alias: str | None,
    target_alias: str | None = None,
) -> dict[str, Any]:
    command = agent.get(command_key)
    if not isinstance(command, list):
        raise ValueError(f"agent {agent.get('id')} on host {host.get('id')} missing {command_key} argv array")
    context = agent_context(host, agent, active_alias, target_alias)
    raw_argv = expand_argv(command, context)
    return {
        "kind": kind,
        "host": str(host.get("id")),
        "agent": context["agent_id"],
        "argv": build_agent_argv(host, agent, raw_argv),
        "timeout_seconds": action_timeout(config, host, agent, kind),
    }


def keyring_action(kind: str, config: Mapping[str, Any], host: Mapping[str, Any], subargs: Sequence[str], target_alias: str | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {
        "kind": kind,
        "host": str(host.get("id")),
        "argv": build_keyring_argv(host, subargs),
        "timeout_seconds": action_timeout(config, host, None, kind),
    }
    if target_alias is not None:
        action["target_alias"] = target_alias
    return action


def choose_alternate(status: HostQuotaStatus, config: Mapping[str, Any]) -> AccountQuota | None:
    recovery = float(policy_value(config, "recovery_threshold_percent", DEFAULT_RECOVERY_THRESHOLD))
    candidates = [
        account
        for alias, account in status.accounts.items()
        if alias != status.active_alias and is_alternate_eligible(account, config, threshold=recovery)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda a: (a.floor_percent is not None, a.floor_percent or -1, a.alias), reverse=True)[0]


def partial_failure_present(state: Mapping[str, Any] | None) -> bool:
    if not isinstance(state, Mapping):
        return False
    if state.get("partial_failure"):
        return True
    hosts = state.get("hosts") or {}
    if not isinstance(hosts, Mapping):
        return False
    return any(isinstance(host_state, Mapping) and host_state.get("mode") == "partial_failure" for host_state in hosts.values())


def within_switch_cooldown(host_state: Mapping[str, Any], config: Mapping[str, Any], now: str) -> bool:
    cooldown = int(policy_value(config, "switch_cooldown_seconds", DEFAULT_SWITCH_COOLDOWN_SECONDS))
    if cooldown <= 0:
        return False
    switched_at = parse_iso8601(str(host_state.get("switched_at"))) if host_state.get("switched_at") else None
    now_dt = parse_iso8601(now)
    if switched_at is None or now_dt is None:
        return False
    return (now_dt - switched_at).total_seconds() < cooldown


def append_agent_actions(
    actions: list[dict[str, Any]],
    config: Mapping[str, Any],
    agents: Sequence[Mapping[str, Any]],
    host: Mapping[str, Any],
    kind: str,
    command_key: str,
    active_alias: str | None,
    target_alias: str | None = None,
    *,
    optional: bool = False,
) -> None:
    for configured_agent in agents:
        if optional and not isinstance(configured_agent.get(command_key), list):
            continue
        actions.append(
            agent_action(
                kind,
                config,
                host,
                configured_agent,
                command_key,
                active_alias,
                target_alias,
            )
        )


def append_keyring_switch_actions(
    actions: list[dict[str, Any]], config: Mapping[str, Any], host: Mapping[str, Any], target_alias: str
) -> None:
    actions.append(keyring_action("keyring_auto_off", config, host, ["auto", "off"]))
    switch = keyring_action("keyring_switch", config, host, ["switch", target_alias], target_alias=target_alias)
    switch["verify_argv"] = build_status_argv(host)
    switch["verify_expected_active_alias"] = target_alias
    switch["verify_expected_auto_switch"] = False
    actions.append(switch)


def plan_fleet(config: Mapping[str, Any], statuses: Mapping[str, Any], state: Mapping[str, Any] | None = None, now: str | None = None) -> dict[str, Any]:
    validate_config(config)
    state = state or {}
    now = now or utc_now_iso()
    max_age = int(policy_value(config, "max_status_age_seconds", DEFAULT_MAX_STATUS_AGE_SECONDS))
    actions: list[dict[str, Any]] = []
    host_summaries: list[dict[str, Any]] = []
    planned_state = copy.deepcopy(state) if isinstance(state, dict) else {}
    planned_state.setdefault("hosts", {})

    if partial_failure_present(state):
        for host in config.get("hosts", []):
            host_id = str(host.get("id"))
            host_state = ((state.get("hosts") or {}).get(host_id) or {}) if isinstance(state, Mapping) else {}
            host_summaries.append(
                {
                    "host": host_id,
                    "active_alias": None,
                    "active_floor_percent": None,
                    "previous_mode": host_state.get("mode", "codex"),
                    "decision": "blocked_partial_failure",
                }
            )
        return {
            "mode": "plan",
            "planned_at": now,
            "hosts": host_summaries,
            "actions": [],
            "planned_state": planned_state,
        }

    for host in config.get("hosts", []):
        host_id = str(host.get("id"))
        raw_status = statuses.get(host_id)
        if raw_status is None:
            raise ValueError(f"missing keyring status for host {host_id}")
        status = raw_status if isinstance(raw_status, HostQuotaStatus) else parse_keyring_status(raw_status, now=now, max_age_seconds=max_age)
        active = status.accounts.get(status.active_alias or "")
        host_state = ((state.get("hosts") or {}).get(host_id) or {}) if isinstance(state, Mapping) else {}
        previous_mode = host_state.get("mode", "codex")
        agents = [agent for agent in (host.get("agents") or []) if agent.get("enabled", True)]

        summary = {
            "host": host_id,
            "active_alias": status.active_alias,
            "active_floor_percent": active.floor_percent if active else None,
            "previous_mode": previous_mode,
            "decision": "no_action",
        }

        block_reason = active_status_block_reason(status.active_alias, active, config)
        if block_reason is not None:
            summary["decision"] = "blocked_status"
            summary["status_reason"] = block_reason
            host_summaries.append(summary)
            continue

        if previous_mode == "fallback":
            if is_account_recovered(active, config):
                append_agent_actions(actions, config, agents, host, "agent_drain", "drain_command", status.active_alias)
                append_agent_actions(actions, config, agents, host, "agent_restore", "restore_command", status.active_alias)
                append_agent_actions(
                    actions,
                    config,
                    agents,
                    host,
                    "agent_refresh",
                    "refresh_command",
                    status.active_alias,
                    optional=True,
                )
                append_agent_actions(actions, config, agents, host, "agent_resume", "resume_command", status.active_alias)
                planned_state["hosts"][host_id] = {
                    "mode": "codex",
                    "active_alias": status.active_alias,
                    "restored_at": now,
                }
                summary["decision"] = "restore_codex"
            else:
                alternate = choose_alternate(status, config)
                if alternate is not None:
                    append_agent_actions(
                        actions,
                        config,
                        agents,
                        host,
                        "agent_drain",
                        "drain_command",
                        status.active_alias,
                        alternate.alias,
                    )
                    append_keyring_switch_actions(actions, config, host, alternate.alias)
                    append_agent_actions(actions, config, agents, host, "agent_restore", "restore_command", alternate.alias)
                    append_agent_actions(
                        actions,
                        config,
                        agents,
                        host,
                        "agent_refresh",
                        "refresh_command",
                        alternate.alias,
                        optional=True,
                    )
                    append_agent_actions(actions, config, agents, host, "agent_resume", "resume_command", alternate.alias)
                    planned_state["hosts"][host_id] = {
                        "mode": "codex",
                        "active_alias": alternate.alias,
                        "switched_at": now,
                        "restored_at": now,
                        "previous_alias": status.active_alias,
                    }
                    summary["decision"] = "switch_account_restore_codex"
                    summary["target_alias"] = alternate.alias
                else:
                    summary["decision"] = "wait_for_recovery"
            host_summaries.append(summary)
            continue

        if is_account_above_drain(active, config):
            planned_state["hosts"].setdefault(host_id, {"mode": "codex", "active_alias": status.active_alias})
            host_summaries.append(summary)
            continue

        if active and active.floor_percent != 0 and within_switch_cooldown(host_state, config, now):
            summary["decision"] = "wait_switch_cooldown"
            host_summaries.append(summary)
            continue

        alternate = choose_alternate(status, config)
        if alternate is not None:
            append_agent_actions(
                actions,
                config,
                agents,
                host,
                "agent_drain",
                "drain_command",
                status.active_alias,
                alternate.alias,
            )
            append_keyring_switch_actions(actions, config, host, alternate.alias)
            append_agent_actions(
                actions,
                config,
                agents,
                host,
                "agent_refresh",
                "refresh_command",
                alternate.alias,
                optional=True,
            )
            append_agent_actions(actions, config, agents, host, "agent_resume", "resume_command", alternate.alias)
            planned_state["hosts"][host_id] = {
                "mode": "codex",
                "active_alias": alternate.alias,
                "switched_at": now,
                "previous_alias": status.active_alias,
            }
            summary["decision"] = "switch_account"
            summary["target_alias"] = alternate.alias
        else:
            append_agent_actions(actions, config, agents, host, "agent_drain", "drain_command", status.active_alias)
            append_agent_actions(actions, config, agents, host, "agent_fallback", "fallback_command", status.active_alias)
            append_agent_actions(
                actions,
                config,
                agents,
                host,
                "agent_refresh",
                "refresh_command",
                status.active_alias,
                optional=True,
            )
            append_agent_actions(actions, config, agents, host, "agent_resume", "resume_command", status.active_alias)
            planned_state["hosts"][host_id] = {
                "mode": "fallback",
                "active_alias": status.active_alias,
                "fallback_started_at": now,
            }
            summary["decision"] = "fallback"

        host_summaries.append(summary)

    return {
        "mode": "plan",
        "planned_at": now,
        "hosts": host_summaries,
        "actions": actions,
        "planned_state": planned_state,
    }


def load_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        if default is not None:
            return copy.deepcopy(default)
        raise


def write_json(path: str | os.PathLike[str], data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def read_statuses(config: Mapping[str, Any], executor: Any | None = None) -> dict[str, Any]:
    validate_config(config)
    executor = executor or SubprocessExecutor()
    statuses: dict[str, Any] = {}
    for host in config.get("hosts", []):
        host_id = str(host.get("id"))
        result = executor.run(build_status_argv(host), timeout=int(host.get("status_timeout_seconds", 30)))
        if result.returncode != 0:
            raise RuntimeError(f"codex-keyring status failed for host {host_id} with exit {result.returncode}")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError(f"codex-keyring status returned a non-object payload for host {host_id}")
        statuses[host_id] = payload
    return statuses


def redact_argv(argv: Sequence[Any]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for raw in argv:
        arg = str(raw)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if arg.startswith("--") and "=" in arg:
            flag, _value = arg.split("=", 1)
            if SECRET_FLAG_RE.search(flag):
                redacted.append(f"{flag}=<redacted>")
                continue
        if arg.startswith("-") and SECRET_FLAG_RE.search(arg):
            redacted.append(arg)
            redact_next = True
            continue
        if "=" not in arg and SECRET_KEY_RE.fullmatch(arg.lstrip("-")):
            redacted.append(arg)
            redact_next = True
            continue
        if any(char.isspace() for char in arg) and SECRET_FLAG_RE.search(arg):
            try:
                nested = shlex.split(arg)
            except ValueError:
                redacted.append("<redacted-command>")
                continue
            redacted.append(shlex.join(redact_argv(nested)))
            continue
        sanitized = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", arg)
        if sanitized != arg:
            redacted.append(sanitized)
            continue
        if SECRET_VALUE_RE.search(arg):
            redacted.append("<redacted>")
            continue
        redacted.append(arg)
    return redacted


def public_action(action: Mapping[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in action.items() if key not in {"argv", "verify_argv"}}
    safe["argv"] = redact_argv(action.get("argv", []))
    if "verify_argv" in action:
        safe["verify_argv"] = redact_argv(action.get("verify_argv", []))
    return safe


def public_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in plan.items() if key not in {"actions"}}
    safe["actions"] = [public_action(action) for action in plan.get("actions", [])]
    return safe


def derive_lock_path(state_path: str | os.PathLike[str] | None, lock_path: str | os.PathLike[str] | None = None) -> Path | None:
    if lock_path is not None:
        return Path(lock_path)
    if state_path is None:
        return None
    return Path(f"{state_path}.lock")


def partial_failure_state(
    state: Mapping[str, Any] | None,
    attempted: Sequence[Mapping[str, Any]],
    failed_action_index: int,
    now: str,
) -> dict[str, Any]:
    persisted = copy.deepcopy(state) if isinstance(state, Mapping) else {}
    persisted.setdefault("hosts", {})
    completed = [action for action in attempted if action.get("status") == "ok"]
    failed = attempted[failed_action_index]
    partial = {
        "failed_at": now,
        "failed_action_index": failed_action_index,
        "failed_action_kind": failed.get("kind"),
        "failed_action_host": failed.get("host"),
        "completed_action_indexes": [action.get("index") for action in completed],
        "completed_action_kinds": [action.get("kind") for action in completed],
        "completed_actions": [
            {
                "index": action.get("index"),
                "kind": action.get("kind"),
                "host": action.get("host"),
                "agent": action.get("agent"),
                "target_alias": action.get("target_alias"),
            }
            for action in completed
        ],
    }
    persisted["partial_failure"] = partial
    failed_host = failed.get("host")
    if failed_host:
        host_state = persisted["hosts"].setdefault(str(failed_host), {})
        if isinstance(host_state, dict):
            host_state["mode"] = "partial_failure"
            host_state["partial_failure"] = partial
    return persisted


def apply_plan(
    plan: Mapping[str, Any],
    state: Mapping[str, Any] | None,
    executor: Any | None = None,
    receipt_path: str | os.PathLike[str] | None = None,
    state_path: str | os.PathLike[str] | None = None,
    lock_path: str | os.PathLike[str] | None = None,
    now: str | None = None,
    use_lock: bool = True,
) -> dict[str, Any]:
    executor = executor or SubprocessExecutor()
    now = now or utc_now_iso()
    attempted: list[dict[str, Any]] = []
    ok = True
    failed_action_index = None
    lock_target = derive_lock_path(state_path, lock_path) if use_lock else None
    lock_handle = None
    lock_acquired: bool | None = None
    receipt: dict[str, Any] | None = None

    if lock_target is not None:
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(lock_target, "a+")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
        except BlockingIOError:
            receipt = {
                "ok": False,
                "applied_at": now,
                "planned_at": plan.get("planned_at"),
                "attempted_actions": [],
                "failed_action_index": None,
                "failed_action": None,
                "completed_action_count": 0,
                "planned_action_count": len(plan.get("actions", [])),
                "lock_acquired": False,
                "error": "apply lock is already held",
            }
            if receipt_path is not None:
                write_json(receipt_path, receipt)
            lock_handle.close()
            return receipt

    try:
        for index, action in enumerate(plan.get("actions", [])):
            timeout = int(action.get("timeout_seconds") or DEFAULT_ACTION_TIMEOUT_SECONDS)
            verification_error = None
            verification_error_class = None
            verification_record = None
            action_error = None
            action_error_class = None
            action_argv = normalized_argv(action.get("argv", []))
            if action_argv is None:
                result = CommandResult(returncode=1)
                action_error_class = "ValueError"
                action_error = "action argv must be a non-empty argv array"
            else:
                try:
                    result = executor.run(action_argv, timeout=timeout)
                except EXPECTED_EXECUTOR_EXCEPTIONS as exc:
                    result, action_error_class, action_error = command_exception_result(exc, "action")
            if result.returncode == 0 and "verify_argv" in action:
                verify_argv = normalized_argv(action.get("verify_argv", []))
                if verify_argv is None:
                    verify_result = CommandResult(returncode=1)
                    verification_error_class = "ValueError"
                    verification_error = "verification argv must be a non-empty argv array"
                else:
                    try:
                        verify_result = executor.run(verify_argv, timeout=timeout)
                    except EXPECTED_EXECUTOR_EXCEPTIONS as exc:
                        verify_result, verification_error_class, verification_error = command_exception_result(exc, "verification")
                verification_record = {
                    "argv": redact_argv(verify_argv or []),
                    "returncode": verify_result.returncode,
                    "status": "ok" if verify_result.returncode == 0 else "failed",
                }
                if verification_error_class:
                    verification_record["error_class"] = verification_error_class
                if verify_result.returncode != 0:
                    verification_error = verification_error or "post-action status verification command failed"
                else:
                    try:
                        verification_payload = json.loads(verify_result.stdout)
                        if not keyring_state_matches(
                            verification_payload,
                            action.get("verify_expected_active_alias"),
                            action.get("verify_expected_auto_switch"),
                        ):
                            verification_error = "post-action status did not match the planned active alias and auto-switch state"
                    except (json.JSONDecodeError, TypeError):
                        verification_error = "post-action status returned invalid verification JSON"
                if verification_error:
                    verification_record["status"] = "failed"
                    verification_record["error"] = verification_error
                    result = CommandResult(returncode=1)
            if result.returncode == 0 and action.get("kind") == "keyring_verify":
                try:
                    verification_payload = json.loads(result.stdout)
                    if not keyring_state_matches(
                        verification_payload,
                        action.get("expected_active_alias"),
                        action.get("expected_auto_switch"),
                    ):
                        verification_error = "keyring status did not match the planned active alias and auto-switch state"
                except (json.JSONDecodeError, TypeError):
                    verification_error = "keyring status returned invalid verification JSON"
                if verification_error:
                    result = CommandResult(returncode=1)
            status = "ok" if result.returncode == 0 else "failed"
            action_record = {
                "index": index,
                "kind": action.get("kind"),
                "host": action.get("host"),
                "agent": action.get("agent"),
                "target_alias": action.get("target_alias"),
                "argv": redact_argv(action_argv or []),
                "returncode": result.returncode,
                "status": status,
                "timeout_seconds": timeout,
            }
            if action_error_class:
                action_record["error_class"] = action_error_class
            if action_error:
                action_record["error"] = action_error
            if verification_error:
                if verification_error_class:
                    action_record["error_class"] = verification_error_class
                action_record["error"] = verification_error
            if verification_record is not None:
                action_record["verification"] = verification_record
            attempted.append(action_record)
            if result.returncode != 0:
                ok = False
                failed_action_index = index
                break

        if ok and state_path is not None:
            write_json(state_path, plan.get("planned_state", state or {}))
        if not ok and failed_action_index is not None and state_path is not None:
            write_json(state_path, partial_failure_state(state, attempted, failed_action_index, now))

        receipt = {
            "ok": ok,
            "applied_at": now,
            "planned_at": plan.get("planned_at"),
            "attempted_actions": attempted,
            "failed_action_index": failed_action_index,
            "failed_action": attempted[failed_action_index] if failed_action_index is not None else None,
            "completed_action_count": sum(1 for action in attempted if action.get("status") == "ok"),
            "planned_action_count": len(plan.get("actions", [])),
        }
        if lock_acquired is not None:
            receipt["lock_acquired"] = lock_acquired
        if receipt_path is not None:
            write_json(receipt_path, receipt)
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    if receipt is None:
        raise RuntimeError("apply did not produce a receipt")
    return receipt
