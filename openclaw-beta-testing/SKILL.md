---
name: openclaw-beta-testing
description: "Create and run evidence-backed OpenClaw beta test plans and issue intelligence loops. Use when an operator asks for beta testing, release candidate validation, baseline beta health checks, canary stress packets, beta issue submission/follow-up, automated vs manual beta checklists, contributor test asks, or OpenClaw release gate structure."
metadata:
  openclaw:
    emoji: "🧪"
    always: false
---

# OpenClaw Beta Testing Intelligence

Use this skill when an operator asks to test an OpenClaw beta, route contributor feedback into tests, decide what agents can automate vs a human must manually check, run baseline beta validation, stress-test beta behavior on a constrained canary, or follow up on beta issues already submitted.

## Purpose

Contributor channels are release comms, beta feedback, and contributor testing surfaces. Do not treat them only as weekly summary channels.

The goal is to convert community feedback into reproducible test work, submit useful issues when failures are found, and track those issues until their outcome teaches us what to update, retest, or improve in future reports.

## V2 Principle

Do not test a beta as a vague "install it and click around" task. Build the test plan from two evidence streams:

1. **Community asks:** what contributor channels, release channels, GitHub issues/PRs, and linked docs are asking people to verify.
2. **Ops reality:** what broke across our agents in the last week: model routing, channel delivery, cron routing, sync loops, config migration, missing browser/runtime dependencies, install path mistakes, and real-route vs catalog mismatch.

The output should be a small, executable test packet for the right canary host, plus issue drafts for failures, plus issue-intelligence tracking for anything we submit.

## Default Request Flow

When an operator says any version of "beta test OpenClaw version X", "test latest beta on this agent", "OC beta test", or asks whether a target agent should join beta testing, use this flow by default.

### Phase 0: Intake + Mission Control

Create Mission Control state before real work starts.

1. Create or claim an MC task named like `OpenClaw beta canary: <version/channel> on <agent>`.
2. Add task notes with:
   - target agent/host and why it was selected
   - requested version, tag, branch, or `latest beta`
   - current version before update
   - install method and rollback path
   - risk level: low / medium / high
   - intended mode: `phase-1-canary` or `phase-2-deep-run`
3. Write a dated plan in `docs/plans/` and update `docs/plans/ACTIVE_PLAN.md`.
4. Pull release evidence from contributor channels, release channels, and GitHub release/PR links for the target version.

Do not ask the operator for these fields if they can be inferred from agent routing, release context, or local state. Mark unknowns in the MC task instead.

### Phase 1: Canary Run

Phase 1 is the default first run. It is intentionally smaller than the deep matrix and should produce a decision quickly.

Run Phase 1 on one non-primary target agent unless the operator explicitly asks for a broader rollout.

Default beta ladder:

1. **Spock baseline first** for clean Linux beta-health validation. Use Spock to decide whether the beta is generally healthy.
2. **Constrained-host stress second** for Raspberry Pi / arm64 / low-resource / cron-channel-gateway endurance testing. Do not use a constrained host alone to judge broad release health.
3. **Zora third** for Mac/client-specific behavior.
4. **Ada last or never** unless the beta specifically needs main-gateway coverage.

If the operator explicitly names a target, use that target. If the operator only says "beta test latest", default to a baseline canary first, then a constrained host only after baseline passes or when stress behavior is the point.

Minimum canary steps:

1. Snapshot current state:
   - `openclaw --version`
   - `openclaw status --all` or JSON equivalent
   - `openclaw doctor`
   - active service/gateway PID or restart marker
   - relevant channel, cron, plugin, model, and session state
2. Update:
   - existing install: `openclaw update --channel beta` unless testing a specific tag/branch
   - new/recovery install: use the release instructions for the requested platform
3. Verify upgrade:
   - version matches target
   - gateway/service restarted cleanly
   - `status` and `doctor` are sane
   - no obvious restart loop, stuck update sentinel, or missing plugin dependency
4. Run P0 smoke:
   - Discord visible reply or the target channel's equivalent visible reply path
   - one model/runtime call through the expected route
   - one tool-using or longer Codex/runtime turn if the release touches runtime behavior
   - one forced cron run with delivery/result evidence if cron behavior is in scope
   - `plugins list` / plugin repair path if plugin/update behavior is in scope
   - session list/cleanup command path if CLI/session behavior is in scope
