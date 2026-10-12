"""A chat's custom agent: instructions its owner chose from a project folder on their own computer.

The chat keeps a copy of those instructions under 'agent', and the chat page sends them
with every message, where a Full-access plan follows them on the owner's computer. So the
agent stays with the person who chose it:

- a chat copied from someone else, or brought in from a file, starts without one, since
  its new owner never chose those instructions;
- share links and other people reading the chat never see it, since the instructions
  come from the owner's private project files.
"""

AGENT_KEY = 'agent'


def without_agent(chat):
    """The chat's contents without its custom agent; anything that isn't a dict is returned as is."""
    if not isinstance(chat, dict):
        return chat
    return {key: value for key, value in chat.items() if key != AGENT_KEY}


def contents_for_owner(chat: dict, source_owner_id: str | None, owner_id: str) -> dict:
    """The chat's contents for a copy that owner_id will own.

    source_owner_id is who owned the chat it was copied from, or None for a chat
    brought in from a file. Only a copy of one's own chat keeps its agent.
    """
    if source_owner_id is not None and source_owner_id == owner_id:
        return chat
    return without_agent(chat)
