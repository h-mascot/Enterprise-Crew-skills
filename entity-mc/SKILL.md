---
name: entity-mc
description: Bootstrap Entity Mission Control helper runtime for crew agents with a shared canonical bundle, per-agent manifest, safe cron install, verification, and rollback.
---

# Entity MC

Use this skill when an agent needs the standard Entity Mission Control helper bundle without manually copying shell scripts around.

This skill packages the current MC helper runtime into one installable bundle:
- `mc.sh`
- `mc-auto-pull.sh`
- `mc-assign-model.sh`
- `mc-build-context.sh`
- `mc-stall-check.sh`

It also handles:
- per-agent manifests
- idempotent install/update
- safe cron registration
- post-install verification
- rollback to the previous runtime

## Files

- Installer: `skills/entity-mc/install.sh`
- Verifier: `skills/entity-mc/verify.sh`
- Rollback: `skills/entity-mc/rollback.sh`
- Shared helpers: `skills/entity-mc/lib.sh`
- Manifests: `skills/entity-mc/manifests/*.env`
- Runtime version: `skills/entity-mc/VERSION`

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
- `ENTITY_MC_ENABLE_STALL_CHECK` (`true|false`)
- `ENTITY_MC_AUTO_PULL_SCHEDULE`
- `ENTITY_MC_STALL_CHECK_SCHEDULE`
- `ENTITY_MC_PROFILE_NAME`
- `ENTITY_MC_EXTRA_NOTES`

## Install

```bash
bash skills/entity-mc/install.sh --manifest skills/entity-mc/manifests/scotty.env
```

Optional flags:

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
- cron block is present exactly once when enabled
- `verify.sh` passes
