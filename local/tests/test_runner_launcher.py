"""Verify the Windows Buddy Runner launcher starts Python safely."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY / 'local' / 'start-buddy-runner.ps1'
RUNNER_MODULE = 'open_webui.utils.subscriptions.runner'


@unittest.skipUnless(os.name == 'nt', 'The PowerShell Runner launcher is tested on Windows')
class RunnerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.powershell = shutil.which('pwsh') or shutil.which('powershell')
        if self.powershell is None:
            self.skipTest('PowerShell is unavailable')
        directory = tempfile.TemporaryDirectory(prefix='buddy-runner-launcher-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        # Stands in for python.exe and prints the arguments it was given.
        self.stub = self.directory / 'fake python.ps1'
        self.stub.write_text(
            'param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RunnerPassedArguments)\n'
            'ConvertTo-Json -InputObject @($RunnerPassedArguments) -Compress\n'
            'exit 0\n',
            encoding='utf-8',
        )
        # An untrusted project folder with a module that shadows the stdlib.
        self.project = self.directory / 'project'
        self.project.mkdir()
        (self.project / 'argparse.py').write_text('print("planted argparse ran")\n', encoding='utf-8')

    def run_launcher(self):
        result = subprocess.run(
            [self.powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(LAUNCHER),
             '-PythonPath', str(self.stub), '-StateDir', str(self.directory / 'state')],
            cwd=self.project, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def import_argparse_from_project(self, interpreter_options):
        return subprocess.run(
            [sys.executable, *interpreter_options, '-c', 'import argparse; print(argparse.__file__)'],
            cwd=self.project, capture_output=True, text=True, timeout=30,
        )

    def test_python_starts_without_the_callers_folder_on_sys_path(self):
        arguments = self.run_launcher()
        module_index = arguments.index('-m')
        self.assertEqual(arguments[module_index + 1], RUNNER_MODULE)
        interpreter_options = arguments[:module_index]
        self.assertIn('-P', interpreter_options)

        # The project's module shadows the stdlib without those options...
        shadowed = self.import_argparse_from_project([])
        self.assertIn('planted argparse ran', shadowed.stdout)
        # ...and the launcher's options keep it from running.
        protected = self.import_argparse_from_project(interpreter_options)
        self.assertEqual(protected.returncode, 0, protected.stderr)
        self.assertNotIn('planted argparse ran', protected.stdout)
        self.assertNotIn(str(self.project), protected.stdout)


if __name__ == '__main__':
    unittest.main()
