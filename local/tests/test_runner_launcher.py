"""Verify the Windows Buddy Runner launchers start Python safely."""

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
CMD_WRAPPER = REPOSITORY / 'local' / 'start-buddy-runner.cmd'
RUNNER_MODULE = 'open_webui.utils.subscriptions.runner'

# Stands in for python.exe: prints its arguments and the Python environment it
# was given, then exits with FAKE_PYTHON_EXIT_CODE.
FAKE_PYTHON = '''param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RunnerPassedArguments)
ConvertTo-Json -Compress -InputObject @{
    arguments = @($RunnerPassedArguments)
    pythonPath = $env:PYTHONPATH
    encoding = $env:PYTHONIOENCODING
}
exit [int]$env:FAKE_PYTHON_EXIT_CODE
'''

# Starts the launcher from a PowerShell session that has its own Python
# settings, then prints those settings and the launcher's exit code.
INTERACTIVE_CALLER = '''param([string]$Launcher, [string]$PythonPath, [string]$StateDir)
$env:PYTHONPATH = 'caller-entry'
$env:PYTHONIOENCODING = $null
& $Launcher -PythonPath $PythonPath -StateDir $StateDir
ConvertTo-Json -Compress -InputObject @{
    pythonPath = $env:PYTHONPATH
    encoding = $env:PYTHONIOENCODING
    exitCode = $LASTEXITCODE
}
'''


@unittest.skipUnless(os.name == 'nt', 'The PowerShell Runner launcher is tested on Windows')
class RunnerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.powershell = shutil.which('pwsh') or shutil.which('powershell')
        if self.powershell is None:
            self.skipTest('PowerShell is unavailable')
        directory = tempfile.TemporaryDirectory(prefix='buddy-runner-launcher-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.stub = self.directory / 'fake python.ps1'
        self.stub.write_text(FAKE_PYTHON, encoding='utf-8')
        self.state = self.directory / 'state dir'
        # An untrusted project folder with a module that shadows the stdlib.
        self.project = self.directory / 'project'
        self.project.mkdir()
        (self.project / 'argparse.py').write_text('print("planted argparse ran")\n', encoding='utf-8')

    def environment(self, **overrides):
        environment = dict(os.environ)
        environment.update(overrides)
        return environment

    def run_launcher(self):
        result = subprocess.run(
            [self.powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(LAUNCHER),
             '-PythonPath', str(self.stub), '-StateDir', str(self.state)],
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
        arguments = self.run_launcher()['arguments']
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

    def test_launcher_restores_the_callers_python_settings(self):
        caller = self.directory / 'interactive caller.ps1'
        caller.write_text(INTERACTIVE_CALLER, encoding='utf-8')
        result = subprocess.run(
            [self.powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(caller),
             '-Launcher', str(LAUNCHER), '-PythonPath', str(self.stub), '-StateDir', str(self.state)],
            capture_output=True, text=True, timeout=30,
            env=self.environment(FAKE_PYTHON_EXIT_CODE='5'),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        runner_output, caller_output = result.stdout.strip().splitlines()

        runner = json.loads(runner_output)
        backend = str(REPOSITORY / 'backend')
        self.assertEqual(runner['pythonPath'], backend + os.pathsep + 'caller-entry')
        self.assertEqual(runner['encoding'], 'utf-8')

        after = json.loads(caller_output)
        self.assertEqual(after['pythonPath'], 'caller-entry')
        self.assertIsNone(after['encoding'])
        self.assertEqual(after['exitCode'], 5)

    def test_cmd_wrapper_runs_where_powershell_scripts_are_disabled(self):
        # The Process scope reads this variable, like a Restricted default.
        restricted = self.environment(PSExecutionPolicyPreference='Restricted', FAKE_PYTHON_EXIT_CODE='7')
        direct = subprocess.run(
            [self.powershell, '-NoProfile', '-File', str(LAUNCHER), '-PythonPath', str(self.stub)],
            capture_output=True, text=True, timeout=30, env=restricted,
        )
        if direct.returncode == 0:
            self.skipTest('Group Policy sets the execution policy on this computer')
        self.assertIn('disabled', direct.stderr)

        wrapper_command = subprocess.list2cmdline([
            str(CMD_WRAPPER), '-PythonPath', str(self.stub), '-StateDir', str(self.state), '-Port', '9123',
        ])
        # /s keeps cmd from stripping quotes inside the wrapper's command line.
        result = subprocess.run(
            f'cmd.exe /d /s /c "{wrapper_command}"',
            capture_output=True, text=True, timeout=30, env=restricted,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        arguments = json.loads(result.stdout.strip())['arguments']
        self.assertEqual(arguments[arguments.index('--state-dir') + 1], str(self.state))
        self.assertEqual(arguments[arguments.index('--port') + 1], '9123')


if __name__ == '__main__':
    unittest.main()
