# Mommy.exe Voice Settings Update

- Adds **AI → Voice Settings** to the admin-only Mommy.exe dashboard.
- All supported built-in TTS voices are selectable.
- **Preview Selected** plays a temporary sample in the admin's current VC and does not save the choice.
- **Set Selected** stores the voice in MongoDB per server and applies it to future Mommy.exe TTS immediately.
- Voice choice survives Render restarts.
- **Reset Default** returns to `MOMMY_TTS_VOICE` (or `shimmer` if unset).
- Mommy's punishment TTS and normal AI speech both use the saved voice.
