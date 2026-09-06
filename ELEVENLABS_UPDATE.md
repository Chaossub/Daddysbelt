# Mommy.exe — ElevenLabs TTS update

When both of these Render environment variables are present, Mommy.exe automatically uses ElevenLabs for all generated voice audio:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

OpenAI remains in use for transcription / AI conversation, but OpenAI TTS becomes the fallback only when the ElevenLabs variables are absent.

Optional ElevenLabs settings:

- `ELEVENLABS_MODEL_ID` (default: `eleven_multilingual_v2`)
- `ELEVENLABS_OUTPUT_FORMAT` (default: `mp3_44100_128`)
- `ELEVENLABS_STABILITY`
- `ELEVENLABS_SIMILARITY`
- `ELEVENLABS_STYLE`
- `ELEVENLABS_SPEED`

The Mommy.exe Voice Settings page detects ElevenLabs automatically and shows a reusable **Test ElevenLabs Voice** button. The same panel can be used for repeated tests without reopening `!mommy`.

Changing `ELEVENLABS_VOICE_ID` in Render and redeploying changes Mommy's voice.
