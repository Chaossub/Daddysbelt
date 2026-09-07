# Mommy.exe / Daddy's Belt Staff Moderation Build

## Phase 1 — moderation safety
- Bot-executed bans, unbans, and kicks are disabled.
- Mommy.exe VC automod remains limited to warnings, role removal, timeouts, and role+timeout.
- Legacy temp-ban controls were removed from Mommy.exe's visible VC automod panel.
- Excessive timeouts are escalated for human review at 3, 5, 7, 9... timeouts in a rolling 7-day window.
- Escalations are written to MongoDB, sent to the configured moderation/log channel when available, and DM'd to the server owner as a fallback/secondary alert.

## Phase 2 — recording archive + transcript search
- Completed end-of-session transcripts are indexed in MongoDB by session and speaker line.
- `/staff` opens a staff-only button dashboard.
- Transcript phrase search supports a phrase, optional user ID, and lookback days.
- Recent recording sessions can be opened from buttons.
- Recording notes can be general or timestamped.
- Archive records retain Discord transcript/audio message IDs for navigation.

## Phase 3 — incident bookmarks
- `/incident-bookmark` and the Staff Console `Bookmark Incident` button create incidents.
- Default incident window is five minutes before the bookmark and two minutes after it.
- When the source session ends, Mommy.exe renders the incident window as an MP3, posts it to the recording archive channel, and includes a transcript preview.
- Incident UI supports opening incidents, adding notes, marking evidence, and closing incidents.

## Mommy.exe conversational memory
- Text chat activation supports @mention plus wake names at the beginning of a message: `Mommy`, `Mommy.exe`, and `Mommy bot`.
- Chat history is only saved when a message is addressed to Mommy.exe.
- VC conversational history/facts are only saved when the transcribed speech explicitly addresses Mommy.exe.
- A separate per-user fact store remembers explicit memory/preference statements such as `remember that...`, `my favorite...`, `I like/love/hate/prefer...`, `call me...`, and `my name is...`.
- Full VC transcripts are never treated as personal conversational memory.

## Staff access
The new staff tools use the existing access-control system and require at least Moderator access. Server owner/admins continue to pass automatically.

## New MongoDB collections
- `vc_recording_sessions`
- `vc_transcript_rows`
- `vc_recording_notes`
- `vc_incidents`
- `vc_timeout_escalations`
- `mommy_user_facts`
