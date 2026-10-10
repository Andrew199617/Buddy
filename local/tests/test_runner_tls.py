"""Operator-supplied Runner TLS, using ephemeral certificates and loopback only."""

import asyncio
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import machines, runner
from local.tests.test_remote_process_cleanup import is_running
from local.tests.tls_fixtures import TemporaryCertificateAuthority


REPOSITORY = Path(__file__).resolve().parents[2]


class CertificateFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buddy-runner-tls-test-')
        self.directory = Path(self.temporary.name)
        self.ca = TemporaryCertificateAuthority(self.directory)
        self.certificate, self.private_key = self.ca.issue_server(
            'server fixture', valid_days=60, dns_names=('localhost', 'host.docker.internal'),
        )

    def tearDown(self):
        self.temporary.cleanup()


class RunnerTlsContextTests(CertificateFixtures):
    def test_optional_tls_preserves_loopback_http(self):
        self.assertIsNone(runner.build_server_tls_context(None, None))

    def test_certificate_and_private_key_must_be_supplied_together(self):
        for certificate, key in ((self.certificate, None), (None, self.private_key)):
            with self.subTest(certificate=certificate, key=key), self.assertRaisesRegex(ValueError, 'together'):
                runner.build_server_tls_context(certificate, key)

    def test_server_context_requires_tls_12_without_inherited_key_logging(self):
        key_log = self.directory / 'must-not-create-session-secrets.log'
        with patch.dict(os.environ, {'SSLKEYLOGFILE': str(key_log)}):
            context = runner.build_server_tls_context(self.certificate, self.private_key)
        self.assertEqual(context.protocol, ssl.PROTOCOL_TLS_SERVER)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertIsNone(context.keylog_filename)
        self.assertFalse(key_log.exists())

    def test_invalid_pem_and_mismatched_key_are_rejected(self):
        invalid = self.directory / 'invalid.pem'
        invalid.write_text('This is a disposable invalid PEM fixture.', encoding='utf-8')
        _, other_key = self.ca.issue_server('other')
        for certificate, key in ((invalid, self.private_key), (self.certificate, other_key)):
            with self.subTest(certificate=certificate), self.assertRaises(ValueError):
                runner.build_server_tls_context(certificate, key)

    def test_encrypted_key_is_rejected_without_interactive_password(self):
        key = serialization.load_pem_private_key(self.private_key.read_bytes(), password=None)
        encrypted = self.directory / 'encrypted-fixture-key.pem'
        encrypted.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b'disposable-fixture-password'),
        ))
        with self.assertRaises(ValueError):
            runner.build_server_tls_context(self.certificate, encrypted)

    def test_expired_and_not_yet_valid_certificates_are_rejected(self):
        for name, options, message in (
            ('expired', {'expired': True}, 'expired'),
            ('future', {'not_yet_valid': True}, 'not valid until'),
        ):
            certificate, key = self.ca.issue_server(name, **options)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                runner.build_server_tls_context(certificate, key)

    def test_expired_certificate_cannot_pass_by_replacing_source_after_openssl_load(self):
        certificate, key = self.ca.issue_server('rotation-expired', expired=True)
        valid_data = self.certificate.read_bytes()
        real_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        def load_then_replace(certificate_path, key_path, *, password):
            real_context.load_cert_chain(certificate_path, key_path, password=password)
            certificate.write_bytes(valid_data)

        context = SimpleNamespace(load_cert_chain=load_then_replace)
        with patch.object(runner.ssl, 'SSLContext', return_value=context):
            with self.assertRaisesRegex(ValueError, 'expired'):
                runner.build_server_tls_context(certificate, key)
        # Validity rejection now happens before OpenSSL could load the expired
        # leaf and a later path read could mistake its replacement for that leaf.
        self.assertNotEqual(certificate.read_bytes(), valid_data)

    def test_certificate_snapshot_never_copies_embedded_private_key(self):
        combined = self.directory / 'mixed-certificate-and-key.pem'
        combined.write_bytes(self.certificate.read_bytes() + self.private_key.read_bytes())
        with patch.object(runner.tempfile, 'TemporaryDirectory') as create_snapshot:
            with self.assertRaisesRegex(ValueError, 'must not contain a private key'):
                runner.build_server_tls_context(combined, self.private_key)
        create_snapshot.assert_not_called()

    def test_public_snapshot_survives_source_rotation_and_is_removed_after_load(self):
        _, mismatched_key = self.ca.issue_server('rotation-mismatch')
        original_data = self.certificate.read_bytes()
        for key, succeeds in ((self.private_key, True), (mismatched_key, False)):
            with self.subTest(succeeds=succeeds):
                self.certificate.write_bytes(original_data)
                snapshots = []
                real_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

                def load_snapshot(certificate_path, key_path, *, password):
                    snapshot = Path(certificate_path)
                    snapshots.append(snapshot)
                    self.assertNotEqual(snapshot, self.certificate)
                    self.assertEqual(snapshot.read_bytes(), original_data)
                    self.assertEqual(Path(key_path), key)
                    self.assertEqual(password, '')
                    self.assertEqual(list(snapshot.parent.iterdir()), [snapshot])
                    self.certificate.write_bytes(b'Disposable replacement invalid PEM')
                    real_context.load_cert_chain(certificate_path, key_path, password=password)

                context = SimpleNamespace(load_cert_chain=load_snapshot)
                with patch.object(runner.ssl, 'SSLContext', return_value=context):
                    if succeeds:
                        runner.build_server_tls_context(self.certificate, key)
                    else:
                        with self.assertRaises(ValueError):
                            runner.build_server_tls_context(self.certificate, key)
                self.assertEqual(len(snapshots), 1)
                self.assertFalse(snapshots[0].exists())
                self.assertFalse(snapshots[0].parent.exists())

    def test_near_expiry_warns_with_restart_and_session_impact(self):
        certificate, key = self.ca.issue_server('near-expiry')
        with self.assertLogs(runner.log, level='WARNING') as captured:
            runner.build_server_tls_context(certificate, key)
        warning = '\n'.join(captured.output)
        self.assertIn('expires on', warning)
        self.assertIn('+00:00', warning)
        self.assertIn('restart the Runner', warning)
        self.assertIn('active CLI and terminal sessions will stop', warning)
        with patch.object(runner.log, 'warning') as warn:
            runner.build_server_tls_context(self.certificate, self.private_key)
        warn.assert_not_called()


