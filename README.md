# Espadna Sim Content Agent

Telegram-first inventory, content production and public SIM-number catalog for **Espadna Sim**.

## Status
Project initialization. No Telegram bot, automation, image rendering, or deployment is live yet.

## Product requirements
- Roles: owner, manager (full operations), viewer (download/read content only).
- Authorized managers upload Excel inventories (append or explicit full sync), register sold/reserved numbers and manage users. Changes are audited.
- Monthly content prepared in advance when possible; six connected Instagram stories daily; scheduled delivery at **08:00 Asia/Tehran**.
- 30 approved **cinematic dark navy/black and metallic-gold** story compositions, rotating in five six-story groups. The approved contact sheet is a *visual reference*, not an editable asset. Rebuild individual 1080×1920 templates with independent text layers and verified Persian typesetting.
- No prices in any Instagram image, caption, or public channel post. No geography claims or city backgrounds tied to prefixes.
- Entire phone number must be gold, accurately spaced and validated. Operator is displayed only if explicitly verified from inventory; Espadna Sim is a retailer, not a mobile network operator. Never depict Espadna-branded carrier SIMs.
- Telegram public channel: inventory-backed catalog, inquiry button routing to a sales bot; update listings when sold.
- Replace scheduled content featuring sold/reserved numbers; notify staff if older downloaded versions are invalid.
- No paid services or deployment without owner approval.

## Planned architecture
Cloudflare Workers + D1, object storage if needed, and a portable rendering worker with an Ubuntu fallback. Keep secrets in deployment environment, never in Git.

## Next milestones
1. Inventory schema, Excel parser, bot authorization and audit log.
2. Telegram inventory and role management.
3. Editable story renderer and golden-reference image tests.
4. Monthly planner, 08:00 Tehran delivery, revisions.
5. Public channel publishing and inquiry workflow.

## Security
This repository is public. **Never commit bot tokens, private phone lists, customer data, server keys, or inventory Excel files.** Use environment secrets and test fixtures only.