5. Observe:
   - watch logs briefly for stalls, event-loop warnings, missed replies, gateway restarts, or update repair loops
   - record timestamps and exact command output

Phase 1 outputs one of:

- `pass-expand`: no P0/P1 blockers, safe to run Phase 2 or add one more agent
- `mixed-hold`: usable with caveats, keep canary only and draft issues/docs asks
- `fail-report`: blocker found, keep/rollback target as needed and submit or draft a regression report
- `fail-rollback`: blocker found, rollback target if needed and submit or draft a regression report
- `blocked`: missing access, destructive approval needed, or target unavailable

### Phase 1 Self-Healing + Checkback

Every Phase 1 canary must have a checkback mechanism. Do not rely on memory to remember to inspect it later.

For short canaries, write a checkpoint file and create an MC follow-up note with the exact checkback time. For any observation window longer than 10 minutes, use the `self-healing` skill pattern:

- write `/tmp/self-heal-oc-beta-<agent>-<version>.json` with phase, target, version, models/agents tried, last command, and evidence path
- schedule a watchdog/checkback every 5-10 minutes, or a one-shot checkback if the observation window is fixed and short
- the watchdog must verify proof, not only liveness
- the watchdog must remove/disable itself when proof is verified or when rollback is complete
- if the canary runner dies, resume from the checkpoint and do not repeat completed phases
- if the target agent is unavailable, switch executor or mark the MC task blocked with reason

The watchdog/checkback should inspect:

- is the target still on the intended version?
- is the gateway/service healthy?
- are logs free of repeated crashes, event-loop warnings, missed visible replies, or update loops?
- did the report artifact and MC evidence get written?
- is rollback needed?

### Phase 2: Deep Beta Run

Phase 2 is the existing full beta-test matrix in this skill. Run it when:

- Phase 1 returns `pass-expand`
- the operator explicitly asks for a deep run
- the release is close to stable and needs release-gate confidence
- the change touches multiple high-risk lanes
- community evidence shows repeated or severe regressions

Phase 2 can cover multiple agents, longer observation windows, more channels, UI/browser checks, provider matrices, plugin/MCP validation, fleet/control-plane checks, and issue drafts. Use the lane list below for Phase 2 scope.

### Phase 3: Issue Submission

When a beta test finds a reportable issue, the job is not done at "hold beta". We are beta testing to report issues.

Before submitting:

1. Search GitHub for likely duplicates that match the current OpenClaw version, current beta channel, or the same release window first.
2. Check the repo's current issue template.
3. Fill the issue with observed evidence only.
4. Use `NOT_ENOUGH_INFO` where the template asks for facts we do not have.
5. Include exact version, environment, install method, repro, expected/actual, redacted logs, impact, and known related issues/PRs.
6. Do not include secrets, tokens, private URLs, or unredacted personal data.

After submitting:

1. Record the issue in `skills/openclaw-beta-testing/state/issues.json`.
2. Add the GitHub issue URL to the Mission Control task and report artifact.
3. Capture any immediate bot labels/comments, especially ClawSweeper verdicts.
4. If the issue is accepted/kept open, decide whether to keep the canary host available for repro.
5. If the issue is rejected/closed/duplicated, capture the reason and update this skill's reporting guidance if our report quality needs improving.

### Phase 4: Issue Intelligence

Every submitted beta issue must be tracked until it is resolved or intentionally abandoned.

For GitHub issue research, prefer issues and PRs from the current OpenClaw version, current beta/stable release window, and still-open or recently-closed issues. Old closed issues are background context only. Do not present old closed issues as likely matches unless the exact regression signature, version range, and maintainer signal show the behavior is still relevant.

The registry lives at:

`skills/openclaw-beta-testing/state/issues.json`

Each issue entry should include:

- `repo`, `number`, `url`, `title`
- submitted timestamp and submitter
- source beta canary/test, MC task, target agent, version, report path
- current state, labels, comment count, last checked timestamp
- ClawSweeper verdict and requested follow-up
- maintainer outcome when available
- learning fields:
  - `initial_lesson`: what the first review taught us
  - `outcome_lesson`: what acceptance/rejection/closure taught us

