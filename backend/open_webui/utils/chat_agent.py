"""A chat's custom agent: instructions its owner chose from a project folder on their own computer.

The chat keeps a copy of those instructions under 'agent', and the chat page sends them
with every message, where a Full-access plan follows them on the owner's computer. A chat
copied from someone else, or brought in from a file, could carry instructions its new
owner never chose, so such a copy starts without an agent.
"""

AGENT_KEY = 'agent'


def contents_for_owner(chat: dict, source_owner_id: str | None, owner_id: str) -> dict:
    """The chat's contents for a copy that owner_id will own.

    source_owner_id is who owned the chat it was copied from, or None for a chat
    brought in from a file. Only a copy of one's own chat keeps its agent.
    """
    if source_owner_id is not None and source_owner_id == owner_id:
        return chat
    return {key: value for key, value in chat.items() if key != AGENT_KEY}
