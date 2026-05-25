# Conference Prep

Reusable conference preparation workflow for summits, trade shows, investor events, and industry conferences.

## What it covers

- Pre-conference research across attendees, speakers, agenda, sponsors, and side events
- Pitch-deck preparation and missing-asset tracking
- Meeting scheduler creation
- Contact enrichment from approved internal and public sources
- Session notes capture
- Post-conference follow-up queue and debrief

## Use

Load `SKILL.md`, then generate a prep pack under:

```text
output/conference-prep/<event-slug>/
```

Validate the final pack:

```bash
python3 conference-prep/scripts/validate-conference-pack.py \
  --skill-dir conference-prep \
  --pack output/conference-prep/<event-slug>/<event-slug>-prep-pack.md
```

## Safety

Draft outbound messages and calendar invites first. Do not send on behalf of a user/client without approval.
