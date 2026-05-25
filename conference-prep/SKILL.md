---
name: conference-prep
description: "Prepare for conferences, summits, trade shows, and investor events: research attendees/speakers/agenda, build pitch-deck briefs, schedule meetings, enrich contacts, capture session notes, and run post-event follow-ups."
---

# Conference Prep

Use when a user, team, or client is attending, speaking at, sponsoring, or evaluating a conference.

This is the canonical skill. `conference-gtm` is the legacy alias.

## Workflow

1. Establish the conference brief: name, dates, location, venue, source URLs, goals, audience, and company/context.
2. Build the research pack: agenda, speakers, sponsors, attendees if available, side events, target accounts, and source log.
3. Segment targets into customer, partner, investor, press, competitor/intel, and internal/logistics buckets.
4. Prepare the pitch deck brief: audience-specific thesis, proof points, demo angle, objections, asks, and missing slide/assets list.
5. Build the meeting scheduler: priority contacts, desired meeting type, best channel, owner, status, suggested slots, and follow-up owner.
6. Enrich contacts from approved sources: CRM, Fireflies, Gmail, Calendar, LinkedIn/public web, Beeper/WhatsApp/iMessage where appropriate, and prior conference notes.
7. Prepare session notes capture: day-by-day note templates, quote/intel fields, action owner, and next-step tags.
8. Run post-conference follow-up: same-day raw capture, 48-hour follow-ups, CRM updates, debrief, and sequence drafts.
9. Validate the prep pack with `scripts/validate-conference-pack.py` before claiming it is ready.

## Outputs

Default output folder:

```text
output/conference-prep/<event-slug>/
```

Minimum files:

- `<event-slug>-prep-pack.md`
- `sources.md` or a source log inside the prep pack
- optional `meeting-scheduler.csv`
- optional `session-notes-template.md`
- optional `follow-up-queue.csv`

## Rules

- Do not send emails, LinkedIn messages, direct messages, or calendar invites on behalf of the user/client without approval. Draft first.
- Mark attendee lists as `public`, `provided`, or `inferred`; do not pretend a private attendee app has been scraped.
- For investor or high-profile events, separate relationship building from direct selling. The pitch should be contextual, not generic booth copy.
- If the event is within 72 hours, skip perfection: produce a pocket brief, target list, scheduler, and follow-up capture surface first.
- If the event already happened, run the post-conference branch first and recover notes from chat, calendar, email, photos, and memory.

## References

- `references/runbook.md` - detailed workflow and timing.
- `references/templates.md` - pack, scheduler, notes, deck, and follow-up templates.
- `references/source-checklist.md` - source order and verification rules.
