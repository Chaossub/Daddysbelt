# Mommy.exe - VC Migration Build

This build keeps Daddy's Belt and Mommy.exe in the same Render service. Daddy keeps the text/admin features; Mommy.exe now owns the voice-recording cog.

## Render variables

Keep your existing variables. Mommy.exe uses:

- `DISCORD_TOKEN_MOMMY`
- `ENABLE_MOMMY=true`
- `MOMMY_MONGODB_DATABASE=mommy_exe` (optional; this is the default)
- `MOMMY_AI_MODEL=gpt-5.6-luna` (optional)
- `MOMMY_TTS_VOICE=shimmer` (optional)
- `MOMMY_CONVERSATION_WINDOW_SECONDS=45` (optional)
- `MOMMY_JOEY_USER_ID=<discord user id>` (optional; enables Joey-specific solo sass)

Daddy can keep using the legacy `DISCORD_TOKEN`; `DISCORD_TOKEN_DADDY` is also supported.

## What moved to Mommy.exe

- Voice receive / full recording
- Live captions
- Final speaker-labelled transcript
- AI voice replies and TTS mixed into the saved audio
- One-human natural VC conversation
- Two-human VC behavior: respond when addressed, when shit-talked, or while a 45-second conversation with that speaker is active
- Long-term AI memory only for exchanges that actually involve Mommy.exe
- `/record clip` saves the previous three minutes from an active voice session
- `/record followadd`, `/record followremove`, `/record followlist` configure members Mommy.exe automatically follows into VC

## Important after deploying

Mommy.exe uses her own Mongo database. Re-run `/record setup` (and captions/auto settings as desired) for Mommy.exe because Daddy's previous voice configuration is not automatically copied into her separate database.

Daddy no longer loads the voice-recording extension in this build, avoiding two bots trying to own the same VC.

## Next production pass

The next pass wires the button-first UI for the voice controls plus VC automod, exception roles, House Trained removal/restoration, rules-reaction verification, and punishment audio.
