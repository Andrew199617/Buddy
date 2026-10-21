"""Ultra turns, where the plan turn orchestrates workers instead of doing the work itself.

The person turns Ultra on in the composer and may choose the workers' model and
thinking level. Buddy adds orchestrator_note() to the turn's instructions and
ULTRA_TURN_NOTE to the person's message on the server, so neither is saved with
the chat. Workers get the turn's own access; the orchestrator may start one with
narrower access, but never broader. The orchestrator's workers may start
sub-workers, and sub-workers may not start any.
"""

import logging
import os

from open_webui.utils.subscriptions.common import ACCESS_FULL, ACCESS_READ, UltraOptions

log = logging.getLogger(__name__)

# The admin's optional cap on workers at once. Unset means no limit.
MAX_WORKERS_ENV = 'BUDDY_ULTRA_MAX_WORKERS'

# The orchestrator's workers are depth 1 and their sub-workers depth 2.
MAX_DEPTH = 2

# The ids of claude_code.PROVIDER_ID and codex.PROVIDER_ID. Both modules import
# this one, so it can't import them.
CLAUDE_PROVIDER_ID = 'claude'
CODEX_PROVIDER_ID = 'codex'

# The Claude Code subagent types Buddy defines on Ultra turns. A buddy-worker
# has the turn's own tools; a buddy-reader can only read and search.
WORKER_AGENT = 'buddy-worker'
READER_AGENT = 'buddy-reader'

# Added to the person's message on Ultra turns.
ULTRA_TURN_NOTE = 'Ultra is on: orchestrate workers for this request.'

# The status line that starts a reply the person turned Ultra on for, when it runs without Ultra.
OFF_STATUS = 'Ultra is off for this reply: {reason}'

# The system prompt of buddy-worker and buddy-reader.
WORKER_PROMPT = '\n'.join(
    [
        'You are a worker in Buddy, a personal chat app. An orchestrating agent gave you one task '
        'that is part of a larger request.',
        '',
        "- Do only your task. Don't widen it or take on other parts of the request.",
        '- When you finish, report concisely what you did and what you found. Your final message is '
        'all the orchestrator sees, so include the evidence it needs, such as file paths and the '
        'results of commands you ran.',
        '- If your task is to verify work, try to refute it: look for the cases where it fails, and '
        'say plainly when it does.',
        "- Never claim that something works, passes or is fixed unless you checked it. Say what you couldn't check.",
    ]
)

ORCHESTRATOR_INTRO = (
    'Ultra is on. The person turned it on to have you orchestrate workers for their request '
    'instead of doing the work yourself: plan the work, hand it to workers, check what they report '
    "and write the reply. Workers don't see this chat, so give each one everything it needs: its "
    'task, the context and what to report back.'
)

CODEX_TOOLS = (
    'Start workers with spawn_agent, collect their results with your wait tool, and close each '
    'worker once it is done.'
)

DEPTH_NOTE = (
    'Your workers may start sub-workers of their own when their task splits into independent parts, '
    "but only one level down: sub-workers do their work themselves and can't start more."
)

# Codex doesn't limit how deep workers go, so Buddy stops deeper ones itself.
CODEX_DEPTH_NOTE = "Buddy stops any worker started deeper than that, so say this in each worker's task."

DIRECT_REPLY_NOTE = 'Reply directly, without workers, to greetings, thanks and questions you can answer at once.'


def max_workers() -> int | None:
    """The admin's cap on workers at once, or None for no limit.

    Read from BUDDY_ULTRA_MAX_WORKERS. A value that isn't a whole number is
    ignored, and anything below 1 counts as 1.
    """
    text = os.environ.get(MAX_WORKERS_ENV, '').strip()
    if not text:
        return None
    try:
        limit = int(text)
    except ValueError:
        log.warning('Ignoring %s=%r: it is not a whole number, so Ultra has no worker limit.', MAX_WORKERS_ENV, text)
        return None
    return max(limit, 1)


def _parallel_hint(provider_id: str) -> str:
    if provider_id == CLAUDE_PROVIDER_ID:
        return 'Put several Agent calls in one message so they run at the same time.'
    if provider_id == CODEX_PROVIDER_ID:
        return 'Call spawn_agent for each of them before you wait for any, so they run at the same time.'
    return 'Start them together so they run at the same time.'


def _stages(provider_id: str) -> str:
    research = (
        "1. Research. Start workers for the questions that don't depend on each other, all at once. "
        f'{_parallel_hint(provider_id)}'
    )
    fix = (
        '2. Fix or build. Once you know what to do, start workers to do it: to make the changes, or to '
        "write them up when workers can't change the project."
    )
    verify = (
        '3. Verify. After each fix, start fresh, independent workers that did not write it to check it, '
        'by running tests, reviewing it or reproducing the problem. Ask them to try to prove it wrong.'
    )
    repeat = (
        '4. Repeat. While verification fails, start workers to fix what it found, then verify again '
        'with fresh workers, until verification passes.'
    )
    return '\n'.join(['Work in stages:', research, fix, verify, repeat])


