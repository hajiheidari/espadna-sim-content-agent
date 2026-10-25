# Espadna Sim Content Agent

Telegram-first inventory, content production and public SIM-number catalog for **Espadna Sim**.

## Status
Local inventory, private Telegram scaffold, monthly content planning, tracked exports and editable draft HTML proof sheets are implemented. Nothing is deployed or live. See [setup and security](docs/SETUP.fa.md).

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
1. Obtain the approved contact sheet and rebuild its 30 individual story compositions; the current HTML renderer is a visibly marked draft proof, not an approved template.
2. Add raster rendering and reference-image verification on top of the validated public-content boundary.
3. Build a delivery queue with retries/idempotency and last-moment inventory revalidation for 08:00 Tehran delivery. Current planning records target times only.
4. Implement public channel publishing, updates and inquiry workflow after review and explicit launch approval.

## Security
This repository is public. **Never commit bot tokens, private phone lists, customer data, server keys, or inventory Excel files.** Use environment secrets and test fixtures only.
