# Mommy.exe - Dual Bot Pass 1

This build runs Daddy's Belt and Mommy.exe inside the same Render service/process.

## Render environment variables

Keep your existing variables and add:

- `DISCORD_TOKEN_MOMMY` = Mommy.exe bot token
- `ENABLE_MOMMY` = `true`

Optional:

- `DISCORD_TOKEN_DADDY` = Daddy's Belt token. If omitted, the existing `DISCORD_TOKEN` still works.
- `MOMMY_MONGODB_DATABASE` = defaults to `mommy_exe`
- `MOMMY_AI_MODEL` = defaults to the same model used by the existing voice AI

Existing variables still used:

- `MONGODB_URI`
- `MONGODB_DATABASE` (Daddy database)
- `OPENAI_API_KEY`
- `PORT` (provided by Render)

## Pass 1 behavior

- Daddy's Belt remains unchanged and still owns the existing VC cog.
- Mommy.exe logs in as the second Discord client.
- Mention Mommy.exe in text to get an AI reply.
- Mention Mommy.exe with `dashboard`, or type `!mommy`, to open the button-first dashboard.
- Mommy.exe conversational memory is stored in her own MongoDB database and only stores exchanges directed at her.
- Voice/automod/clip/verification buttons are visible placeholders for the next migration pass; they do not change server settings yet.

## Safety switch

Set `ENABLE_MOMMY=false` in Render to boot Daddy's Belt by himself without changing code.
