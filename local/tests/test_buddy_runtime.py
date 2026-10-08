"""Verify Buddy launchers use tracked code and preserve existing user data."""

import ast
import importlib.util
import os
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

LOCAL_DIR = Path(__file__).resolve().parents[1]


class BuddyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fake_uvicorn = types.SimpleNamespace(run=lambda *args, **kwargs: None)
        fake_patches = types.SimpleNamespace(apply=lambda: None)
        module_stubs = {'uvicorn': fake_uvicorn, 'owui_local_patches': fake_patches}
        with patch.dict(sys.modules, module_stubs):
            spec = importlib.util.spec_from_file_location('buddy_serve_under_test', LOCAL_DIR / 'serve.py')
            self.serve = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.serve)
        self.serve.ROOT_DIR = self.root
        self.environ_patch = patch.dict(os.environ, {}, clear=True)
        self.environ_patch.start()
        self.addCleanup(self.environ_patch.stop)
        self.path_patch = patch.object(sys, 'path', list(sys.path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def build_frontend(self):
        frontend_dir = self.root / 'build'
        frontend_dir.mkdir()
        (frontend_dir / 'index.html').write_text('<title>Buddy</title>', encoding='utf-8')

    def test_missing_build_explains_how_to_build_buddy(self):
        with self.assertRaisesRegex(SystemExit, 'npm ci and npm run build'):
            self.serve.configure_buddy_runtime()
        self.assertNotIn('FRONTEND_BUILD_DIR', os.environ)

    def test_tracked_backend_and_frontend_use_existing_data_location(self):
        self.build_frontend()
        self.serve.configure_buddy_runtime()
        self.assertEqual(sys.path[0], str(self.root / 'backend'))
        self.assertEqual(os.environ['FRONTEND_BUILD_DIR'], str(self.root / 'build'))
        self.assertEqual(os.environ['DATA_DIR'], str(self.root / 'open-webui-data'))
        self.assertEqual(os.environ['STATIC_DIR'], str(self.root / 'open-webui-data' / 'static'))
        self.assertEqual(os.environ['WEBUI_NAME'], 'Buddy')
        self.assertEqual(os.environ['WEBUI_FAVICON_URL'], '/static/favicon.png')

    def test_explicit_data_location_and_name_are_preserved(self):
        self.build_frontend()
        data_dir = self.root / 'existing-data'
        os.environ['DATA_DIR'] = str(data_dir)
        os.environ['WEBUI_NAME'] = 'Team Buddy'
        self.serve.configure_buddy_runtime()
        self.assertEqual(os.environ['DATA_DIR'], str(data_dir))
        self.assertEqual(os.environ['STATIC_DIR'], str(data_dir / 'static'))
        self.assertEqual(os.environ['WEBUI_NAME'], 'Team Buddy')


class BuddyManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_uses_square_app_icons_with_correct_dimensions(self):
        # Load only this route's function so this regression check does not
        # initialize the application database or contact provider services.
        root_dir = LOCAL_DIR.parent
        route_path = root_dir / 'backend' / 'open_webui' / 'main.py'
        syntax_tree = ast.parse(route_path.read_text(encoding='utf-8'))
        manifest_function = next(
            node for node in syntax_tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_manifest_json'
        )
        manifest_function.decorator_list = []
        route_module = ast.Module(body=[manifest_function], type_ignores=[])
        app_state = types.SimpleNamespace(WEBUI_NAME='Buddy', EXTERNAL_PWA_MANIFEST_URL=None)
        route_namespace = {'app': types.SimpleNamespace(state=app_state)}
        exec(compile(route_module, str(route_path), 'exec'), route_namespace)
        manifest = await route_namespace['get_manifest_json']()

        self.assertEqual({icon['sizes'] for icon in manifest['icons']}, {'192x192', '512x512'})
        for icon in manifest['icons']:
            self.assertNotEqual(icon['src'], '/static/logo.png', 'The wordmark is not an app icon')
            self.assertEqual(icon['purpose'], 'any maskable')
            icon_path = root_dir / 'static' / icon['src'].lstrip('/')
            png_bytes = icon_path.read_bytes()
            self.assertEqual(png_bytes[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', png_bytes[16:24])
            self.assertEqual(width, height, 'App icons must be square')
            self.assertEqual(icon['sizes'], f'{width}x{height}', 'Manifest size must match the served image')


if __name__ == '__main__':
    unittest.main()
