---
name: publish-skill
description: "Use when the operator says publish a skill, promote a skill publicly, or ship a skill to SuperAda, GitHub, and ClawHub. Sanitizes the skill bundle for personal info/secrets, publishes a GitHub source copy, updates SuperAda skill pages/install assets, and publishes to ClawHub with receipts."
version: 1.0.0-ec.1
author: Book / operator crew
license: MIT
metadata:
  hermes:
    tags: [skills, publishing, superada, github, clawhub, sanitization, enterprise-crew]
    related_skills: [skill-sharer, superada-content-publishing, github-pr-workflow, vaultwarden-bw-cli]
---

# Publish Skill

Publish an agent skill through the operator crew public distribution path:

```text
source skill → sanitizer → GitHub source repo → SuperAda skills page/assets → ClawHub publish → receipts
```

The default targets are:

- **GitHub:** `h-mascot/enterprise-crew-skills`
- **SuperAda:** `superada.ai/skills/<slug>/` plus `public/skills/<slug>/`
- **ClawHub:** `https://clawhub.ai/<owner>/<slug>` via `clawhub skill publish`

## When to use

- "Publish this skill."
- "Put this on SuperAda, GitHub, and ClawHub."
- "Make this an EC skill and publish it."
- "Share this skill publicly."

Do not use for private agent-only skills, skills containing credentials, or customer-specific workflows unless the public version is explicitly redacted.

## Required inputs

1. Source skill directory containing `SKILL.md`.
2. Public slug. Default: frontmatter `name`.
3. Public title and short description.
4. Version. Default for first public release: `1.0.0`; bump patch for updates.
5. Owner handles:
   - GitHub repo owner/org.
   - ClawHub owner (`--owner`) if not personal default.
6. Publish scope: GitHub, SuperAda, ClawHub, or all three.

## Mandatory sanitizer gate

Before any external publish, run the sanitizer script:

```bash
CH="<ENTERPRISE_CREW_HOME>"
"$CH/Skills/publish-skill/scripts/sanitize-skill.py"   --input /path/to/skill   --output /tmp/public-skill/<slug>   --report /tmp/public-skill/<slug>-sanitize-report.json
```

Then read the report. If `blocked` is non-empty, stop and fix the source or add an explicit safe replacement. Never publish a bundle with:

- API keys, bearer tokens, OAuth cookies, private keys, `.env` files, or password examples.
- Personal home paths such as `/Users/<name>/` or `/home/<name>/`.
- Tailscale/private IPs or internal hostnames.
- Private emails, customer names, raw chat/session transcripts, or screenshots with private data.
- Enterprise-only absolute paths unless the public skill is intentionally EC-only and the path is documented as local install context, not a secret.

## GitHub publish

Use a clean worktree of `enterprise-crew-skills` when the main checkout is dirty.

```bash
REPO=<HOME>/Code/enterprise-crew-skills
slug=<slug>
mkdir -p "$REPO/$slug"
rsync -a --delete /tmp/public-skill/$slug/ "$REPO/$slug/"
python3 "$CH/Skills/publish-skill/scripts/update-enterprise-crew-skills-readme.py"   --repo "$REPO" --slug "$slug" --description "<description>"
cd "$REPO"
git add "$slug" README.md
git commit -m "Add skill: $slug"
git push origin HEAD:main
```

If the repo is dirty with unrelated work, create a separate worktree from `origin/main`, copy the sanitized bundle there, commit, and push. Do not overwrite unrelated local changes.

## SuperAda publish

Use the SuperAda skill section, not workflow packs.

1. Copy sanitized files into `public/skills/<slug>/`.
2. Add or update a record in `src/data/skills.ts` with:
   - `slug`
   - `title`
   - `description`
   - `tagline`
   - category
   - source URLs
   - ClawHub slug/URL when available
   - install command
   - artifact links
3. Add or update `public/install/<slug>` if the site uses curl installers for this skill.
4. Run the writing humanizer on public prose.
5. Run `npm run build`.
6. Push and poll production until `/skills/<slug>/`, `/resources/skills/`, and direct artifact URLs return 200 with markers.

Use a clean worktree when `<HOME>/Code/superada-ai` is dirty or conflicted.

## ClawHub publish

Preferred command:

```bash
clawhub skill publish /tmp/public-skill/<slug>   --slug <slug>   --name "<Title>"   --version <semver>   --owner <owner>   --tags latest   --clawscan-note "Public agent skill; no secrets; sanitized by operator crew publish-skill gate."
```

If the CLI is not logged in, do not fake success. Report:

```text
ClawHub blocked: CLI not logged in. Run `clawhub login --device` or provide a ClawHub API token.
```

After publish, verify:

```bash
curl -fsSL https://clawhub.ai/api/v1/skills/<slug>
curl -fsSL "https://clawhub.ai/api/v1/skills/<slug>/file?path=SKILL.md"
```

## Receipts

Write a receipt under:

```text
crew workspace/Output/skills/publish-skill/<slug>/<timestamp>-receipt.md
```

Include:

- source path and source SHA256 for every published file
- sanitizer report path and blocked count
- GitHub commit/URL
- SuperAda commit/live URLs/build output
- ClawHub command result/API verification or exact auth blocker
- final public install commands

## Common pitfalls

1. **Publishing before sanitizing.** Wrong. Sanitizer first, every time.
2. **Treating GitHub as ClawHub.** GitHub source copy is not a registry publish.
3. **Treating SuperAda build success as live success.** Poll production URLs.
4. **Using a dirty SuperAda checkout.** Use a clean worktree when needed.
5. **Printing tokens while debugging ClawHub auth.** Never print API tokens; use `clawhub whoami` metadata only.
6. **Adding skills directly to the ClawHub repo.** ClawHub repo policy says skills must be uploaded through the CLI, not committed into repo content.
7. **Leaving private EC paths in public examples.** Replace them with placeholders or mark them explicitly as local EC operator context.

## Verification checklist

- [ ] Source skill inspected.
- [ ] Sanitizer report has zero blockers.
- [ ] Public bundle contains `SKILL.md` and references/scripts needed to run.
- [ ] GitHub source copy pushed and URL verified.
- [ ] SuperAda page/assets built locally and verified live.
- [ ] ClawHub publish completed or exact auth blocker reported.
- [ ] Receipt written under operator output.