The issue-intelligence check should classify each issue as:

- `accepted-for-maintainer-triage`: open with maintainer/bot labels showing the report is plausible/actionable.
- `needs-info-from-us`: open with a direct request for more evidence, screenshots, diagnostics, reproduction, or clarification.
- `in-progress`: linked PR, maintainer assignment, or comment indicating active work.
- `fixed-awaiting-update`: issue closed as completed or linked PR merged; update canary and rerun the affected lane.
- `duplicate`: issue closed as duplicate; link the canonical issue and track that instead.
- `rejected-not-planned`: issue closed as not planned or invalid; capture the reason and improve future reports.
- `stale-or-unclear`: no useful movement after several checks; prepare a concise follow-up with fresh evidence or ask the operator whether to keep tracking.

When an issue changes state:

1. Update the registry.
2. Add a Mission Control note to the originating task when known.
3. If fixed or in-progress, create the next beta retest action.
4. If rejected, write a short lesson about how to improve future issue quality.
5. If more evidence is requested, collect it from the canary host if still available.

Every issue-intelligence run must choose one concrete action outcome:

- `external-comment-submitted`: publish a GitHub comment with requested evidence, narrowed repro, expected/actual clarification, or a correction.
- `internal-lesson-updated`: refine the skill, issue registry, or beta-reporting guidance because the review/closure taught us something.
- `retest-queued`: schedule or run the affected canary lane again because the issue was accepted, fixed, or needs live reproduction.
- `no-action-with-reason`: record why no external or internal action is useful right now.

Do not treat issue intelligence as passive watching. If ClawSweeper or a maintainer asks for evidence and the canary host is available, collect safe diagnostics and comment back. If a retest changes the diagnosis, comment with the new narrower finding instead of leaving stale severity claims in place. If the issue is rejected, duplicated, or corrected, update the reporting rules so the next issue is better.

ClawSweeper-specific handling:

- Treat `keep open`, `P1`, `needs maintainer review`, `needs live repro`, and positive issue-rating labels as a successful report, not a rejection.
- Treat requests for screenshots, recordings, diagnostics exports, or expected-vs-actual as follow-up evidence tasks.
- ClawSweeper updates one durable marker-backed comment in place, so compare the latest comment body and labels against the previous registry state rather than expecting new comments every run.

### Phase 5: Scheduled Issue Follow-Up

The beta issue-intelligence cron should run while there are unresolved issues in the registry.

Default cadence:

- During active beta windows: every 6 hours.
- Otherwise: daily.

Default delivery:

- Send beta issue-intelligence summaries and failure alerts to the configured beta-testing channel.
- Do not send recurring beta intelligence to ad hoc steering threads unless the operator explicitly asks for that thread to become the durable beta log.

The cron must:

1. Read `skills/openclaw-beta-testing/state/issues.json`.
2. Fetch each open issue and comments.
3. Detect label/state/comment changes.
4. Update the registry with the latest state and lessons.
5. Write a report under `output/openclaw-beta/issue-intelligence/`.
6. Add MC notes for meaningful changes.
7. Choose exactly one action outcome: external comment, internal lesson, retest queued, or no-action-with-reason.
8. Stay quiet or produce a no-change summary when nothing changed.

Cron implementation notes:

- Prefer a cheap, allowlisted orchestration model for this loop; the checker is deterministic and should not burn premium reasoning by default.
- If the operator workspace is not a git repository, explicitly tell the cron runner to skip git status or repository hygiene preflights and run the checker directly.
- Restrict tools to the minimum needed for the checker run when the cron platform supports tool allow-lists.

The cron must not:

- spam GitHub comments
- close issues
- ask for re-review without a clear reason
- claim an issue is fixed without a linked maintainer signal or successful retest

## Automation Split

Every beta test plan must label each check as **automated**, **hybrid**, or **human/manual checklist**. Do not make a human manually test things an agent can verify with commands, logs, request captures, or bot-to-bot flows.

### Automated by agents

