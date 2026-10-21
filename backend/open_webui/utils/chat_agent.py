"""A chat's custom agent: instructions its owner chose from a project folder on their own computer.

The chat keeps a copy of those instructions under 'agent', and the chat page sends them
with every message, where a Full-access plan follows them on the owner's computer. So the
agent stays with the person who chose it:

- a chat copied from someone else, or brought in from a file, starts without one, since
  its new owner never chose those instructions. It also loses its hidden system prompt
  ('params.system', and the older top-level 'system'), which the page sends the same way,
  and its Ultra setting ('ultra'), since its new owner never chose to spend their plan
  on workers;
- share links and other people reading the chat never see it, since the instructions
  come from the owner's private project files. They also list each reply's Ultra workers
  without what they were doing or what they found, which can name private project
  details too.
"""

AGENT_KEY = 'agent'
ULTRA_KEY = 'ultra'
# Top-level keys that only a copy of one's own chat keeps, besides the agent:
# the older hidden system prompt and the Ultra setting.
OWNER_ONLY_KEYS = ('system', ULTRA_KEY)
# What a share link keeps of each Ultra worker: its label, state, model, effort and
# token count, and what it takes to list it under its parent with how long it ran.
SHARED_WORKER_FIELDS = (
    'id',
    'parent_id',
    'depth',
    'label',
    'state',
    'model',
    'effort',
    'tokens',
    'started_at',
    'ended_at',
)
WORKER_RUNNING = 'running'


def without_agent(chat):
    """The chat's contents without its custom agent; anything that isn't a dict is returned as is."""
    if not isinstance(chat, dict):
        return chat
    return {key: value for key, value in chat.items() if key != AGENT_KEY}


def without_hidden_instructions(chat):
    """The chat's contents without its custom agent, system prompt or Ultra setting.

    Anything that isn't a dict is returned as is.
    """
    if not isinstance(chat, dict):
        return chat
    contents = {key: value for key, value in without_agent(chat).items() if key not in OWNER_ONLY_KEYS}
    params = contents.get('params')
    if isinstance(params, dict):
        contents['params'] = {key: value for key, value in params.items() if key != 'system'}
    return contents


def contents_for_owner(chat: dict, source_owner_id: str | None, owner_id: str) -> dict:
    """The chat's contents for a copy that owner_id will own.

    source_owner_id is who owned the chat it was copied from, or None for a chat
    brought in from a file. Only a copy of one's own chat keeps its agent, system prompt
    and Ultra setting.
    """
    if source_owner_id is not None and source_owner_id == owner_id:
        return chat
    return without_hidden_instructions(chat)


def contents_for_share(chat):
    """The chat's contents for a share link, or for anyone but its owner who reads the chat.

    They leave out the custom agent, and each reply's Ultra workers keep only
    SHARED_WORKER_FIELDS, in the history and in the list of the shown branch.
    Anything that isn't a dict is returned as is.
    """
    if not isinstance(chat, dict):
        return chat
    contents = without_agent(chat)

    history = contents.get('history')
    if isinstance(history, dict) and isinstance(history.get('messages'), dict):
        messages = {}
        for message_id, message in history['messages'].items():
            messages[message_id] = _message_for_share(message)
        contents['history'] = {**history, 'messages': messages}

    if isinstance(contents.get('messages'), list):
        contents['messages'] = [_message_for_share(message) for message in contents['messages']]
    return contents


def _message_for_share(message):
    """The message for a share link, with its Ultra workers reduced to SHARED_WORKER_FIELDS."""
    if not isinstance(message, dict) or 'workers' not in message:
        return message
    shared = {key: value for key, value in message.items() if key != 'workers'}
    workers = message['workers']
    # Anything that isn't a list of workers is left out rather than shared as it is.
    if isinstance(workers, dict) and isinstance(workers.get('items'), list):
        shared['workers'] = _workers_for_share(workers['items'])
    return shared


def _workers_for_share(items: list) -> dict:
    """The workers snapshot for a share link: each worker's shared fields, and how many were running."""
    shared_items = []
    running = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        shared_items.append({field: item[field] for field in SHARED_WORKER_FIELDS if field in item})
        if item.get('state') == WORKER_RUNNING:
            running += 1
    return {'items': shared_items, 'running': running}
