"""Reading custom agent files from a project folder on This server.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_project_agents.py -v
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / 'backend' / 'open_webui' / 'utils' / 'subscriptions' / 'project_agents.py'
)
spec = importlib.util.spec_from_file_location('project_agents', MODULE_PATH)
project_agents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project_agents)


def write(root: Path, relative: str, data) -> Path:
    path = root.joinpath(*relative.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.write_bytes(data)
    return path


def link_directory(target: Path, link: Path) -> bool:
    """A symlink, or on Windows a junction, which needs no special privilege. False when neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name != 'nt':
        return False
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError:
        return False
    return True


class ReadAgentFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / 'site'
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def read(self):
        return project_agents.read_agent_files(str(self.project))

    def test_reads_the_agent_files_in_each_agent_folder(self):
        write(self.project, '.github/agents/reviewer.agent.md', '---\nname: Reviewer\n---\nReview.')
        write(self.project, '.github/agents/notes.txt', 'Not an agent.')
        write(self.project, '.claude/agents/tester.md', 'Test.')
        write(self.project, '.github/chatmodes/plan.chatmode.md', 'Plan.')
        write(self.project, '.github/chatmodes/README.md', 'Not a chat mode.')
        write(self.project, 'docs/other.agent.md', 'Outside the agent folders.')
        self.assertEqual(self.read(), {
            'files': [
                {'path': '.github/agents/reviewer.agent.md', 'content': '---\nname: Reviewer\n---\nReview.'},
                {'path': '.claude/agents/tester.md', 'content': 'Test.'},
                {'path': '.github/chatmodes/plan.chatmode.md', 'content': 'Plan.'},
            ],
            'left_out': 0,
        })

    def test_a_project_without_agents_has_none(self):
        self.assertEqual(self.read(), {'files': [], 'left_out': 0})

    def test_passes_over_files_that_are_too_large_or_not_text(self):
        write(self.project, '.github/agents/big.agent.md', b'x' * (project_agents.MAX_AGENT_BYTES + 1))
        write(self.project, '.github/agents/binary.agent.md', b'\xff\xfe\x00agent')
        write(self.project, '.github/agents/nul.agent.md', 'a\0b')
        write(self.project, '.github/agents/ok.agent.md', 'Fine.')
        self.assertEqual([file['path'] for file in self.read()['files']], ['.github/agents/ok.agent.md'])

    def test_reads_at_most_max_agent_files_and_counts_the_rest(self):
        for index in range(project_agents.MAX_AGENT_FILES + 2):
            write(self.project, f'.github/agents/agent-{index:03}.agent.md', 'Do it.')
        result = self.read()
        self.assertEqual(len(result['files']), project_agents.MAX_AGENT_FILES)
        self.assertEqual(result['left_out'], 2)

    def test_does_not_follow_an_agent_folder_that_leads_out_of_the_project(self):
        outside = self.base / 'outside'
        write(outside, 'secret.md', 'Private.')
        (self.project / '.claude').mkdir()
        if not link_directory(outside, self.project / '.claude' / 'agents'):
            self.skipTest('This account cannot create directory links.')
        self.assertEqual(self.read()['files'], [])

    def test_follows_an_agent_folder_linked_inside_the_project(self):
        write(self.project, 'shared-agents/helper.md', 'Help.')
        (self.project / '.claude').mkdir()
        if not link_directory(self.project / 'shared-agents', self.project / '.claude' / 'agents'):
            self.skipTest('This account cannot create directory links.')
        self.assertEqual(self.read()['files'], [{'path': '.claude/agents/helper.md', 'content': 'Help.'}])

    def test_does_not_follow_a_linked_agent_file(self):
        outside = write(self.base, 'outside/secret.md', 'Private.')
        (self.project / '.github' / 'agents').mkdir(parents=True)
        try:
            os.symlink(outside, self.project / '.github' / 'agents' / 'linked.agent.md')
        except OSError:
            self.skipTest('This account cannot create file links.')
        self.assertEqual(self.read()['files'], [])

    def test_a_missing_project_folder_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            project_agents.read_agent_files(str(self.base / 'gone'))


if __name__ == '__main__':
    unittest.main()
