# Daddy's Belt — Phase 3A

Includes all working welcome and scheduled-message features plus
custom text triggers.

## Trigger features

- Create, edit, pause/resume, test, and delete triggers
- Match by contains, exact, starts with, or ends with
- Multiple random replies separated by `|||`
- Optional per-trigger channel restriction
- Optional author ping
- Configurable cooldowns
- Ignores bots
- Stops after one trigger fires per message
- Reply placeholders:
  `{mention}`, `{username}`, `{display_name}`, `{server}`, `{channel}`

## Required Discord setting

In Discord Developer Portal → Bot → Privileged Gateway Intents,
enable **Message Content Intent**. Without it, triggers cannot read
message text.