Use agents for checks that can be proven with commands, logs, API calls, local artifacts, or controlled bot-to-bot messaging.

Automate:

- config migration and `doctor --fix`
- install/update command paths that are non-destructive or run in a disposable host/container
- runtime/auth route assertions
- harness registration checks
- model allowlist and default/fallback continuity
- self-update sentinel/handoff logic, except final real macOS destructive confirmation
- plugin install/repair/version resolution
- MCP tool inclusion in outbound request bodies
- duplicate poller detection
- Telegram bot-to-bot delivery and stream timing
- remote-aware fleet health/path verification
- prod/sandbox isolation checks
- image route preference
- cron forced-run success
- disk-growth/sync-recursion alarms
- regression issue draft generation from evidence

Agent evidence must include exact command output, route tuple, logs, message IDs, config diffs with secrets redacted, screenshots/video when relevant, and pass/fail result.

### Hybrid: agent runs, human confirms UX

Use hybrid checks when an agent can create the condition and collect evidence, but a human should judge whether the user experience is actually acceptable.

Hybrid:

- Telegram live preview: agent verifies delivery/timing; human confirms the preview looks right.
- Compaction missing-scope behavior: agent triggers the backend failure; human confirms the UI error is understandable.
- Control UI attachment/TTS rendering: agent captures screen/video; human confirms it is acceptable.
- macOS LaunchAgent self-update: agent checks logs, version, sentinel, and health; human confirms real-host destructive behavior is acceptable before broad release.
- model chooser ergonomics: agent verifies elements and screenshots; human confirms the picker is not confusing with large provider lists.
- Discord voice: agent can start/join/probe; human confirms quality, interruption, and turn-taking.

### Human/manual checklist

Keep the human manual checklist small. It should cover only things that require the operator's account, real client, or product judgment.

The operator should personally test or approve:

- WhatsApp slash command visible reply in his real client.
- Discord voice quality, barge-in, and TTS in a real call.
- Beeper/Discord media delivery when it depends on the operator's bridge/client state.
- Real OAuth org-role/scope UX on the account class that customers use.
- macOS LaunchAgent destructive self-update on a real maintained install.
- Model chooser/product ergonomics when the issue is "this confused a user."
- Any external posting, public issue filing, or message sent as the operator.

Manual checklist evidence should be lightweight: version, account/client, exact action, visible result, screenshot/video if useful, and pass/fail.

## V2 Release Gates

Use these gates when the request is about a beta or release candidate rather than a single PR.

### P0: must pass before beta

- **Upgrade / migration / self-update:** stable to beta, beta to beta, `doctor --fix`, self-update, macOS LaunchAgent handoff, restart protocol, post-upgrade cron.
- **Provider / runtime / auth routing:** `openai/gpt-*`, legacy `openai-codex/gpt-*`, Codex OAuth, Azure via LiteLLM, `litellm/` default safety, `cron-pi`, ACP option mapping.
- **Channel E2E:** Telegram, WhatsApp, Discord text, Discord voice, Beeper/Discord, media/attachments, duplicate poller prevention.
- **Fleet/control-plane canary:** multi-host version inventory, remote-aware health, correct paths, service/cron ownership, non-primary-host task completion, prod/sandbox URL separation.

### P1: must pass before stable

- **Control UI / sessions / compaction:** session history, model chooser, `/stop`, compaction errors, TTS/attachments, streaming/session resolve.
- **MCP / tools / plugins:** outbound tool inclusion, honest failure instead of confabulation, plugin auth, marketplace install/repair, AO MCP.
- **Sandbox / service isolation / sync safety:** prod/sandbox DB/service/log split, sync excludes, disk-growth stop condition, proxy/no-proxy route.

If a P0 check fails, do not call the beta ready. If a P1 check fails, beta can continue only with owner, issue, workaround, and stable-blocker status.

## Intake

For each beta/test request, capture:

- release or beta name/version
- feature or workflow being tested
- source links: Discord thread/message, release note, GitHub issue/PR, docs
- expected behavior
- target environment: OS, OpenClaw version, channel/plugin/provider, account type
- test priority: smoke / regression / exploratory / reproduction
- deadline or event dependency, especially Weekly Claw

