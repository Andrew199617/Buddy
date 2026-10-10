"""Chat models served through the official Claude Code and Codex CLIs.

Each CLI signs in with the user's own Claude or ChatGPT subscription and keeps
its credentials in its own config folder. Buddy starts the CLI, sends the chat,
and streams the reply; it never reads or stores the subscription tokens.

The modules in this package avoid importing the rest of Open WebUI so they can
be tested on their own.
"""
