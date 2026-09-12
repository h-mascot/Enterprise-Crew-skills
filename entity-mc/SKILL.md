---
name: entity-mc
description: Bootstrap Entity Mission Control helper runtime for crew agents with a shared canonical bundle, portable MC operating memory, structured intake, per-agent manifest, safe cron install, verification, and rollback.
---

# Entity MC

Use this skill when an agent needs the standard Entity Mission Control helper bundle without manually copying shell scripts around.

This skill packages the current MC helper runtime into one installable bundle:
- `mc.sh`
- `mc-auto-pull.sh`
- `mc-assign-model.sh`
- `mc-build-context.sh`
- `mc-stall-check.sh`
- `mc-intake.sh`
- `memory/entity-mc/*.md` — portable MC operating memory installed into the workspace memory directory
- `.entity-mc/context/*.md` — same context files also linked in the state directory

It also handles:
- per-agent manifests
- idempotent install/update
- safe cron registration
- optional structured intake from JSON/JSONL into MC tasks
- portable MC operating context installed into both `memory/entity-mc/` and `.entity-mc/context/`
- AGENTS.md patched with startup read instruction for `memory/entity-mc/`
- post-install verification
- rollback to the previous runtime

## Files

- Installer: `skills/entity-mc/install.sh`
- Verifier: `skills/entity-mc/verify.sh`
- Rollback: `skills/entity-mc/rollback.sh`
- Shared helpers: `skills/entity-mc/lib.sh`
- Manifests: `skills/entity-mc/manifests/*.env`
- Runtime version: `skills/entity-mc/VERSION`
- Onboarding flow doc: `skills/entity-mc/docs/onboarding-flow.md`

## Manifest contract

Each manifest is a simple env file.

Required:
- `ENTITY_MC_AGENT_NAME`
- `ENTITY_MC_TARGET_HOME`

Optional:
- `ENTITY_MC_TARGET_SCRIPTS_DIR`
- `ENTITY_MC_STATE_DIR`
- `ENTITY_MC_MODE` (`copy` or `symlink`, default `copy`)
- `ENTITY_MC_ENABLE_AUTO_PULL` (`true|false`)
- `ENTITY_MC_ENABLE_REVIEW_PULL` (`true|false`, default `false`; enable after configuring an independent reviewer)
- `ENTITY_MC_ENABLE_STALL_CHECK` (`true|false`)
- `ENTITY_MC_ENABLE_INTAKE` (`true|false`, default `false`; enable only after writing a source-specific intake policy)
- `ENTITY_MC_INTAKE_SCHEDULE`
- `ENTITY_MC_CONTEXT_DIR` (derived from `ENTITY_MC_STATE_DIR`, installed automatically)
- `ENTITY_MC_AUTO_PULL_SCHEDULE`
- `ENTITY_MC_STALL_CHECK_SCHEDULE`
- `ENTITY_MC_PROFILE_NAME`
- `ENTITY_MC_EXTRA_NOTES`
- `ENTITY_MC_DISPATCH_HOST` (required for dispatch, exact output of `hostname`; `install-auto.sh` records it)
- `ENTITY_MC_DOCS_SOURCE_ID` (Entity file source for evidence links; default `workspace`)
- `ENTITY_MC_REVIEW_EXEC_LOG` (review worker log override; otherwise uses the shared execution log)
- `ENTITY_MC_DEFAULT_REVIEWER` (peer reviewer identity for new submissions)
- `ENTITY_MC_HUMAN_REVIEWERS` (comma-separated human identities authorized to decide human-gated reviews)

## Install

Preferred one-command install from inside the target workspace:

```bash
bash skills/entity-mc/install-auto.sh
```

This records the current executor search path and resolved binaries in an auto manifest for the current workspace, installs runtime wrappers, writes the Entity MC cron block, installs portable MC/intake setup context into `.entity-mc/context/`, and runs verification.

Automatic cron enablement requires the selected executor to be installed. Use `--install-cron false` to prepare a workspace before its executor is available.

