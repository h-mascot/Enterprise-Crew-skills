# openclaw-beta-testing

Create and run evidence-backed OpenClaw beta canaries, release-gate checks, and issue-intelligence loops.

This workflow is built for operators who want to test one non-primary agent first, collect useful proof, file tighter upstream issues, and avoid expanding a bad beta across a fleet.

## Structure

```text
openclaw-beta-testing/
├── README.md
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   └── github-issue-template.md
└── state/
    └── issues.example.json
```

## What It Covers

- Mission Control or task-board intake before touching a host
- Phase 1 canary testing on one non-primary agent
- Phase 2 deep testing only after the canary is clean or more evidence is needed
- Update, gateway, channel, cron, plugin, session, and model-route checks
- GitHub issue drafting with expected/actual, environment, and redacted evidence
- Issue follow-up until the result creates a retest, a public comment, or a lesson

## Usage

Copy this folder into your OpenClaw-compatible `skills/` directory:

```bash
git clone https://github.com/h-mascot/Enterprise-Crew-skills.git /tmp/enterprise-crew-skills
mkdir -p skills
cp -R /tmp/enterprise-crew-skills/openclaw-beta-testing skills/openclaw-beta-testing
```

Then ask your agent to use `openclaw-beta-testing` for a beta canary or release-candidate validation run.

## Issue Template

This bundle includes `references/github-issue-template.md`, the public issue-writing structure used by the workflow. For OpenClaw itself, also check the current upstream bug report template:

<https://github.com/openclaw/openclaw/blob/main/.github/ISSUE_TEMPLATE/bug_report.yml>

## Requirements

- OpenClaw CLI and gateway access on the target canary host
- A non-primary canary agent or host
- A durable task board or issue tracker for evidence and checkbacks
- GitHub issue access if failures will be submitted upstream

## License

MIT
