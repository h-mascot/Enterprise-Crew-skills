# Codex fleet account drain and agent fallback

Date: 2026-08-07
Branch: `feature/codex-fleet-drain`
Owner: Book supervising Geordi/Codex

## Goal

Extend `model-orchestrator` from cron-only model balancing into a safe, account-aware fleet controller. When a Codex account reaches a configurable reserve, stop assigning new work to that account across every configured agent, including both real Geordi host surfaces. Prefer another healthy Codex account; otherwise route configured agents to their non-Codex fallback. Support arbitrary additional agents through data, not hardcoded names.

## Safety contract

- Planning is the default. No command runs without explicit `apply` mode.
- Never terminate an in-flight Codex worker.
- Switch host-local `auth.json` only through `codex-keyring`; never copy tokens between hosts.
- Disable keyring auto-switch before a coordinated manual switch so it cannot immediately revert.
- Treat the five-hour and weekly windows independently; the lower known remaining percentage is the account floor.
- Drain at `<= 20%`; recover at `>= 35%` by default. Persist state for hysteresis.
- Missing, unknown, stale, or inexact active quota blocks mutation by default; unknown/stale aliases are never eligible alternates.
- Quota freshness is explicit. Alias-level quota observation timestamps are required; generic `updatedAt` and switch timestamps are not quota observations.
- Alternates require fresh known quota, `confidence=exact`, `manualOnly=false`, and a policy-allowed health value.
- Agent admission/drain, fallback, and restore commands are declarative argv arrays. No implicit broad process kills.
- Agent transports may differ from their quota/keyring host transport.
- Apply is bounded by action timeouts and an exclusive non-blocking file lock.
- Partial mutation persists `partial_failure` state; future plans block until operator reconciliation.
- Audit actions without persisting credentials or raw auth payloads.
- Geordi is represented by its real Enterprise and MascotM3 Codex surfaces, not as a standalone gateway persona.

## Deliverables

- [x] Pure Python policy/controller using only the standard library.
- [x] Generic JSON config schema/example for hosts and arbitrary agents.
- [x] Local and SSH host adapters for `codex-keyring status --json`, `auto off`, and `switch`.
- [x] Deterministic action plan for switch, fallback, recovery, and no-op paths.
- [x] Explicit apply mode with ordered, fail-closed execution and audit/state receipts.
- [x] Direct `scripts/fleet.py` entrypoints for plan/apply/status; no dependency on the legacy cron orchestrator.
- [x] Unit tests written before implementation for thresholds, hysteresis, alternate-account choice, multi-agent coverage, SSH command quoting, dry-run, and fail-closed apply.
- [x] Parent-review hardening tests written before correction for actual keyring list shape, explicit freshness, metadata, cross-host agents, admission ordering, partial failure, bounded execution, locking, SSH redaction, config validation, and switch cooldown.
- [x] Final parent-review regression tests written before correction for CLI/read-status freshness, deployable sensor behavior, transaction-wide locking, fallback alternate recovery, first-action partial failure, invalid quota percentages, schema validation, receipt naming, and example topology.
- [x] Optional `status_command` argv contract plus shipped standard-library `scripts/codex-keyring-status.py` sensor for safe per-alias quota metadata enrichment.
- [x] Documentation covering Book, Ada, `geordi-enterprise`, Spock, Scotty, Zora, Midas, EntityBuilder opt-in, and MascotM3 without committing private host details or credentials.
- [x] Remove credential-bearing defaults from touched legacy shell scripts; regression scanning covers tracked shell sources.

## Verification

- [x] Failing tests captured before implementation at `/tmp/model-orchestrator-hardening-red.log`.
- [x] Final correction failing tests captured before implementation at `/tmp/model-orchestrator-final-red.log`.
- [x] `python3 -m unittest discover -s model-orchestrator/tests -v`
- [x] Python fleet CLI plus `fleet-*` shell dispatch; all touched shell entrypoints pass `bash -n` and secret-default regression scanning.
- [x] `python3 -m py_compile model-orchestrator/fleet_controller.py model-orchestrator/scripts/fleet.py model-orchestrator/scripts/codex-keyring-status.py`
- [x] JSON validation plus `fleet_controller.validate_config()` over `model-orchestrator/config/fleet.example.json`
- [x] CLI and sensor fixture smokes: plan low-account switch, plan no-alternate fallback, lock contention, receipt collision resistance, apply through harmless fixture commands, recovery plan, and safe sensor enrichment.
- [x] Secret/private-default scan across the branch diff.
- [x] Generated `__pycache__` and `output` directories removed.
- [x] Codex autoreview over the final implementation; actionable controller and touched-shell findings fixed.
- [x] Final tests rerun before handoff.
- [x] Feature branch pushed and PR opened: https://github.com/h-mascot/Enterprise-Crew-skills/pull/4.

## Live rollout boundary

This branch ships the controller and examples. Live fleet mutation is a separate gated step. Before any live apply:

1. Install/register `codex-keyring` independently on each real Codex host.
2. Add private host and per-agent route commands outside the public repo.
3. Run `python3 scripts/fleet.py plan --config /private/path/fleet.json` and inspect the receipt.
4. Prove current processes and active jobs; no process kill is permitted.
5. Apply to one canary host, verify account identity and next-request routing, then expand.

## Progress log

- 2026-08-07: Public orchestrator confirmed cron-only before this feature branch.
- 2026-08-07: Live rollout evidence and account telemetry are intentionally excluded from this public plan; rollout must use private operator receipts outside the repo.
- 2026-08-07: Added the standard-library fleet controller, direct CLI, placeholder fleet config, TDD tests, and docs. Kept the legacy cron orchestrator out of the patch so provider credentials and unrelated baseline defects are not surfaced.
- 2026-08-07: Verification passed with 53 unit tests, deterministic fixture smokes, Python compilation, JSON example validation, and an added/deleted-line private-secret scan. Commit/push/PR intentionally left for Book.
- 2026-08-07: Parent-review correction reproduced the actual `codex-keyring status --json` list shape without quota timestamps as a red test, then hardened status freshness, active-block decisions, alternate eligibility, per-agent transports, fallback/recovery ordering, partial-failure state, timeouts, file locking, nested SSH redaction, config validation, switch cooldown, and the example status sensor contract. Red receipt: `/tmp/model-orchestrator-hardening-red.log`.
- 2026-08-07: Final correction red run captured at `/tmp/model-orchestrator-final-red.log`, then fixed CLI/read-status freshness, added the deployable safe status sensor, widened apply locking to cover state/status/plan/execution, added fallback alternate recovery, persisted first-action partial failures, rejected out-of-range quota percentages, corrected the example topology, added collision-resistant receipt names, validated `schema_version`, reran all gates, and removed generated cache/output.
- 2026-08-07: Hardening added regressions for non-finite quota/policy numbers, alias-only freshness, future observations, no-enabled-agent topology, MascotM3 fallback actions, CLI lock-proof receipt persistence, string boolean parsing, and `{skill_dir}` status-command expansion from a different cwd.
- 2026-08-07: Final verification normalization rejects missing or ambiguous keyring state fields instead of inferring success. Focused regression and full 53-test suite pass.

## Resume instructions

Read this file, inspect `git status --short`, run the focused unit suite, and continue from the first unchecked deliverable. Do not touch a sibling canonical checkout; it may contain unrelated uncommitted work. Work only in the checkout containing this plan.
