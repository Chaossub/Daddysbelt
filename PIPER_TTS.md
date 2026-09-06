# Mommy.exe — Piper TTS

Mommy.exe uses the self-hosted Piper service when `PIPER_TTS_URL` is configured.

Required on the main bot Render service:

- `PIPER_TTS_URL=https://mommy-piper-service.onrender.com/tts`

Optional:

- `PIPER_API_KEY` — must match the secret configured on the Piper service, if one is used.

TTS order:

1. Piper
2. OpenAI fallback


The configured Piper service uses `en_US-libritts_r-medium`, speaker `3922 (0)`.
