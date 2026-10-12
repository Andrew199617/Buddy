"""The Runner's connect mode: its connection, its private file, and how it stops.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_connect.py -v
"""

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from buddy_runner import connect, protocol  # noqa: E402
from buddy_runner.keys import read_private_text, write_private_text  # noqa: E402
from buddy_runner.runner import Runner  # noqa: E402
from buddy_runner.tunnel import PermanentDisconnect  # noqa: E402
from buddy_runner.workspace_capabilities import WorkspaceCapabilities  # noqa: E402

CONNECTION = {'server_url': 'https://buddy.example.com', 'machine_id': 'machine-1', 'credential': 'bdr1_fixture'}

# A Runner process that Buddy refuses at once, as when the computer was removed.
REMOVED_RUNNER = textwrap.dedent('''
    import sys
    sys.path.insert(0, sys.argv[1])
    from buddy_runner import connect, protocol
    from buddy_runner.tunnel import PermanentDisconnect

    async def removed(*args, **kwargs):
        raise PermanentDisconnect(protocol.CLOSE_UNKNOWN_MACHINE, 'removed')

    connect.run_tunnel = removed
    sys.exit(connect.main(['run', '--connection-stdin', '--state-dir', sys.argv[2]]))
''')


class ConnectionTests(unittest.TestCase):
    def test_a_complete_connection_is_accepted_and_normalized(self):
        parsed = connect.parse_connection(json.dumps({**CONNECTION, 'server_url': 'https://Buddy.Example.com/'}))
        self.assertEqual(parsed, CONNECTION)

    def test_incomplete_or_unsafe_connections_are_refused(self):
        refused = [
            'not json',
            '[]',
            json.dumps({**CONNECTION, 'credential': ''}),
            json.dumps({**CONNECTION, 'machine_id': None}),
            json.dumps({**CONNECTION, 'server_url': 'http://buddy.example.com'}),
        ]
        for text in refused:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    connect.parse_connection(text)


class PrivateFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-runner-connect-')
        self.addCleanup(directory.cleanup)
        # A state folder the helper creates itself, owner-only from the start.
        self.state_dir = Path(directory.name) / 'state'

    def test_the_connection_file_round_trips_and_is_replaced_whole(self):
        path = self.state_dir / 'connection.json'
        write_private_text(path, json.dumps(CONNECTION), state_dir=self.state_dir)
        self.assertEqual(json.loads(read_private_text(path, state_dir=self.state_dir)), CONNECTION)
        write_private_text(path, json.dumps({**CONNECTION, 'machine_id': 'machine-2'}), state_dir=self.state_dir)
        self.assertEqual(json.loads(read_private_text(path, state_dir=self.state_dir))['machine_id'], 'machine-2')
        self.assertEqual(sorted(entry.name for entry in self.state_dir.iterdir()), ['connection.json'])

    def test_a_missing_file_says_so(self):
        write_private_text(self.state_dir / 'other.json', '{}', state_dir=self.state_dir)
        with self.assertRaises(FileNotFoundError):
            read_private_text(self.state_dir / 'connection.json', state_dir=self.state_dir)

    def test_files_outside_the_state_folder_are_refused(self):
        with self.assertRaises(ValueError):
            write_private_text(self.state_dir / 'nested' / 'connection.json', '{}', state_dir=self.state_dir)


class ServeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='buddy-runner-serve-')
        self.addCleanup(directory.cleanup)
        self.runner = Runner(Path(directory.name), WorkspaceCapabilities())
        self.addAsyncCleanup(self.runner.close)

    async def serve_capturing(self, **options):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = await connect.serve(CONNECTION, self.runner, **options)
        statuses = [json.loads(line) for line in output.getvalue().splitlines()]
        return exit_code, [status['state'] for status in statuses]

    async def test_a_removed_machine_stops_with_its_own_exit_code(self):
        async def refused(*args, **kwargs):
            raise PermanentDisconnect(protocol.CLOSE_UNKNOWN_MACHINE, 'removed')

        with patch.object(connect, 'run_tunnel', refused):
            exit_code, states = await self.serve_capturing(watch_stdin=False)
        self.assertEqual(exit_code, connect.EXIT_REMOVED)
        self.assertEqual(states, ['removed'])
        self.assertTrue(self.runner.closing)

    async def test_a_stop_from_the_app_closes_the_runner_cleanly(self):
        async def stays_connected(*args, on_status=None, **kwargs):
            on_status('connected', 'Fixture PC')
            await asyncio.Event().wait()

        def stop_at_once(stop):
            stop.set()

        with patch.object(connect, 'run_tunnel', stays_connected), patch.object(connect, '_start_stdin_reader', stop_at_once):
            exit_code, states = await self.serve_capturing(watch_stdin=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(states[-1], 'stopped')
        self.assertTrue(self.runner.closing)


class StopCommandTests(unittest.TestCase):
    def test_only_a_stop_command_stops(self):
        self.assertTrue(connect._is_stop_command('{"command": "stop"}\n'))
        for line in ('garbage\n', '[]\n', '{"command": "other"}\n'):
            with self.subTest(line=line):
                self.assertFalse(connect._is_stop_command(line))


class RunnerProcessExitTests(unittest.TestCase):
    def test_a_permanent_close_exits_while_the_app_keeps_stdin_open(self):
        state = tempfile.TemporaryDirectory(prefix='buddy-runner-exit-')
        self.addCleanup(state.cleanup)
        child = subprocess.Popen(
            [sys.executable, '-B', '-c', REMOVED_RUNNER, str(ROOT / 'backend'), state.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Like the Buddy app: send the connection and keep stdin open.
        child.stdin.write(json.dumps(CONNECTION) + '\n')
        child.stdin.flush()
        try:
            exit_code = child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            exit_code = None
            child.kill()
        output, _ = child.communicate()
        self.assertEqual(exit_code, connect.EXIT_REMOVED, 'The Runner kept running after Buddy removed this computer.')
        self.assertIn('"removed"', output)


if __name__ == '__main__':
    unittest.main()
