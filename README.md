# Espadna Sim Content Agent

Telegram-first inventory, content production and public SIM-number catalog for **Espadna Sim**.

## Status
Local inventory foundation and private Telegram bot scaffold implemented. Nothing is deployed or live. See [setup and security](docs/SETUP.fa.md).

## Product requirements
- Roles: owner (user management), admin (inventory and sale operations), viewer (read public-safe content). Unknown users are denied.
- Excel import is additive only. Missing numbers never delete records. Imports never reset sale/reservation state. Role management is owner-only; changes are audited.
- Monthly content prepared in advance when possible; six connected Instagram stories daily; scheduled delivery at **08:00 Asia/Tehran**.
- 30 approved **cinematic dark navy/black and metallic-gold** story compositions, rotating in five six-story groups. The approved contact sheet is a *visual reference*, not an editable asset. Rebuild individual 1080×1920 templates with independent text layers and verified Persian typesetting.
- No prices in any Instagram image, caption, or public channel post. No geography claims or city backgrounds tied to prefixes.
- Entire phone number must be gold, accurately spaced and validated. Operator is displayed only if explicitly verified from inventory; Espadna Sim is a retailer, not a mobile network operator. Never depict Espadna-branded carrier SIMs.
- Telegram public channel: inventory-backed catalog, inquiry button routing to a sales bot; update listings when sold.
- Replace scheduled content featuring sold/reserved numbers; notify staff if older downloaded versions are invalid.
- No paid services or deployment without owner approval.

## Planned architecture
Current implementation: Python 3.11+, SQLite, openpyxl and python-telegram-bot. Cloudflare Workers + D1 remains a future deployment option requiring an adapter/migration and review. Keep secrets in runtime environment, never in Git.

## Next milestones
1. Inventory schema, Excel parser, bot authorization and audit log.
2. Telegram inventory and role management.
3. Editable story renderer and golden-reference image tests.
4. Monthly planner, 08:00 Tehran delivery, revisions.
5. Public channel publishing and inquiry workflow.

## Security
This repository is public. **Never commit bot tokens, private phone lists, customer data, server keys, or inventory Excel files.** Use environment secrets and test fixtures only.
