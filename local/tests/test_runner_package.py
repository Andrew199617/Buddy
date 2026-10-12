"""The Runner package ships alone in the desktop app, so it must not need Open WebUI.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_runner_package.py -v
"""

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'backend' / 'buddy_runner'


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


class RunnerPackageBoundaryTests(unittest.TestCase):
    def test_no_module_imports_open_webui(self):
        offenders = []
        for path in sorted(PACKAGE.glob('*.py')):
            for name in imported_modules(path):
                if name == 'open_webui' or name.startswith('open_webui.'):
                    offenders.append(f'{path.name}: {name}')
        self.assertEqual(offenders, [])

    def test_importing_every_module_loads_no_open_webui_code(self):
        module_names = sorted(
            f'buddy_runner.{path.stem}' for path in PACKAGE.glob('*.py') if path.stem != '__main__'
        )
        script = (
            'import importlib, sys\n'
            f'for name in {module_names!r}:\n'
            '    importlib.import_module(name)\n'
            "print(sorted(name for name in sys.modules if name.split('.')[0] == 'open_webui'))\n"
        )
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(ROOT / 'backend')
        result = subprocess.run(
            [sys.executable, '-P', '-c', script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
