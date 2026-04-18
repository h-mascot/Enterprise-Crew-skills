---
name: release-manager
description: Turn shipped git activity into release candidates with suggested versions, changelog drafts, GitHub release notes, and launch-copy drafts. Use when a project has active commits but no clean public release boundary yet, when you want to decide whether recent work is release-worthy, or when managing recurring release packaging across multiple repos.
---

# Release Manager

Turn recent git history into a coherent release candidate.

## What it does

Release Manager separates three concerns:
- git/GitHub provides facts
- Release Manager provides judgment
- a project registry provides workflow state

The workflow:
1. Read the repo since the last release boundary
2. Classify recent commits into rough buckets
3. Score whether the batch is release-worthy
4. Suggest the next version
5. Write draft artifacts for review

## Use this when
- a repo has shipped meaningful changes and needs a release boundary
- work is being pushed regularly but releases are inconsistent
- you want draft changelogs and release notes generated from git history
- you are managing multiple active repos and need one repeatable release flow

## Core rule
A release happens when recent work forms a coherent, externally legible unit of progress.

Not every commit deserves a release.
Not every release needs dozens of commits.
The story matters more than raw count.

## Registry shape
Keep a registry with one entry per project. Each entry should include:
- project id
- project name
- repo path
- repo URL
- visibility
- whether the project is active
- release cadence
- last tag / last version if known
- optional threshold overrides
- notes about source-of-truth location

## Outputs
For each project scan, write:
- `latest.json`
- `summary.md`
- `CHANGELOG.draft.md`
- `GITHUB_RELEASE.draft.md`
- `TWEET.draft.md`

Use deterministic paths so cron jobs and follow-up agents can find them reliably.

## Versioning guidance
Use lightweight semver:
- `v0.1.0` for first usable public release
- patch bumps for small fixes and polish
- minor bumps for meaningful new capability
- major bumps only for breaking changes or major repositioning

Most early-stage projects should live in `v0.x.x` for a while.

## Judgment rules
Treat a batch as release-worthy when at least one is true:
- one meaningful user-facing feature shipped
- two to four related improvements tell one story
- a fix materially changes usability or reliability
- enough time has passed and there is clear shipped progress

Do not release just because commit count looks big.

## Public communication rule
Each good release should eventually become:
- a GitHub release
- a changelog entry
- a short post or tweet
- optionally a demo or screenshot

The release is the packaging layer between raw work and public distribution.
