"""Offline tests: the instructions that make an Ultra turn orchestrate workers.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_notes.py -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from open_webui.utils.subscriptions import claude_code, codex, ultra  # noqa: E402
from open_webui.utils.subscriptions.common import (  # noqa: E402
    ACCESS_CHAT,
    ACCESS_FULL,
    ACCESS_LEVELS,
    ACCESS_READ,
    UltraOptions,
)
from open_webui.utils.subscriptions.events import StatusUpdate  # noqa: E402

PROVIDERS = (claude_code.PROVIDER_ID, codex.PROVIDER_ID)
SONNET = UltraOptions(worker_model='sonnet', worker_label='Claude Sonnet 4.6', worker_effort='high')
ASTRA = UltraOptions(worker_model='gpt-6-astra', worker_label='GPT-6 Astra', worker_effort='xhigh')


def max_workers_with(value: str | None) -> int | None:
    environment = dict(os.environ)
    environment.pop(ultra.MAX_WORKERS_ENV, None)
    if value is not None:
        environment[ultra.MAX_WORKERS_ENV] = value
    with patch.dict(os.environ, environment, clear=True):
        return ultra.max_workers()


class MaxWorkersTests(unittest.TestCase):
    def test_unset_means_no_limit(self):
        self.assertIsNone(max_workers_with(None))
        self.assertIsNone(max_workers_with(''))
        self.assertIsNone(max_workers_with('   '))

    def test_a_whole_number_is_the_limit(self):
        self.assertEqual(max_workers_with('4'), 4)
        self.assertEqual(max_workers_with(' 12 '), 12)

    def test_anything_below_one_counts_as_one(self):
        self.assertEqual(max_workers_with('0'), 1)
        self.assertEqual(max_workers_with('-3'), 1)

    def test_a_value_that_is_not_a_whole_number_is_ignored(self):
        for value in ('many', '2.5', '4 workers', 'none'):
            with self.subTest(value=value):
                with self.assertLogs(ultra.log, level='WARNING'):
                    self.assertIsNone(max_workers_with(value))


class OrchestratorNoteTests(unittest.TestCase):
    def test_provider_ids_match_the_engines(self):
        self.assertEqual(ultra.CLAUDE_PROVIDER_ID, claude_code.PROVIDER_ID)
        self.assertEqual(ultra.CODEX_PROVIDER_ID, codex.PROVIDER_ID)

    def test_every_provider_and_access_level_delegates_in_stages(self):
        for provider_id in PROVIDERS:
            for access in ACCESS_LEVELS:
                with self.subTest(provider=provider_id, access=access):
                    note = ultra.orchestrator_note(provider_id, access, UltraOptions())
                    self.assertIn('Ultra is on', note)
                    self.assertIn('instead of doing the work yourself', note)
                    self.assertIn("Workers don't see this chat", note)
                    self.assertIn('1. Research.', note)
                    self.assertIn('2. Fix or build.', note)
                    self.assertIn('3. Verify.', note)
                    self.assertIn('After each fix, start fresh, independent workers that did not write it', note)
                    self.assertIn('try to prove it wrong', note)
                    self.assertIn('4. Repeat.', note)
                    self.assertIn('until verification passes', note)
                    self.assertIn('only one level down', note)
                    self.assertIn('Reply directly, without workers, to greetings', note)

    def test_stages_come_in_order(self):
        for provider_id in PROVIDERS:
            with self.subTest(provider=provider_id):
                note = ultra.orchestrator_note(provider_id, ACCESS_FULL, UltraOptions())
                stages = [note.index(f'{number}. ') for number in (1, 2, 3, 4)]
                self.assertEqual(stages, sorted(stages))

    def test_workers_have_the_turns_access(self):
        wording = {
            ACCESS_CHAT: 'they can only think: they have no files, commands or web',
            ACCESS_READ: 'they can read the project but not change it',
            ACCESS_FULL: 'they can read and edit the project and run commands',
        }
        for provider_id in PROVIDERS:
            for access, expected in wording.items():
                with self.subTest(provider=provider_id, access=access):
                    note = ultra.orchestrator_note(provider_id, access, UltraOptions())
                    self.assertIn('Workers have the same access as you', note)
                    self.assertIn(expected, note)
                    for other_access, other in wording.items():
                        if other_access != access:
                            self.assertNotIn(other, note)

    def test_claude_names_its_worker_types_and_runs_workers_in_one_message(self):
        for access in ACCESS_LEVELS:
            with self.subTest(access=access):
                note = ultra.orchestrator_note(claude_code.PROVIDER_ID, access, UltraOptions())
                self.assertIn('several Agent calls in one message', note)
                self.assertIn('the Agent tool', note)
                self.assertIn('buddy-worker', note)
                self.assertNotIn('spawn_agent', note)
                self.assertNotIn('Buddy stops any worker started deeper', note)

    def test_claude_offers_buddy_reader_only_when_there_is_something_to_read(self):
        for access in (ACCESS_READ, ACCESS_FULL):
            with self.subTest(access=access):
                note = ultra.orchestrator_note(claude_code.PROVIDER_ID, access, UltraOptions())
                self.assertIn('Use the buddy-reader type for a worker that only needs to read', note)
        chat_note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_CHAT, UltraOptions())
        self.assertNotIn('buddy-reader', chat_note)
        self.assertIn('Start every worker with the Agent tool and the buddy-worker type.', chat_note)

    def test_codex_uses_its_collaboration_tools(self):
        for access in ACCESS_LEVELS:
            with self.subTest(access=access):
                note = ultra.orchestrator_note(codex.PROVIDER_ID, access, UltraOptions())
                self.assertIn('Start workers with spawn_agent', note)
                self.assertIn('your wait tool', note)
                self.assertIn('close each worker once it is done', note)
                self.assertIn('before you wait for any', note)
                self.assertNotIn('Agent tool', note)
                self.assertNotIn('Agent calls', note)
                self.assertNotIn('buddy-worker', note)
                self.assertNotIn('buddy-reader', note)

    def test_codex_relays_the_depth_limit_it_does_not_enforce(self):
        note = ultra.orchestrator_note(codex.PROVIDER_ID, ACCESS_FULL, UltraOptions())
        self.assertIn("Buddy stops any worker started deeper than that, so say this in each worker's task.", note)

    def test_codex_asks_for_read_only_conduct_only_with_full_access(self):
        request = 'When a worker only needs to read, tell it not to change anything.'
        full_note = ultra.orchestrator_note(codex.PROVIDER_ID, ACCESS_FULL, UltraOptions())
        self.assertIn(request, full_note)
        for access in (ACCESS_CHAT, ACCESS_READ):
            with self.subTest(access=access):
                self.assertNotIn(request, ultra.orchestrator_note(codex.PROVIDER_ID, access, UltraOptions()))
        claude_note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_FULL, UltraOptions())
        self.assertNotIn(request, claude_note)

    def test_no_cap_means_no_fixed_limit(self):
        for provider_id in PROVIDERS:
            for access in ACCESS_LEVELS:
                with self.subTest(provider=provider_id, access=access):
                    note = ultra.orchestrator_note(provider_id, access, UltraOptions())
                    self.assertIn('Use as many workers as the work needs.', note)
                    self.assertIn('There is no fixed limit, but every worker should earn its place.', note)
                    self.assertNotIn('at once, so', note)
                    self.assertNotIn('at a time', note)

    def test_a_cap_is_mentioned_only_when_set(self):
        for provider_id in PROVIDERS:
            for access in ACCESS_LEVELS:
                with self.subTest(provider=provider_id, access=access):
                    note = ultra.orchestrator_note(provider_id, access, UltraOptions(max_workers=3))
                    self.assertIn('every worker should earn its place', note)
                    self.assertIn('At most 3 workers can run at once, so start them in groups of 3 or fewer.', note)
                    self.assertNotIn('no fixed limit', note)

    def test_a_cap_of_one_runs_workers_one_after_another(self):
        note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_READ, UltraOptions(max_workers=1))
        self.assertIn('Only one worker can run at a time, so start them one after another.', note)
        self.assertNotIn('At most', note)
        self.assertNotIn('no fixed limit', note)

    def test_without_a_chosen_model_workers_use_the_chats_own(self):
        for access in ACCESS_LEVELS:
            with self.subTest(access=access):
                claude_note = ultra.orchestrator_note(claude_code.PROVIDER_ID, access, UltraOptions())
                self.assertIn('The person chose your own model and thinking level for the workers.', claude_note)
                self.assertIn("Don't choose a different model or thinking level for a worker.", claude_note)
                codex_note = ultra.orchestrator_note(codex.PROVIDER_ID, access, UltraOptions())
                self.assertIn('The person chose your own model and thinking level for the workers.', codex_note)
                self.assertIn('Start workers without a model or reasoning effort, so they use yours.', codex_note)
                self.assertNotIn('Start every worker with model', codex_note)

    def test_claude_names_the_chosen_model_and_thinking_level(self):
        for access in ACCESS_LEVELS:
            with self.subTest(access=access):
                note = ultra.orchestrator_note(claude_code.PROVIDER_ID, access, SONNET)
                self.assertIn('The person chose Claude Sonnet 4.6 at the high thinking level for the workers.', note)
                self.assertIn("Don't choose a different model or thinking level for a worker.", note)
                self.assertNotIn('your own model', note)

    def test_codex_passes_the_chosen_model_and_effort_to_every_spawn(self):
        for access in ACCESS_LEVELS:
            with self.subTest(access=access):
                note = ultra.orchestrator_note(codex.PROVIDER_ID, access, ASTRA)
                self.assertIn('The person chose GPT-6 Astra at the xhigh thinking level for the workers.', note)
                self.assertIn('Start every worker with model `gpt-6-astra` and reasoning effort `xhigh`.', note)
                self.assertNotIn('without a model', note)

    def test_a_chosen_model_alone_keeps_the_chats_thinking_level(self):
        options = UltraOptions(worker_model='gpt-6-astra', worker_label='GPT-6 Astra')
        note = ultra.orchestrator_note(codex.PROVIDER_ID, ACCESS_FULL, options)
        self.assertIn('The person chose GPT-6 Astra for the workers, at your own thinking level.', note)
        self.assertIn('Start every worker with model `gpt-6-astra`.', note)
        self.assertNotIn('reasoning effort `', note)

    def test_a_chosen_thinking_level_alone_keeps_the_chats_model(self):
        options = UltraOptions(worker_effort='low')
        claude_note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_CHAT, options)
        self.assertIn('The person chose the low thinking level for the workers, on your own model.', claude_note)
        codex_note = ultra.orchestrator_note(codex.PROVIDER_ID, ACCESS_CHAT, options)
        self.assertIn('Start every worker with reasoning effort `low`.', codex_note)
        self.assertNotIn('model `', codex_note)

    def test_a_model_without_a_name_is_named_by_its_id(self):
        options = UltraOptions(worker_model='opus', worker_effort='max')
        note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_FULL, options)
        self.assertIn('The person chose opus at the max thinking level for the workers.', note)

    def test_another_provider_gets_the_note_without_tool_names(self):
        note = ultra.orchestrator_note('future-engine', ACCESS_FULL, ASTRA)
        self.assertIn('instead of doing the work yourself', note)
        self.assertIn('Start them together so they run at the same time.', note)
        self.assertIn('When a worker only needs to read, tell it not to change anything.', note)
        self.assertIn("Don't choose a different model or thinking level for a worker.", note)
        for tool_name in ('Agent', 'buddy-worker', 'buddy-reader', 'spawn_agent', 'wait tool'):
            self.assertNotIn(tool_name, note)

    def test_an_unknown_access_level_gets_the_narrowest_wording(self):
        note = ultra.orchestrator_note(claude_code.PROVIDER_ID, 'everything', UltraOptions())
        self.assertIn('they can only think', note)
        self.assertNotIn('buddy-reader', note)


class NotePlacementTests(unittest.TestCase):
    def test_the_note_leads_the_turns_instructions(self):
        note = ultra.orchestrator_note(claude_code.PROVIDER_ID, ACCESS_READ, SONNET)
        noted = ultra.with_orchestrator_note('Folder instructions.', claude_code.PROVIDER_ID, ACCESS_READ, SONNET)
        self.assertEqual(noted, f'{note}\n\nFolder instructions.')
        self.assertEqual(ultra.with_orchestrator_note('', claude_code.PROVIDER_ID, ACCESS_READ, SONNET), note)

    def test_taking_the_note_off_gives_back_the_chats_own_instructions(self):
        for provider_id in PROVIDERS:
            for system in ('', 'Folder instructions.', 'A project agent.\n\nFolder instructions.'):
                with self.subTest(provider_id=provider_id, system=system):
                    noted = ultra.with_orchestrator_note(system, provider_id, ACCESS_FULL, ASTRA)
                    self.assertEqual(ultra.without_orchestrator_note(noted, provider_id, ACCESS_FULL, ASTRA), system)

    def test_instructions_without_the_note_stay_as_they_are(self):
        self.assertEqual(
            ultra.without_orchestrator_note('Folder instructions.', claude_code.PROVIDER_ID, ACCESS_CHAT, SONNET),
            'Folder instructions.',
        )

    def test_the_off_status_names_the_reason(self):
        reason = 'Claude Code on Fixture PC is too old for Ultra.'
        self.assertEqual(
            ultra.OFF_STATUS.format(reason=reason),
            'Ultra is off for this reply: Claude Code on Fixture PC is too old for Ultra.',
        )

    def test_the_off_status_line_stays_shown_once_the_reply_ends(self):
        reason = 'Claude Code on Fixture PC is too old for Ultra.'
        self.assertEqual(
            ultra.off_status(reason),
            StatusUpdate('Ultra is off for this reply: Claude Code on Fixture PC is too old for Ultra.', keep=True),
        )


class WorkerPromptTests(unittest.TestCase):
    def test_workers_stick_to_their_task_and_report_what_they_checked(self):
        prompt = ultra.WORKER_PROMPT
        self.assertIn('Do only your task.', prompt)
        self.assertIn('report concisely what you did and what you found', prompt)
        self.assertIn('If your task is to verify work, try to refute it', prompt)
        self.assertIn('Never claim that something works, passes or is fixed unless you checked it.', prompt)

    def test_the_turn_note_is_one_short_line(self):
        self.assertEqual(ultra.ULTRA_TURN_NOTE, 'Ultra is on: orchestrate workers for this request.')

    def test_worker_types_and_depth(self):
        self.assertEqual(ultra.WORKER_AGENT, 'buddy-worker')
        self.assertEqual(ultra.READER_AGENT, 'buddy-reader')
        self.assertEqual(ultra.MAX_DEPTH, 2)


if __name__ == '__main__':
    unittest.main()