If any field is unknown, infer what you can from Discord/release context and mark the rest as unknown. Do not block on the operator unless the missing detail changes the test.

## Evidence Pull

Before writing a test brief:

1. Pull the last 7 days of the contributor channel and filter for test asks, regressions, PRs, issues, "can someone verify", "try this", "broken", "doesn't reply", "timeout", "provider", "model", "config", "install", and "release".
2. Pull `#releases` for the actual beta/release claims and version.
3. Pull linked GitHub PRs/issues when they define a verification command, acceptance criterion, or affected subsystem.
4. Compare against the last 7 days of crew/agent ops notes: crew digests, drift reports, plans, benchmarks, and agent reports.
5. State the evidence scope in the result. Example: "read-only bot covered 3160 clawtributors messages since 2026-05-08; ops comparison used crew digests, drift reports, and benchmark artifacts."

## Test Brief Shape

Send the canary agent a brief with:

- **Problem Statement:** what needs testing and why
- **Scope:** exact feature/workflow, links, environment assumptions
- **Test Matrix:** cases to run
- **Acceptance Criteria:** what counts as pass/fail
- **Evidence Required:** logs, screenshots, command output, reproduction steps
- **Output Format:** concise report with severity and recommended next action

## V2 Test Lanes

Classify every beta ask into one or more lanes. Prefer narrow, reproducible lanes over one giant undirected beta pass.

### 1. Upgrade / Migration / Self-Update

Use when the beta changes installation, update, package handoff, LaunchAgent/service behavior, or first-run config.

Automation class: mostly automated; macOS LaunchAgent destructive self-update is hybrid/manual approval.

Minimum checks:

- clean install path
- update-from-previous-stable path
- rollback or recovery note
- service/gateway restarts cleanly
- no stranded supervisor/gateway after self-update
- version shown by CLI/UI matches installed package

Evidence:

- exact install command or update path
- OS/host/runtime
- before/after version
- service status and logs

### 2. Provider / Runtime / Auth Routing

Use for model picker, provider aliases, OAuth/API-key routing, LiteLLM/Bifrost/Azure/OpenAI/OpenRouter, Codex harness, model availability, and runtime option mapping.

Automation class: automated.

Minimum checks:

- model appears in picker/catalog
- real inference call succeeds through the selected route
- wrong route fails clearly, not silently
- allowlist/default/fallback behavior matches docs
- Codex harness is not pointed at unsupported provider IDs
- provider health checks are meaningful or explicitly marked unreliable
- effective provider/runtime/auth tuple is captured
- ACP option mapping works for `thinking`, `permissionProfile`, and `timeoutSeconds`

Recent evidence patterns:

- A model catalog can advertise `gpt-5.5-pro` while real calls fail with Azure `DeploymentNotFound`.
- Ada went down after default model was set to `litellm/gpt-5.5`; Codex harness rejected the provider.
- LiteLLM completions worked while `/health` was not a reliable monitoring signal.
- Users hit `Unknown provider: openai/gpt-5.5:default` and model picker confusion.
- OAuth-only installs routed through the wrong OpenAI path instead of Codex OAuth.

### 3. Channel Delivery

Use for Discord, Telegram, WhatsApp, Beeper, Slack, voice, media, replies, reactions, attachments, or group-chat behavior.

Automation class: hybrid. Agents can verify bot/API/log delivery; the operator or a tester confirms real-client visibility and voice quality.

Minimum checks:

- plain text reply is visible
- long reply does not truncate unexpectedly
- final answer is visible after tool progress
- attachment/media/tool artifact is delivered, not only mentioned
- async completion handoff still posts the result
- group-chat `visibleReplies` behavior works for the intended default
- slash commands and replies work in the affected channel

Recent evidence patterns:

- Discord truncation reports.
- `messages.groupChat.visibleReplies=message_tool` losing messages while `automatic` recovers them.
- async music/media output created a local `MEDIA:` artifact but did not attach it to Discord.
- WhatsApp slash commands stopped visibly replying after an update.

### 4. Fleet / Control-Plane Canary

Use when testing multi-agent operations, Mission Control trust, remote host routing, service ownership, cron ownership, prod/sandbox URLs, path verification, or fleet health.

