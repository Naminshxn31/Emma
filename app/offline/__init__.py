"""Emma without the internet (OFFLINE_FALLBACK, docs/emma-offline-mode.md).

No model writes a word here. What she can say offline is a short list of
fixed lines a person approved (`lines`) plus the approved story lines; what
she hears is decoded on the machine (`asr`); which of those she says is
chosen by rules (`router`); the audio was rendered while online (`voice`).
`app/providers/offline.py` puts them behind the provider interface, so the
session, the face and the turn log see an ordinary provider.

Every module here is stdlib-only at import: scripts/robot_server.py and the
fetch/render/approve scripts import their pins without loading a model.
"""
