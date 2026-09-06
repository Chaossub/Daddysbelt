# Mommy.exe UI / AI Production Pass

This deploy is the button-first Mommy.exe pass before the final roles/moderation deploy.

## Included
- Daddy's Belt performs a one-time cleanup of stale globally registered slash commands (including old ghost `/record` commands), then keeps its intended commands guild-scoped.
- Mommy.exe remains the owner of the VC recording cog.
- Mommy.exe dashboard is button-first with Back/Home navigation throughout.
- Voice panel: Start/Join, Stop/Leave, Clip Last 3 Minutes, Recording Setup, fixed-room Auto Join, Captions, Follow Members, status, and Auto Join off.
- Channel/user configuration uses Discord selects rather than typed IDs.
- Follow-member management is button/select based.
- 3-minute clipping works from the active aligned VC session.
- AI personality prompt updated for more varied responses and stronger context-specific sass without repetitive catchphrases.
- Automod and Verification pages remain visible as previews; the actual House Trained/roles/infraction system is intentionally reserved for the final moderation deploy.

## Dashboard entry
- Mention Mommy.exe with `dashboard`, `panel`, `controls`, or `settings`.
- Or send `!mommy` / `!mommy dashboard`.
- `/record` commands remain available as fallback controls for this deploy.