Automation class: automated.

Minimum checks:

- per-host version/path inventory is current
- remote health checks use remote paths, not local assumptions
- service and cron definitions have the expected owner/model/runtime
- one task completes on a non-primary host
- prod and sandbox URLs point to the correct service and DB
- no false "unreachable" state on known-good hosts

Recent evidence patterns:

- false Spock unreachable status
- remote verification checked remote paths as local paths
- mixed runtime versions across agents
- cron ownership/provider contamination
- Ada overload while other agents had capacity

### 5. Control UI / Session / Compaction

Use for Codex harness, compaction, tool progress, session binding, timeout, memory/LCM, and long-running turn behavior.

Automation class: hybrid. Agents can trigger and record state; humans should verify confusing UI/UX surfaces.

Minimum checks:

- simple prompt returns quickly
- tool-heavy prompt exposes enough progress without flooding
- `/verbose` and `toolProgress` behavior is understandable
- final answer remains visible after progress output
- compaction succeeds under the active runtime/auth route
- timeout path gives a useful retry/config message
- stale/new session recovery uses correct context

Recent evidence patterns:

- Codex-backed compaction tried OpenAI Responses with Codex OAuth and failed on missing `api.responses.write` scope.
- Tool-heavy replies took long enough that users thought the bot died without progress.
- `/verbose full` flooded huge JSON output.

### 6. MCP / Tools / Plugins

Use for integrations requiring real credentials, webhooks, polling, long-lived monitors, or external APIs.

Automation class: mostly automated; credentialed/live external accounts may be hybrid.

Minimum checks:

- dry-run/unit path passes
- live test command is explicit and gated by env vars
- monitor can start, stop, and reconnect without restart loop
- credentials are redacted from evidence
- failure mode distinguishes provider/API issue from OpenClaw bug
- MCP tools are present in outbound request bodies
- missing tools fail honestly instead of producing fabricated tool behavior

Recent evidence patterns:

- Twitch live monitor fix asked users to run `TWITCH_LIVE_TEST=1 ... npx vitest run extensions/twitch/src/plugin.network.test.ts`.
- Similar restart-loop class affected LINE, Microsoft Teams, Google Chat, and Nextcloud Talk.

### 7. Sandbox / Service Isolation / Sync Safety

Use for prod/sandbox split, local service topology, DB path safety, sync recursion, workspace lifecycle, proxy/no-proxy behavior, and disk growth.

Automation class: automated.

Minimum checks:

- prod and sandbox use separate ports, DBs, services, logs, and URL map entries
- sync excludes prevent recursive mirrors
- disk-growth alarm and stop condition work
- proxy/no-proxy WebSocket path works where supported
- workspace lifecycle does not touch the wrong tree

Recent evidence patterns:

- Entity prod/sandbox split needed explicit URL/service/DB separation.
- Fleet sync created recursive memory directories until excludes were hardened.
- Spock disk filled due to local memory mirror sync.

### 8. Config / Migration

Use for config schema changes, doctor fixes, defaults, old config compatibility, and docs drift.

Automation class: automated.

Minimum checks:

- old config migrates or doctor gives exact fix
- docs still match accepted config
- redacted secrets remain redacted after save
- default values match real expected behavior
- invalid config fails with actionable error

Recent evidence patterns:

- Old Discord config was still documented while migration behavior changed.
- Shared skill installed to the wrong path until EC Home installer and symlink verification existed.
- Fleet sync created recursive memory directories until excludes were hardened.

### 9. Performance / Startup

Use for gateway startup, plugin load, schema validation, cold imports, QMD/embed jobs, and latency-sensitive routes.

Automation class: automated.

Minimum checks:

- before/after wall-clock or profiler evidence
- memory/CPU impact when relevant
- no functional regression in affected route
- health probe is meaningful
- benchmark is repeated enough to avoid one-off claims

Recent evidence patterns:

- Gateway startup PRs reduced cold import/schema validation cost.
- Azure/LiteLLM/Bifrost routes all passed a 24-call benchmark, but health semantics differed.

### 10. Issue Writing / Maintainer Handoff

Use when a finding should become a GitHub issue or PR comment.