def _worker_count(limit: int | None) -> str:
    if limit is None:
        return 'Use as many workers as the work needs. There is no fixed limit, but every worker should earn its place.'
    if limit == 1:
        at_once = 'Only one worker can run at a time, so start them one after another.'
    else:
        at_once = f'At most {limit} workers can run at once, so start them in groups of {limit} or fewer.'
    return f'Use as many workers as the work needs, but every worker should earn its place. {at_once}'


def _access_sentence(access: str) -> str:
    if access == ACCESS_FULL:
        return 'Workers have the same access as you: they can read and edit the project and run commands.'
    if access == ACCESS_READ:
        return 'Workers have the same access as you: they can read the project but not change it.'
    return 'Workers have the same access as you, so they can only think: they have no files, commands or web.'


def _claude_worker_types(access: str) -> str:
    if access == ACCESS_FULL or access == ACCESS_READ:
        return (
            f'Start workers with the Agent tool. Use the {READER_AGENT} type for a worker that only needs '
            f'to read; it can only read and search. Use {WORKER_AGENT} for the rest, including any worker '
            'that may need sub-workers.'
        )
    return f'Start every worker with the Agent tool and the {WORKER_AGENT} type.'


def _access_paragraph(provider_id: str, access: str) -> str:
    sentences = [_access_sentence(access)]
    if provider_id == CLAUDE_PROVIDER_ID:
        sentences.append(_claude_worker_types(access))
    elif access == ACCESS_FULL:
        # Other providers' workers share the turn's sandbox, so Buddy can only ask.
        sentences.append('When a worker only needs to read, tell it not to change anything.')
    return ' '.join(sentences)


def _depth_paragraph(provider_id: str) -> str:
    if provider_id == CODEX_PROVIDER_ID:
        return f'{DEPTH_NOTE} {CODEX_DEPTH_NOTE}'
    return DEPTH_NOTE


def _worker_choice(options: UltraOptions) -> str:
    """The person's choice of worker model and thinking level, in one sentence."""
    label = options.worker_label or options.worker_model
    effort = options.worker_effort
    if options.worker_model and effort:
        return f'The person chose {label} at the {effort} thinking level for the workers.'
    if options.worker_model:
        return f'The person chose {label} for the workers, at your own thinking level.'
    if effort:
        return f'The person chose the {effort} thinking level for the workers, on your own model.'
    return 'The person chose your own model and thinking level for the workers.'


def _codex_spawn_settings(options: UltraOptions) -> str:
    """How Codex should start workers, since Buddy can't set their model for it."""
    settings = []
    if options.worker_model:
        settings.append(f'model `{options.worker_model}`')
    if options.worker_effort:
        settings.append(f'reasoning effort `{options.worker_effort}`')
    if not settings:
        return 'Start workers without a model or reasoning effort, so they use yours.'
    return f'Start every worker with {" and ".join(settings)}.'


def _choice_paragraph(provider_id: str, options: UltraOptions) -> str:
    if provider_id == CODEX_PROVIDER_ID:
        instruction = _codex_spawn_settings(options)
    else:
        instruction = "Don't choose a different model or thinking level for a worker."
    return f'{_worker_choice(options)} {instruction}'


def orchestrator_note(provider_id: str, access: str, options: UltraOptions) -> str:
    """Instructions that make an Ultra turn orchestrate workers, added to the turn's own.

    provider_id is 'claude' or 'codex'; another provider gets the same note
    without tool names. access is the turn's access level.
    """
    paragraphs = [
        ORCHESTRATOR_INTRO,
        _stages(provider_id),
        _worker_count(options.max_workers),
    ]
    if provider_id == CODEX_PROVIDER_ID:
        paragraphs.append(CODEX_TOOLS)
    paragraphs.append(_access_paragraph(provider_id, access))
    paragraphs.append(_depth_paragraph(provider_id))
    paragraphs.append(_choice_paragraph(provider_id, options))
    paragraphs.append(DIRECT_REPLY_NOTE)
    return '\n\n'.join(paragraphs)


def with_orchestrator_note(system: str, provider_id: str, access: str, options: UltraOptions) -> str:
    """A turn's instructions with orchestrator_note() ahead of them."""
    note = orchestrator_note(provider_id, access, options)
    if not system:
        return note
    return f'{note}\n\n{system}'


def without_orchestrator_note(system: str, provider_id: str, access: str, options: UltraOptions) -> str:
    """A turn's instructions without the note that with_orchestrator_note() put ahead of them.

    Workers get the chat's own instructions without it, since they do their
    task themselves, and so does a reply that runs without Ultra after all.
    """
    note = orchestrator_note(provider_id, access, options)
    if system == note:
        return ''
    return system.removeprefix(f'{note}\n\n')
