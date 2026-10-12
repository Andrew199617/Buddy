"""Where a chat runs: reading the bar's request and working out the default target."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.utils.subscriptions import run_targets  # noqa: E402
from open_webui.utils.subscriptions.events import SubscriptionError  # noqa: E402

DefaultComputer = run_targets.DefaultComputer
DefaultFolderChoice = run_targets.DefaultFolderChoice
DefaultInputs = run_targets.DefaultInputs


def inputs(**overrides):
    values = {
        'folder_choice': None,
        'shared_instructions': False,
        'computers': (DefaultComputer('pc-1', True), DefaultComputer('laptop', False), DefaultComputer('local', True)),
        'this_machine_id': None,
        'last_used_machine_id': None,
        'plan_machine_id': 'local',
    }
    values.update(overrides)
    return DefaultInputs(**values)


def choice(mode, machine_id='', access='', paths=None):
    return DefaultFolderChoice(mode, machine_id, access, paths or {})


class RequestTests(unittest.TestCase):
    def test_a_computer_request_keeps_its_folder_and_access(self):
        request = run_targets.parse_request({
            'kind': 'computer', 'machine_id': 'pc-1', 'expected_revision': 'rev-1',
            'folder': {'mode': 'path', 'path': 'C:\\code\\site'}, 'access': 'full',
        })
        self.assertEqual((request.machine_id, request.folder.path, request.access), ('pc-1', 'C:\\code\\site', 'full'))

    def test_no_folder_lets_the_ai_choose(self):
        for folder in (None, {'mode': 'auto', 'path': 'C:\\ignored'}):
            with self.subTest(folder=folder):
                request = run_targets.parse_request({'kind': 'computer', 'machine_id': 'pc-1', 'folder': folder})
                self.assertEqual((request.folder.mode, request.folder.path), ('auto', None))

    def test_detached_is_always_chat_only_with_no_computer_or_folder(self):
        request = run_targets.parse_request({
            'kind': 'detached', 'machine_id': 'pc-1', 'access': 'full', 'folder': {'mode': 'path', 'path': 'C:\\x'},
        })
        self.assertEqual((request.machine_id, request.folder, request.access), (None, None, 'chat'))

    def test_unusable_requests_are_refused(self):
        refused = (
            {'kind': 'cloud'},
            {'kind': 'computer'},
            {'kind': 'computer', 'machine_id': ''},
            {'kind': 'computer', 'machine_id': 'pc-1', 'access': 'root'},
            {'kind': 'computer', 'machine_id': 'pc-1', 'folder': {'mode': 'path'}},
            {'kind': 'computer', 'machine_id': 'pc-1', 'owner_id': 'someone-else'},
            'computer',
        )
        for value in refused:
            with self.subTest(value=value):
                with self.assertRaises(SubscriptionError):
                    run_targets.parse_request(value)

    def test_the_lower_access_wins(self):
        self.assertEqual(run_targets.lowest_access('full', 'read'), 'read')
        self.assertEqual(run_targets.lowest_access('chat', 'full'), 'chat')
        self.assertEqual(run_targets.lowest_access('full', 'unknown'), 'chat')


class DefaultTests(unittest.TestCase):
    def test_a_new_chat_outside_chat_folders_runs_detached(self):
        default = run_targets.choose_default(inputs(this_machine_id='pc-1', last_used_machine_id='laptop'))
        self.assertEqual((default.kind, default.machine_id, default.access), ('detached', None, 'chat'))

    def test_a_chat_from_before_targets_keeps_running_where_its_plan_ran(self):
        default = run_targets.choose_default(inputs(), started_before_targets=True)
        self.assertEqual((default.kind, default.machine_id, default.folder_mode), ('computer', 'local', 'auto'))
        self.assertIsNone(default.access)
        self.assertEqual(default.came_from, 'plan')
        gone = run_targets.choose_default(inputs(plan_machine_id='removed-pc'), started_before_targets=True)
        self.assertEqual(gone.kind, 'detached')

    def test_a_chat_folder_with_no_files_runs_detached(self):
        default = run_targets.choose_default(inputs(folder_choice=choice('chat')))
        self.assertEqual((default.kind, default.came_from), ('detached', 'chat_folder'))

    def test_an_exact_folder_picks_a_computer_that_has_it(self):
        paths = {'laptop': 'D:\\site', 'pc-1': 'C:\\code\\site'}
        named = run_targets.choose_default(inputs(folder_choice=choice('folder', 'laptop', 'read', paths)))
        self.assertEqual((named.machine_id, named.path, named.access), ('laptop', 'D:\\site', 'read'))
        this_pc = run_targets.choose_default(inputs(folder_choice=choice('folder', paths=paths), this_machine_id='pc-1'))
        self.assertEqual((this_pc.machine_id, this_pc.path), ('pc-1', 'C:\\code\\site'))
        listed_first = run_targets.choose_default(inputs(folder_choice=choice('folder', paths=paths)))
        self.assertEqual(listed_first.machine_id, 'pc-1')
        nowhere = run_targets.choose_default(inputs(folder_choice=choice('folder', paths={'gone': 'C:\\x'})))
        self.assertEqual(nowhere.kind, 'detached')

    def test_a_folder_that_lets_the_ai_choose_needs_a_computer(self):
        named = run_targets.choose_default(inputs(folder_choice=choice('auto', 'laptop')))
        self.assertEqual((named.machine_id, named.folder_mode, named.came_from), ('laptop', 'auto', 'chat_folder'))
        steps = (
            ({'this_machine_id': 'laptop', 'last_used_machine_id': 'pc-1'}, 'laptop', 'this_pc'),
            ({'last_used_machine_id': 'pc-1'}, 'pc-1', 'last_used'),
            ({}, 'local', 'plan'),
            ({'plan_machine_id': None}, 'pc-1', 'first_ready'),
        )
        for overrides, machine_id, came_from in steps:
            with self.subTest(overrides=overrides):
                default = run_targets.choose_default(inputs(folder_choice=choice('auto'), **overrides))
                self.assertEqual((default.machine_id, default.came_from), (machine_id, came_from))
        none_ready = inputs(
            folder_choice=choice('auto'), plan_machine_id=None, computers=(DefaultComputer('laptop', False),)
        )
        self.assertEqual(run_targets.choose_default(none_ready).kind, 'detached')

    def test_shared_instructions_keep_a_default_chat_only(self):
        default = run_targets.choose_default(
            inputs(folder_choice=choice('auto', 'pc-1', 'full'), shared_instructions=True)
        )
        self.assertEqual((default.access, default.access_locked), ('chat', True))
        legacy = run_targets.choose_default(inputs(shared_instructions=True), started_before_targets=True)
        self.assertEqual(legacy.access, 'chat')


if __name__ == '__main__':
    unittest.main()