Manual manifest install remains available when you need explicit per-host settings:

```bash
bash skills/entity-mc/install.sh --manifest skills/entity-mc/manifests/scotty.env
```

Optional flags:

```bash
bash skills/entity-mc/install-auto.sh \
  --workspace /path/to/openclaw-workspace \
  --agent Scotty \
  --install-cron true
```

```bash
bash skills/entity-mc/install.sh \
  --manifest skills/entity-mc/manifests/book.env \
  --mode copy \
  --install-cron true
```

## Verify

```bash
bash skills/entity-mc/verify.sh --manifest skills/entity-mc/manifests/scotty.env
```

## Rollback

```bash
bash skills/entity-mc/rollback.sh --manifest skills/entity-mc/manifests/scotty.env
```

## Operational rules

1. Prefer this skill over manual script-copying.
2. Keep shared behavior in the canonical bundle under this skill.
3. Keep agent-specific differences in the manifest, not in forks of the scripts.
4. Re-running install must be safe.
5. Cron entries are managed only inside the Entity MC marker block.
6. Roll out to one agent first, verify, then expand.

## Recommended rollout order

1. Scotty
2. Spock
3. Book

## Definition of done

An install is only done when:
- runtime files are present
- wrappers or symlinks exist in target scripts dir
- version file is written
- cron block is present exactly once by default
- portable context files are installed in both `.entity-mc/context/` AND `memory/entity-mc/`
- AGENTS.md contains the ENTITY_MC_MEMORY_START marker block
- `mc.sh review` exists in the installed helper and `mc-intake.sh` can dry-run structured task creation
- `verify.sh` passes

## Auto task creation / intake

Entity MC auto-pull executes tasks that already exist. Automatic task creation is handled by `mc-intake.sh`, bundled with the runtime.

`mc-intake.sh` is deliberately source-agnostic and conservative: it accepts explicit structured JSON/JSONL candidates, dedupes against active tasks and its local seen log, and creates MC tasks. Source-specific watchers should call it rather than embedding task-creation logic.

Examples:

```bash
# Create one task
bash scripts/mc-intake.sh create \
  --title "Investigate failed deploy" \
  --description "Deploy log URL: ..." \
  --assignee Scotty \
  --source discord \
  --source-id "channel/message" \
  --url "https://discord.com/channels/..."

# Ingest structured candidate from another watcher
echo '{"title":"Fix docs link","description":"Broken in thread...","assignee":"Ada","source":"discord","source_id":"123/456"}' \
  | bash scripts/mc-intake.sh ingest --json

# Dry-run inbox JSONL processing
bash scripts/mc-intake.sh scan-file .entity-mc/intake/inbox.jsonl --dry-run
```

Optional cron support is controlled by `ENTITY_MC_ENABLE_INTAKE=true`; by default it is off because each installed workspace needs an explicit source watcher/inbox policy. The bundle installs `mc-intake-setup.md` into `.entity-mc/context/` so onboarding agents know how to write that local policy before enabling intake.

## Portable operating memory

This bundle installs portable MC context into `.entity-mc/context/` and `mc-build-context.sh` injects it into every pulled task. This is the small memory pack that makes a newly onboarded agent use MC properly without needing Ada's private workspace memory.

Included context:

- `mc-operating-rules.md` — when to use MC, lifecycle, evidence, duplicate avoidance.
- `entity-mc-context.md` — what the runtime installs, manifest contract, structured intake behavior.
- `mc-task-intake-policy.md` — what work should become a board task, routing defaults, columns, and automatic intake rules.
- `mc-intake-setup.md` — how to define source-specific intake policy, candidate JSON/JSONL shape, dedupe keys, and safe enablement.
- `task-closure-contract.md` — exact review/blocker note requirements.

Keep these files public-safe. Do not add private hostnames, tokens, personal data, or Henry-specific secrets. Put host-specific facts in manifests or local memory, not in the public bundle.

## Dispatch ownership and recovery

