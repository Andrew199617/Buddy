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
CHAT = {'title': 'Plan', 'params': {'system': 'Be brief.'}, 'history': {}, 'agent': AGENT}


class ContentsForOwnerTests(unittest.TestCase):
    def test_a_copy_of_ones_own_chat_keeps_its_agent(self):
        self.assertEqual(chat_agent.contents_for_owner(CHAT, 'owner', 'owner'), CHAT)

    def test_a_copy_of_someone_elses_chat_starts_without_one(self):
        copied = chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertNotIn('agent', copied)
        self.assertEqual(copied['params'], {'system': 'Be brief.'})
        self.assertEqual(copied['title'], 'Plan')

    def test_a_chat_brought_in_from_a_file_starts_without_one(self):
        self.assertNotIn('agent', chat_agent.contents_for_owner(CHAT, None, 'owner'))

    def test_the_original_chat_is_left_as_it_was(self):
        chat_agent.contents_for_owner(CHAT, 'someone-else', 'owner')
        self.assertEqual(CHAT['agent'], AGENT)

    def test_a_chat_without_an_agent_is_unchanged(self):
        plain = {'title': 'Plan'}
        self.assertEqual(chat_agent.contents_for_owner(plain, None, 'owner'), plain)


if __name__ == '__main__':
    unittest.main()
