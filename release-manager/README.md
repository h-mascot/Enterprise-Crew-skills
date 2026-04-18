# Release Manager

Turn shipped git activity into release candidates with suggested versions, changelog drafts, GitHub release notes, and launch-copy drafts.

## What problem it solves

Fast-moving repos often have the opposite of a shipping problem. Work lands constantly, but public releases happen inconsistently, so progress disappears into commit history.

Release Manager fixes that by turning recent git history into a draft release package.

## What it generates
- suggested version
- release-worthiness decision
- changelog draft
- GitHub release draft
- tweet or launch-copy draft
- deterministic output folder for review

## Best fit
Use this for:
- open source projects shipping in small batches
- internal tools that still need disciplined versioning
- multi-repo teams that want one release workflow across several products

## Core idea
- git/GitHub = source of facts
- Release Manager = source of judgment
- registry = source of workflow state

A release should happen when the recent work forms a coherent, externally legible unit of progress.

## Recommended setup
Track each project in a registry with:
- project id and name
- repo path
- repo URL
- visibility
- cadence
- last tag / last version
- optional threshold overrides

Then have a script or cron scan each project and write draft artifacts into stable output paths.

## Output artifacts
For each candidate release:
- `summary.md`
- `CHANGELOG.draft.md`
- `GITHUB_RELEASE.draft.md`
- `TWEET.draft.md`
- `latest.json`

## Versioning guidance
Use lightweight semver:
- `v0.1.0` first usable public version
- patch for small fixes
- minor for meaningful feature additions
- major for breaking changes

## Notes
This public skill documents the release operating model. The exact implementation can vary by team, repo layout, and automation stack.
