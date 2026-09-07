# Phase 2 - Recording Archive, Search, Notes, and Mommy Memory

This build adds/completes:
- Searchable full end-of-session VC transcript archive in MongoDB.
- Transcript search by exact phrase or all-keywords mode.
- Optional user ID, voice channel ID, and days-back filters.
- Search within a specific recording.
- Recent recording archive browser with links back to Discord transcript messages.
- Participant/speaker view for archived recordings.
- General and timestamped notes on recordings/incidents.
- Per-user Mommy.exe conversational memory that only stores directly addressed interactions.
- /mommy-memory panel: View Memory, Memory On, Memory Off, Clear My Memory.
- Memory opt-out applies to both text and voice conversations.
- VC transcripts and moderation records stay separate from personal Mommy memory.

Phase 1 moderation safety remains intact: bots do not execute bans or kicks.

## Conversational Mommy.exe additions
- Mommy.exe replies directly in ordinary text channels when @mentioned or addressed by wake name (`Mommy`, `Mommy.exe`, `Mommy bot`).
- Text conversations retain per-user recent direct-chat context and optional long-term facts.
- VC AI now supports conversational participation with any VC size: with one human she can converse naturally; in group VC she waits until addressed, then keeps a short active conversation window for natural follow-up turns.
- VC follow-up context is stored only in RAM and expires; it is not permanent personal memory.
- Permanent VC memory is still written only when the speaker explicitly addresses Mommy.exe and has memory enabled.
