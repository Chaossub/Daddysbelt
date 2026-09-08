# Phase 3 — Incident & Evidence System

This release completes the staff-side VC incident workflow on top of the Phase 2 conversation/transcript build.

## Incident bookmarks
- Staff-only `/staff` dashboard and `/incident-bookmark` command.
- Default bookmark window: 5 minutes before + 2 minutes after.
- Incident IDs use an atomic MongoDB counter (`INC-0001`, etc.).
- Bookmark form can tag involved users, category, severity, and a reason/note.
- Incident details can be edited later from the incident UI.

## Incident review UI
Incident cases expose buttons for:
- Add Note
- Extend Window
- View Transcript
- Generate Neutral Summary
- Mark Evidence
- View User History
- Merge Nearby Incidents
- Close
- Edit Details

## Extendable evidence clips
- Before a recording ends, extending an incident updates the window used for the final clip.
- After a recording ends, Mommy downloads the archived full recording from Discord, rebuilds split recording parts when needed, and posts a replacement incident clip for the larger window.
- Original full-session archive messages are not overwritten.

## Evidence workflow
- `open`, `evidence`, `closed`, and `merged` incident states.
- Dedicated Evidence button on the staff dashboard.
- Evidence incidents remain in MongoDB until staff closes/changes them; there is no automatic evidence deletion in this release.
- Merging keeps the widest window, combined people list, combined reasons, and moves incident notes to the surviving case.

## User history
For tagged people, staff can review:
- Number of linked incidents.
- Number of successful timeout events.
- Recent VC automod actions.

## Case summaries
- Mommy can create a concise neutral summary from the incident transcript and staff notes when OpenAI is configured.
- The summary is instructed not to infer motives, diagnose people, or decide guilt.
- It ends with human review required.
- If AI is unavailable, staff still receive a metadata summary.

## Existing safeguards retained
- Neither bot can ban or kick through bot moderation controls.
- Human admins/moderators use Discord-native ban/kick tools.
- Ban announcement messages may still react to human-performed bans.
- Mommy may timeout/remove configured roles and escalate repeated timeout patterns for staff review.
- Recording/transcript/incident tools remain staff-only.
