# Mommy.exe Member Games — v5.2.1

## v5.2.3 — Configurable write-in blank count

- The host can tap **Write-in Blanks** and enter the exact number of blank white cards to include in every hand.
- Blank count can be anywhere from **0 through the current hand size**.
- A hand can be entirely write-in blanks if desired.
- Changing hand size clamps the blank count so it never exceeds the hand.
- Hand refills now preserve the requested blank/regular-card mix exactly.

The member game system now runs through Mommy.exe while preserving all existing Mommy voice, recording, follow, automod, memory, staff, transcript, clip, and incident features.

## Ownership split
- Daddy's Belt: staff/admin dashboard and Daddy @mention conversation.
- Mommy.exe: existing staff/voice systems plus the public member-games layer.
- Daddy no longer loads or publishes the games cog.
- Mommy staff dashboard includes **Publish Games Panel**.

## Public member side
The published Mommy.exe panel is button-only. Members do not receive Mommy's staff dashboard.

Current game: Cards Against Humanity-style party game with custom personal/server packs, 10-black/30-white playable minimum, multiple packs per game, write-in blank white cards, and Human / AI-if-needed / AI-always Czar modes.


## v5.2.2 — Mommy as a player
- Added a host toggle to add/remove Mommy.exe as a real player at the table.
- Mommy receives a hand and uses AI to choose her own submission when she is not Czar.
- Mommy joins the same rotating Czar queue as human players in rotating modes.
- On Mommy Czar rounds she judges anonymous submissions with the configured humor style.
- Mommy can score round points and win games; bot wins are not written to human user stats.
- AI Always keeps Mommy in the Czar seat; AI If Needed can use a normal rotating table when Mommy is added.