class RunnerTlsCliTests(CertificateFixtures):
    def test_invalid_tls_fails_before_key_creation_and_state_mutation(self):
        _, other_key = self.ca.issue_server('mismatch')
        expired, expired_key = self.ca.issue_server('expired', expired=True)
        future, future_key = self.ca.issue_server('future', not_yet_valid=True)
        cases = (
            ['--tls-cert', str(self.certificate)],
            ['--tls-key', str(self.private_key)],
            ['--tls-cert', str(self.certificate), '--tls-key', str(other_key)],
            ['--tls-cert', str(expired), '--tls-key', str(expired_key)],
            ['--tls-cert', str(future), '--tls-key', str(future_key)],
            ['--tls-cert', str(self.directory / 'missing.pem'), '--tls-key', str(self.private_key)],
        )
        state = self.directory / 'must-not-create-state'
        for tls_arguments in cases:
            with self.subTest(tls_arguments=tls_arguments), patch.object(
                sys, 'argv', ['runner', '--state-dir', str(state), *tls_arguments]
            ), patch.object(runner, 'load_or_create_key') as load_key, patch.object(
                runner, 'build_app'
            ) as build_app, patch.object(runner.web, 'run_app') as run_app:
                with self.assertRaises(SystemExit) as raised:
                    runner.main()
                self.assertEqual(raised.exception.code, 2)
                load_key.assert_not_called()
                build_app.assert_not_called()
                run_app.assert_not_called()
                self.assertFalse(state.exists())

    def test_actual_main_forwards_tls_context_and_keeps_loopback_default_port(self):
        state = self.directory / 'unused-state'
        host = SimpleNamespace(host={'name': 'Fixture', 'platform': 'fixture', 'id': 'fixture'})
        app = {'workspace_capabilities': host}
        with patch.object(sys, 'argv', [
            'runner', '--state-dir', str(state),
            '--tls-cert', str(self.certificate), '--tls-key', str(self.private_key),
        ]), patch.object(runner, 'load_or_create_key', return_value='synthetic-key') as load_key, patch.object(
            runner, 'build_app', return_value=app
        ), patch.object(runner.web, 'run_app') as run_app:
            runner.main()
        load_key.assert_called_once_with(state / 'key', state_dir=state)
        arguments = run_app.call_args.kwargs
        self.assertEqual(arguments['host'], '127.0.0.1')
        self.assertEqual(arguments['port'], 8765)
        self.assertEqual(arguments['ssl_context'].protocol, ssl.PROTOCOL_TLS_SERVER)
        self.assertFalse(state.exists())

    def test_main_still_refuses_non_loopback_tls_binding_before_key_creation(self):
        with patch.object(sys, 'argv', [
            'runner', '--host', '0.0.0.0',
            '--tls-cert', str(self.certificate), '--tls-key', str(self.private_key),
        ]), patch.object(runner, 'load_or_create_key') as load_key:
            with self.assertRaises(SystemExit):
                runner.main()
        load_key.assert_not_called()


class RunnerHttpsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buddy-runner-https-test-')
        self.directory = Path(self.temporary.name)
        self.ca = TemporaryCertificateAuthority(self.directory)
        certificate, key = self.ca.issue_server('https-server', valid_days=60)
        self.app = runner.build_app('synthetic-https-provider-key', self.directory / 'state')
        self.host = self.app['runner']
        self.server = TestServer(self.app, scheme='https')
        await self.server.start_server(ssl=runner.build_server_tls_context(certificate, key))
        self.client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.client_context.load_verify_locations(cafile=str(self.ca.ca_file))
        self.client = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.client_context))
        self.environment = patch.dict(os.environ, {'BUDDY_RUNNER_CA_FILE': str(self.ca.ca_file)})
        self.environment.start()
        self.remote = machines.RemoteMachine(
            'fixture', 'Disposable HTTPS Runner', str(self.server.make_url('')),
            'synthetic-https-provider-key',
        )

    async def asyncTearDown(self):
        try:
            await self.remote.close()
            await self.client.close()
            await self.server.close()
        finally:
            self.environment.stop()
            self.temporary.cleanup()

    async def wait_for_registry_cleanup(self):
        async with asyncio.timeout(10):
            while self.host.processes or self.host.stdin_workers:
                await asyncio.sleep(0.01)

    async def test_verified_https_health_and_unauthorized_http_websocket_requests(self):
        async with self.client.get(self.server.make_url('/health')) as response:
            self.assertEqual(response.status, 401)
        async with self.client.get(
            self.server.make_url('/health'),
            headers={'Authorization': 'Bearer synthetic-https-provider-key'},
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())['ok'])
        with self.assertRaises(aiohttp.WSServerHandshakeError) as raised:
            await self.client.ws_connect(self.server.make_url('/process'))
        self.assertEqual(raised.exception.status, 401)
        self.assertFalse(self.host.processes)

    async def test_tls_12_clients_can_connect_with_certificate_verification(self):
        self.client_context.maximum_version = ssl.TLSVersion.TLSv1_2
        async with self.client.get(self.server.make_url('/health')) as response:
            self.assertEqual(response.status, 401)

    async def test_verified_websocket_natural_exit_preserves_output_and_cleanup(self):
        children = []
        original_start = runner.start_local_process

        def record_start(*args, **kwargs):
            child = original_start(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(runner, 'start_local_process', side_effect=record_start):
            process = await self.remote.start_process(
                [sys.executable, '-c', 'print("disposable HTTPS output",flush=True)'],
                str(self.directory),
            )
            self.assertEqual(await asyncio.wait_for(process.read_line(), 10), 'disposable HTTPS output')
            self.assertEqual(await process.wait(10), 0)
            await process.close()
        self.assertTrue(process.cleanup_confirmed)
        self.assertTrue(children[0].cleanup_confirmed)
        await self.wait_for_registry_cleanup()

    async def test_verified_websocket_stop_confirms_native_descendant_cleanup(self):
        fixture = (
            'import json,subprocess,sys,time; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print(json.dumps({"child":child.pid}),flush=True); time.sleep(30)'
        )
        process = await self.remote.start_process([sys.executable, '-c', fixture], str(self.directory))
        native = next(iter(self.host.processes))
        try:
            descendant = json.loads(await asyncio.wait_for(process.read_line(), 10))['child']
            self.assertTrue(is_running(descendant))
            await process.close()
            self.assertTrue(process.cleanup_confirmed)
            self.assertTrue(native.cleanup_confirmed)
            self.assertFalse(is_running(native.pid))
            self.assertFalse(is_running(descendant))
            await self.wait_for_registry_cleanup()
        finally:
            await native.close()


@unittest.skipUnless(os.name == 'nt', 'Native PowerShell launcher is tested on Windows')
class RunnerTlsPowerShellTests(CertificateFixtures):
    def setUp(self):
        super().setUp()
        self.powershell = shutil.which('pwsh') or shutil.which('powershell')
        if self.powershell is None:
            self.skipTest('PowerShell is unavailable')
        self.stub = self.directory / 'fake python fixture.ps1'
        self.stub.write_text(
            'param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RunnerPassedArguments)\n'
            'ConvertTo-Json -InputObject @($RunnerPassedArguments) -Compress\nexit 0\n',
            encoding='utf-8',
        )
        self.state = self.directory / 'must-not-create-launcher-state'

    def run_launcher(self, arguments):
        return subprocess.run(
            [self.powershell, '-NoProfile', '-File', str(REPOSITORY / 'local' / 'start-buddy-runner.ps1'),
             '-PythonPath', str(self.stub), '-StateDir', str(self.state), *arguments],
            capture_output=True, text=True, timeout=15,
        )

    def test_launcher_forwards_tls_paths_with_spaces_without_creating_state(self):
        result = self.run_launcher([
            '-TlsCertificate', str(self.certificate), '-TlsKeyFile', str(self.private_key),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout.strip())
        self.assertEqual(arguments[arguments.index('--tls-cert') + 1], str(self.certificate))
        self.assertEqual(arguments[arguments.index('--tls-key') + 1], str(self.private_key))
        self.assertEqual(arguments[arguments.index('--port') + 1], '8765')
        self.assertFalse(self.state.exists())

    def test_launcher_rejects_incomplete_or_missing_tls_pair_before_python(self):
        for arguments, message in (
            (['-TlsCertificate', str(self.certificate)], 'together'),
            (['-TlsCertificate', str(self.directory / 'missing.pem'), '-TlsKeyFile', str(self.private_key)], 'not found'),
            (['-TlsCertificate', str(self.certificate), '-TlsKeyFile', str(self.directory / 'missing-key.pem')], 'not found'),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_launcher(arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(result.stdout.strip(), '')
                self.assertFalse(self.state.exists())


class RunnerTlsComposeOverlayTests(unittest.TestCase):
    def test_public_ca_overlay_modifies_only_backend_trust_and_keeps_existing_stack(self):
        import yaml

        base = yaml.safe_load((REPOSITORY / 'docker' / 'buddy' / 'compose.yaml').read_text(encoding='utf-8'))
        overlay = yaml.safe_load((REPOSITORY / 'docker' / 'buddy' / 'runner-tls.yaml').read_text(encoding='utf-8'))
        self.assertEqual(set(overlay), {'services'})
        self.assertEqual(set(overlay['services']), {'buddy'})
        backend = overlay['services']['buddy']
        self.assertEqual(set(backend), {'environment', 'volumes'})
        self.assertEqual(backend['environment'], {'BUDDY_RUNNER_CA_FILE': '/etc/buddy/runner-ca.pem'})
        self.assertEqual(len(backend['volumes']), 1)
        mount = backend['volumes'][0]
        self.assertEqual(mount['type'], 'bind')
        self.assertIn('BUDDY_RUNNER_CA_HOST_FILE:?', mount['source'])
        self.assertEqual(mount['target'], '/etc/buddy/runner-ca.pem')
        self.assertTrue(mount['read_only'])
        self.assertEqual(mount['bind'], {'create_host_path': False})
        # Synthetic merge only; no Docker commands or deployment environment.
        merged = json.loads(json.dumps(base))
        merged['services']['buddy']['environment'].update(backend['environment'])
        merged['services']['buddy']['volumes'].extend(backend['volumes'])
        self.assertEqual(merged['services']['web'], base['services']['web'])
        self.assertEqual(merged['volumes'], base['volumes'])
        self.assertEqual(merged['services']['buddy']['ports'], base['services']['buddy']['ports'])
        self.assertEqual(merged['services']['buddy']['volumes'][0], base['services']['buddy']['volumes'][0])


if __name__ == '__main__':
    unittest.main()