Automation class: automated draft; external filing requires operator approval unless explicitly requested.

Minimum checks:

- one issue per bug
- exact beta version/install path
- smallest repro
- observed vs expected
- logs/screenshots/commands
- severity/frequency
- hypothesis clearly separated from fact
- workaround if known

Use the `debug-reporting` skill for the issue draft.

## Canary Routing

Use **Spock** as the default first-pass baseline canary when:

- the operator asks for a general beta-health check without naming a target
- the goal is to decide whether the release is broadly usable
- we need cleaner Linux VM signal before introducing Raspberry Pi constraints
- the failure must be separated from low-resource host behavior

Use a **constrained host** as the stress canary when:

- the operator explicitly names the constrained host
- the task is about Raspberry Pi, arm64, low-resource, systemd user gateway, cron pressure, Discord/channel pressure, or event-loop starvation
- Spock baseline passed and we need constrained-host confidence
- maintainers need live repro evidence from the constrained host

Use **Zora** for Mac/client-specific behavior. Keep Ada stable unless the beta specifically needs Ada's main gateway.

Use the normal Enterprise Crew routing rules. If the chosen canary is unavailable, switch executor only if the new host still matches the test purpose; otherwise mark blocked instead of mixing baseline and stress signals.

## Canary Execution Packet

Give the canary agent a bounded packet. Do not ask it to "test the beta" without a lane, target, and proof requirement.

Packet:

- **Objective:** one sentence, tied to release/PR/community ask.
- **Lane(s):** from the v2 lane list.
- **Target:** version, branch, PR, release, channel, host, or integration.
- **Environment:** OS, install method, auth mode, channel, provider/model, gateway version.
- **Setup:** exact commands, env vars, credentials location if already approved, and safety constraints.
- **Steps:** ordered smoke/repro/regression steps.
- **Expected:** what success looks like.
- **Stop Conditions:** destructive action, missing credential, external posting, unclear upgrade path, or repeated same failure.
- **Evidence Required:** terminal output, logs, screenshots, message links, version output, timing, and config excerpts with secrets redacted.
- **Return Format:** status, coverage, findings by severity, repro steps, evidence paths, issue drafts, and community summary.

## Baseline Matrix

For OpenClaw betas, include:

- install/update path
- startup/gateway health
- login/auth path if changed
- one Discord interaction path if relevant
- one Telegram/WebChat path if relevant
- one plugin or MCP path if relevant
- regression check for the previous release's known pain point
- docs/help text check if user confusion is likely

Add lane-specific checks from the v2 list. If the release touches Codex, channels, provider routing, config migration, or live integrations, those lanes are not optional.

## Human Manual Checklist Template

When the operator needs to personally test, give them a short checklist like this:

```markdown
## Manual Beta Checks

- [ ] WhatsApp slash command visibly replies in my real client.
  - Version:
  - Account/client:
  - Pass/fail:
  - Screenshot/video:
- [ ] Discord voice joins, speaks, interrupts, and TTS sounds acceptable.
  - Channel:
  - Pass/fail:
  - Notes:
- [ ] Model chooser is understandable with the large provider list.
  - Path:
  - Pass/fail:
  - Confusing label or missing option:
- [ ] macOS LaunchAgent self-update completes on real maintained install.
  - From version:
  - To version:
  - Pass/fail:
  - Any stale version/sentinel:
```

Keep the list under 10 items. Everything else should be delegated to a canary agent with evidence requirements.

## Severity Model

Use this severity model for findings:

- **P0:** data loss, secret exposure, account/token leak, destructive update failure, gateway unable to start, or mass message misdelivery.
- **P1:** common install/update break, channel replies invisible, compaction/session failure that blocks use, wrong model/provider route, self-update strands gateway, live integration restart loop.
- **P2:** confusing UX with workaround, slow path that looks dead, docs/config mismatch, flaky health check, non-critical plugin failure.
- **P3:** polish, wording, minor docs, non-blocking inconsistency.

Frequency:

- **single repro:** one verified environment
- **multi repro:** two or more users/hosts
- **community pattern:** repeated reports in Discord/AO/issues

Risk modifiers:

- raise severity for defaults, silent failures, loss of final answer, private/mod channel impact, or auth/provider confusion.
- lower severity when the issue is default-off, live-test-only, or clearly isolated with a safe workaround.

## Report Shape

Return:

- **Status:** pass / fail / mixed / blocked
- **Coverage:** what was tested
- **Findings:** grouped by severity
- **Repro Steps:** only for failures
- **Evidence:** links/paths to logs, screenshots, outputs
- **Issue Drafts:** GitHub-ready drafts through `debug-reporting` for any issue-worthy failures
- **Community Summary:** what to tell the contributor channel
- **Next Ask:** what feedback or testing should be requested next

### Phase 1 Canary Report Template

Use this compact report after a canary run. Attach or link detailed logs/evidence from MC; do not dump raw logs into chat.

```markdown
## OpenClaw Beta Canary Report

Status: pass-expand / mixed-hold / fail-rollback / blocked
Version tested:
Target agent/host:
Previous version:
Install/update path:
Observation window:
MC task:

### Result
- Recommendation:
- Rollback status:
- Safe next step:

### Checks
- Version/update:
- Gateway/service health:
- Doctor/status:
- Visible channel reply:
- Model/runtime route:
- Tool/long-turn behavior:
- Cron/delivery:
- Plugin/session CLI:
- Logs/watchdog:

### Findings
- P0:
- P1:
- P2/P3:

### Evidence
- Commands/logs:
- Screenshots/messages:
- Config excerpts:
- Checkpoint/watchdog:

### Community Draft
Short contributor-channel note for operator approval:
```

### MC Closure

Before reporting completion to the operator:

1. Add the canary report and evidence links/paths to the MC task.
2. Move the MC task to review for `pass-expand`, `mixed-hold`, or `fail-rollback`.
3. Mark the MC task blocked with reason for `blocked`.
4. Confirm the watchdog/checkback is removed, disabled, or still intentionally scheduled with the next check time.

## Issue Writing

When the canary agent or another tester finds a failure, use the issue-writing reference to turn the finding into a GitHub-ready issue.

Required flow:

1. Read `skills/debug-reporting/SKILL.md`.
2. Read `skills/openclaw-beta-testing/references/github-issue-template.md` or your local issue-writing reference.
3. Keep one issue to one bug.
4. Include exact beta version and install/update path.
5. Include the smallest repro sequence and evidence from the test run.
6. Separate observed facts from hypothesis.
7. Draft the issue first. File externally only if the operator explicitly approved filing.

Issue draft must include:

- summary
- impact/severity/frequency
- steps to reproduce
- expected behavior
- actual behavior
- evidence: logs, screenshots, command output, Discord/release links
- environment: OS, install method, OpenClaw version, Node/runtime if relevant
- hypothesis
- suggested fix or next diagnostic step
- workaround if known

If there are multiple failures, split them into multiple issue drafts unless they share the same root repro.

## Community Feedback Loop

After tests:

1. Convert failures into crisp repros for maintainers.
2. Convert issue-worthy failures through `debug-reporting`.
3. Convert confusing behavior into docs or release-note asks.
4. Convert successful tests into short confidence notes for the contributor channel.
5. Do not post the community summary as the operator without explicit approval.

Good community summary shape:

- what was tested
- environment
- result
- issue/PR link if already public
- remaining ask for other contributors

Do not dump raw logs into Discord. Link artifacts or attach only the minimal evidence needed.

## What Not To Automate

Do not automate these without explicit operator approval:

- posting as the operator
- filing GitHub issues externally
- destructive update/self-update runs on live hosts
- live integrations that spend money or hit production accounts
- private/mod channel sentiment publication
- credentialed tests where secrets could leak in logs
- changing global model defaults or cron routing

Automation is safe for:

- read-only evidence collection
- draft test briefs
- draft issue reports
- local smoke tests in disposable workspaces
- scheduled draft-only beta feedback monitors, after `cron-intelligence`

## Cron Candidate

A beta feedback monitor can be useful, but do not create it directly from this skill.

If the operator approves automation, route through the local cron/scheduling governance first. The likely routing is an OpenClaw cron because the job needs LLM judgment to classify feedback, dedupe issues, and draft test briefs.
