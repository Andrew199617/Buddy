"""A chat's custom agent stays with the person who chose it.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_chat_agent.py -v
"""

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'utils' / 'chat_agent.py'
spec = importlib.util.spec_from_file_location('chat_agent', MODULE_PATH)
chat_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat_agent)

AGENT = {
    'name': 'Reviewer',
    'description': 'Reviews changes',
    'path': '.github/agents/reviewer.agent.md',
    'instructions': 'Review the diff.',
}
CHAT = {
    'title': 'Plan',
    'system': 'Be brief.',
    'params': {'system': 'Be brief.', 'temperature': 0.2},
    'history': {},
    'agent': AGENT,
}


class WithoutAgentTests(unittest.TestCase):
    def test_leaves_out_only_the_agent(self):
        shared = chat_agent.without_agent(CHAT)
        self.assertNotIn('agent', shared)
        self.assertEqual(shared, {key: value for key, value in CHAT.items() if key != 'agent'})
        self.assertEqual(CHAT['agent'], AGENT)

    def test_passes_through_contents_that_are_not_a_dict(self):
        self.assertIsNone(chat_agent.without_agent(None))


class ContentsForOwnerTests(unittest.TestCase):
    def test_a_copy_of_ones_own_chat_keeps_its_agent(self):
        self.assertEqual(chat_agent.contents_for_owner(CHAT, 'owner', 'owner'), CHAT)

    def test_a_copy_of_someone_elses_chat_starts_without_one(self):
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('agent', copied)
        self.assertEqual(copied['title'], 'Plan')

    def test_a_copy_of_someone_elses_chat_loses_its_hidden_system_prompt(self):
        # The page sends params.system as the system message, which a Full-access plan follows.
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('system', copied)
        self.assertEqual(copied['params'], {'temperature': 0.2})

    def test_a_chat_brought_in_from_a_file_starts_without_one(self):
        imported = chat_agent.contents_for_owner(CHAT, None, 'owner')
        self.assertNotIn('agent', imported)
        self.assertNotIn('system', imported)
        self.assertNotIn('system', imported['params'])

    def test_a_copy_of_ones_own_chat_keeps_its_system_prompt(self):
        own = chat_agent.contents_for_owner(CHAT, 'owner', 'owner')
        self.assertEqual(own['params']['system'], 'Be brief.')

    def test_the_original_chat_is_left_as_it_was(self):
        chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertEqual(CHAT['agent'], AGENT)
        self.assertEqual(CHAT['params'], {'system': 'Be brief.', 'temperature': 0.2})
        self.assertEqual(CHAT['system'], 'Be brief.')

    def test_a_chat_without_an_agent_is_unchanged(self):
        plain = {'title': 'Plan'}
        self.assertEqual(chat_agent.contents_for_owner(plain, None, 'owner'), plain)

    def test_params_that_are_not_a_dict_are_left_alone(self):
        odd = {'title': 'Plan', 'params': None}
        self.assertEqual(chat_agent.contents_for_owner(odd, None, 'owner'), odd)


if __name__ == '__main__':
    unittest.main()
