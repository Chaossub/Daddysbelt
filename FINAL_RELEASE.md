# Daddy's Belt + Mommy.exe — Final combined feature build

This build combines the Mommy.exe button/AI/VC work with the final access + moderation systems.

## Mommy.exe
- Admin-only button-first dashboard (`!mommy`) with Back/Home navigation; every dashboard interaction re-checks admin access
- Voice recording, captions, 3-minute clips, fixed auto-join, Follow Members
- Followed members are joined immediately if already in VC and reconciled after restarts
- 1-person / 2-person AI behavior, varied personality, direct-conversation memory only
- VC automod with built-in N-word rule (3 credible detections / 10 min by default)
- Possible quotes are logged but do not count automatically
- 7-day detection/punishment retention
- House Trained punishment ladder: 5m → 15m → 1h → 24h
- Automatic House Trained restoration, including after Render restarts
- Exception roles + owner/bot exemptions
- Add/remove custom VC automod triggers from buttons/modals
- Member history, forgive, and manual restore controls
- Rules reaction verification grants House Trained
- Configurable rules channel/message/emoji/role
- Punishment sound toggle. Optional `MOMMY_PUNISHMENT_SOUND_PATH`; otherwise Mommy TTS says “You're banned.” when connected to VC.

## Daddy's Belt
- Keeps existing text/admin/triggers/dashboard features
- No VC ownership
- Stronger stale slash-command cleanup: clears Daddy guild command registrations before resyncing current commands
- New button-based **Text Automod** dashboard section
- Add/remove custom text automod triggers
- Per-trigger threshold/window/timeout
- Text automod exception roles

## Existing Render variables
No new required variables beyond the dual-bot setup. Optional:
- `MOMMY_PUNISHMENT_SOUND_PATH` — path to a short audio file available to the Render process.

## Discord setup
For the access gate to work, configure the **House Trained** role so it grants access to normal server categories while `@everyone` only sees the rules/welcome area. Mommy.exe must be able to manage House Trained (her role above it, or Administrator).