Set `ENTITY_MC_DISPATCH_HOST` to the verified output of `hostname`. Each agent must have one authoritative dispatch host, login home, canonical MC URL, `ENTITY_MC_STATE_DIR`, and managed cron block. The host-wide lock prevents overlapping dispatch calls; durable reservations belong to that one state directory. Running another installation of the same agent with a different state directory is unsupported. Hold the old scheduler and reconcile its reservations before changing any owner configuration because the task update API does not provide a distributed compare-and-swap claim.

Set the Python executable, runtime binary, execution path, and profile directories explicitly in the manifest. Failures before a worker starts use a bounded retry budget with backoff. Once a worker has started, an uncertain exit requires reconciliation because it may already have performed actions. Inspect the attempt record, task output, process identity, and external outcome before retrying. A substantive request-fix receipt can begin a fresh bounded producer cycle.

During migration, reconcile legacy trackers. Remove a tracker only when the task records its outcome; unresolved or malformed trackers hold dispatch for review. Configure human reviewer identities with `ENTITY_MC_HUMAN_REVIEWERS`; automation always preserves tasks marked `human_gate_required`, `requires_human`, or `review_type: human`.

Health requires fresh outcome telemetry for every enabled belt. `ENTITY_MC_HEALTH_INVENTORY` points to JSON containing an `agents` array with `name`, `host` (or `local`), `state_dir`, `cron_log`, and `enabled_belts`. Maintenance holds use `held: true` and an optional `held_reason`. A fresh cron log alone does not establish health. Set `ENTITY_MC_HEALTH_NO_NOTIFY=1` for local verification.

`mc.sh review <id> "output" [--risk low|medium|high] [--reviewer NAME]` records a new review submission. The assigned independent reviewer uses `accept-review` or `request-fix` with a substantive note. Existing human gates survive resubmission, and `deliver` cannot bypass them. `mc.sh block <id> "blocker, recovery attempted, and required next action"` records blocked work without moving it back to todo.

Python 3.9+, Bash, jq, curl, and the selected executor must be available. Set `ENTITY_MC_PYTHON_BIN`, `ENTITY_MC_EXEC_PATH`, and `ENTITY_MC_OPENCLAW_BIN` or `ENTITY_MC_HERMES_BIN` when cron cannot discover them. A Codex-compatible adapter must explicitly implement `--preflight` and the launch contract; unsupported executors fail before task mutation. `ENTITY_MC_MODEL_CONFIG` optionally supplies `default_model`, `aliases`, `inventory` keyed by lowercase agent, and `fallbacks`; `ENTITY_MC_DEFAULT_MODEL` provides a simple default.

Run the offline fixture suite from the repository root with `python3 -m pytest -q entity-mc/tests`. It uses temporary workspaces, mock crontabs, fake runtimes, and loopback HTTP fixtures.


### Decide the inspected review submission

Read the task and its `metadata.review_submitted_at`, inspect that submission's artifacts, then pass the same generation when recording a decision:

```bash
mc.sh accept-review <id> "What you verified" --submission <review_submitted_at>
mc.sh request-fix <id> "Specific defect and required correction" --submission <review_submitted_at>
```

The review dispatcher supplies this generation in both its command examples and `ENTITY_MC_REVIEW_SUBMISSION`. A versioned submission requires the flag or inherited value; a changed generation stops the decision before mutation. Legacy submissions with no generation use `--submission ''`. Serialize submission changes with review decisions because the task API does not provide conditional writes.

Review submissions record the actual submitting actor, preserve existing human approval gates, and keep producer and reviewer independent. Direct delivery cannot bypass a human gate.

Submit verified proof when entering review with `mc.sh review <id> "output and verification" --reviewer <independent-reviewer> --proof <artifact-ref>`. This sends the explicit artifact reference in `metadata.proof_ref` for the product's review-entry policy. Without `--proof`, existing proof fields are preserved; the CLI does not invent proof. Inspect the referenced artifact before submitting it.
