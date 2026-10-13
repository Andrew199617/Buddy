"""Strict Runner origins and actual verified TLS on disposable loopback servers."""

import asyncio
import os
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.utils.subscriptions import machines, runner_transport
from open_webui.utils.subscriptions.events import SubscriptionError
from local.tests.tls_fixtures import TemporaryCertificateAuthority


class RunnerOriginTests(unittest.TestCase):
    def test_supported_origins_are_canonical(self):
        cases = {
            ' HTTPS://Runner.Example.Invalid:443/ ': 'https://runner.example.invalid',
            'https://runner.example.invalid:9443/': 'https://runner.example.invalid:9443',
            'https://host.docker.internal:8765': 'https://host.docker.internal:8765',
            'https://machine.tail123.ts.net:8765': 'https://machine.tail123.ts.net:8765',
            'https://[2001:0db8::1]:9443': 'https://[2001:db8::1]:9443',
            'http://LOCALHOST:80/': 'http://localhost',
            'http://localhost:8765': 'http://localhost:8765',
            'http://127.0.0.1:8765/': 'http://127.0.0.1:8765',
            'http://127.42.1.2:8765': 'http://127.42.1.2:8765',
            'http://[0:0:0:0:0:0:0:1]:8765/': 'http://[::1]:8765',
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(runner_transport.normalize_runner_url(value), expected)

    def test_every_nonloopback_http_target_is_rejected(self):
        hosts = (
            'host.docker.internal', 'gateway.docker.internal', 'docker.internal',
            'machine.tail123.ts.net', '100.64.1.2', '192.168.1.10', '10.0.0.2',
            '172.16.1.2', '169.254.169.254', '[fd7a:115c:a1e0::1]', '[2001:db8::1]',
            'runner.example.invalid', 'localhost.example.invalid', 'localhost.',
            '127.attacker.example.invalid', '127.1', '2130706433',
        )
        for host in hosts:
            with self.subTest(host=host), self.assertRaises(SubscriptionError):
                runner_transport.normalize_runner_url(f'http://{host}:8765')

    def test_malformed_or_nonorigin_addresses_are_rejected(self):
        cases = (
            '', '//localhost:8765', 'ftp://localhost', 'ws://localhost', 'wss://localhost',
            'https://user:secret@runner.example.invalid', 'https://@runner.example.invalid',
            'https://runner.example.invalid/project', 'https://runner.example.invalid/../',
            'https://runner.example.invalid//', 'https://runner.example.invalid?',
            'https://runner.example.invalid/#', 'https://runner.example.invalid?token=value',
            'https://runner.example.invalid#value', 'https://runner.example.invalid\\project',
            'https://runner.example.invalid\\@localhost', 'https://runner.example.invalid:0',
            'https://runner.example.invalid:65536', 'https://runner.example.invalid:-1',
            'https://runner.example.invalid:not-a-port', 'https://runner.example.invalid:',
            'https://[::1', 'https://', 'https://runner .example.invalid',
            'https://runner.example.invalid\n', '\thttps://runner.example.invalid',
            'https://runner.example.invalid/\x00', 'https://runner.example.invalid\x7f',
            'https://runner.example.invalid\x80', 'https://local%68ost', 'http://[::1%25lo]',
            'https://[::1]suffix', 'https://prefix[::1]', 'http://[::1]suffix:8765',
            'https://[v1.future-host]',
        )
        for value in cases:
            with self.subTest(value=repr(value)), self.assertRaises(SubscriptionError):
                runner_transport.normalize_runner_url(value)

    def test_constructor_rejects_before_headers_or_client_creation(self):
        with patch.object(machines.RemoteMachine, '_headers') as headers:
            with patch.object(machines.aiohttp, 'ClientSession') as session:
                with self.assertRaises(SubscriptionError):
                    machines.RemoteMachine('fixture', 'Fixture', 'http://host.docker.internal:8765', 'synthetic-key')
        headers.assert_not_called()
        session.assert_not_called()

    def test_machine_matching_uses_canonical_origin(self):
        remote = machines.RemoteMachine('fixture', 'Fixture', 'HTTPS://Runner.Example.Invalid:443/', 'synthetic-key')
        self.assertEqual(remote.url, 'https://runner.example.invalid')
        self.assertTrue(remote.matches('Fixture', 'https://runner.example.invalid/', 'synthetic-key'))
        self.assertFalse(remote.matches('Fixture', 'https://other.example.invalid/', 'synthetic-key'))
        with self.assertRaises(SubscriptionError):
            remote.matches('Fixture', 'http://host.docker.internal:8765', 'synthetic-key')

    def test_default_context_verifies_and_never_enables_inherited_key_logging(self):
        with tempfile.TemporaryDirectory(prefix='buddy-tls-keylog-test-') as directory:
            keylog = Path(directory) / 'must-not-exist.log'
            with patch.dict(os.environ, {'BUDDY_RUNNER_CA_FILE': '', 'SSLKEYLOGFILE': str(keylog)}):
                context = runner_transport.runner_ssl_context()
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertIsNone(context.keylog_filename)
            self.assertFalse(keylog.exists())

    def test_custom_ca_addition_preserves_default_roots(self):
        with tempfile.TemporaryDirectory(prefix='buddy-tls-root-test-') as directory:
            authority = TemporaryCertificateAuthority(Path(directory))
            with patch.dict(os.environ, {'BUDDY_RUNNER_CA_FILE': ''}):
                default = runner_transport.runner_ssl_context()
            with patch.dict(os.environ, {'BUDDY_RUNNER_CA_FILE': str(authority.ca_file)}):
                additional = runner_transport.runner_ssl_context()
            default_roots = set(default.get_ca_certs(binary_form=True))
            additional_roots = set(additional.get_ca_certs(binary_form=True))
            self.assertTrue(default_roots.issubset(additional_roots))
            self.assertIn(authority.certificate.public_bytes(serialization.Encoding.DER), additional_roots)
            self.assertTrue(additional.check_hostname)
            self.assertEqual(additional.verify_mode, ssl.CERT_REQUIRED)


class RunnerTlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buddy-runner-tls-test-')
        self.root = Path(self.temporary.name)
        self.authority = TemporaryCertificateAuthority(self.root)
        self.key = 'synthetic-transport-fixture-key'
        self.requests = []
        self.servers = []
        self.clients = []
        self.enterContext(patch.dict(os.environ, {'BUDDY_RUNNER_CA_FILE': ''}))

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        for server in self.servers:
            await server.close()
        self.temporary.cleanup()

    def synthetic_runner(self):
        async def health(request):
            self.requests.append((request.path, request.headers.get('Authorization')))
            return web.json_response({'fixture': 'verified runner'})

        async def process(request):
            self.requests.append((request.path, request.headers.get('Authorization')))
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            start = await socket.receive_json(timeout=2)
            self.assertEqual(start['type'], 'start')
            self.assertEqual(start['args'], ['synthetic-command'])
            await socket.send_json({'type': 'started', 'pid': 12345})
            await socket.send_json({'type': 'stdout', 'data': 'fixture output\n'})
            await socket.send_json({'type': 'exit', 'code': 0, 'stderr': ''})
            await socket.close()
            return socket

        app = web.Application()
        app.router.add_get('/health', health)
        app.router.add_get('/process', process)
        return app

    async def serve(self, app, *, name='server', tls=True, **certificate_options):
        server = TestServer(app)
        if tls:
            certificate, key = self.authority.issue_server(name, **certificate_options)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(certificate), str(key))
            await server.start_server(ssl=context)
        else:
            await server.start_server()
        self.servers.append(server)
        return server

    def remote(self, server):
        remote = machines.RemoteMachine('fixture', 'Isolated TLS fixture', str(server.make_url('')), self.key)
        self.clients.append(remote)
        return remote

    async def assert_certificate_rejected_before_headers(self, remote):
        with self.assertRaises(SubscriptionError) as health_error:
            await remote.info()
        self.assertIsInstance(health_error.exception.__cause__, machines.aiohttp.ClientConnectorCertificateError)
        with self.assertRaises(SubscriptionError) as process_error:
            await remote.start_process(['synthetic-command'], str(self.root))
        self.assertIsInstance(process_error.exception.__cause__, machines.aiohttp.ClientConnectorCertificateError)
        self.assertNotIsInstance(process_error.exception, machines.StartupCleanupUnconfirmedError)
        self.assertEqual(self.requests, [])

    async def assert_health_and_process(self, remote):
        self.assertEqual(await remote.info(), {'fixture': 'verified runner'})
        session = remote._session
        process = await remote.start_process(['synthetic-command'], str(self.root))
        self.assertEqual(await asyncio.wait_for(process.read_line(), 2), 'fixture output')
        self.assertEqual(await process.wait(2), 0)
        await process.close(2)
        self.assertTrue(process.cleanup_confirmed)
        self.assertIs(remote._session, session)
        self.assertEqual(self.requests, [
            ('/health', f'Bearer {self.key}'), ('/process', f'Bearer {self.key}'),
        ])

    async def test_added_ca_verifies_actual_http_and_websocket_on_same_session(self):
        server = await self.serve(self.synthetic_runner())
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(self.authority.ca_file)
        await self.assert_health_and_process(self.remote(server))

    async def test_explicit_self_signed_end_entity_trust_verifies_http_and_websocket(self):
        server = await self.serve(self.synthetic_runner(), name='self-signed', self_signed=True)
        public_certificate = self.root / 'self-signed-certificate.pem'
        certificate = x509.load_pem_x509_certificate(public_certificate.read_bytes())
        self.assertEqual(certificate.subject, certificate.issuer)
        self.assertFalse(certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(public_certificate)
        await self.assert_health_and_process(self.remote(server))

    async def test_untrusted_ca_rejects_http_and_websocket_before_headers(self):
        server = await self.serve(self.synthetic_runner())
        await self.assert_certificate_rejected_before_headers(self.remote(server))

    async def test_wrong_hostname_rejects_http_and_websocket_before_headers(self):
        server = await self.serve(
            self.synthetic_runner(), dns_names=('wrong-host.example.invalid',), ip_addresses=(),
        )
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(self.authority.ca_file)
        await self.assert_certificate_rejected_before_headers(self.remote(server))

    async def test_expired_certificate_rejects_http_and_websocket_before_headers(self):
        server = await self.serve(self.synthetic_runner(), expired=True)
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(self.authority.ca_file)
        await self.assert_certificate_rejected_before_headers(self.remote(server))

    async def assert_bad_ca_configuration(self, ca_file):
        server = await self.serve(self.synthetic_runner())
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(ca_file)
        remote = self.remote(server)
        with patch.object(remote, '_headers') as headers:
            with self.assertRaisesRegex(SubscriptionError, 'BUDDY_RUNNER_CA_FILE'):
                await remote.info()
            with self.assertRaisesRegex(SubscriptionError, 'BUDDY_RUNNER_CA_FILE'):
                await remote.start_process(['synthetic-command'], str(self.root))
        headers.assert_not_called()
        self.assertIsNone(remote._session)
        self.assertEqual(self.requests, [])

    async def test_missing_ca_file_fails_without_client_headers_or_fallback(self):
        await self.assert_bad_ca_configuration(self.root / 'missing-ca.pem')

    async def test_invalid_ca_pem_fails_without_client_headers_or_fallback(self):
        invalid = self.root / 'invalid-ca.pem'
        invalid.write_text('disposable invalid public CA fixture', encoding='utf-8')
        await self.assert_bad_ca_configuration(invalid)

    async def test_combined_certificate_and_private_key_is_rejected_before_headers(self):
        _, private_key = self.authority.issue_server('combined-trust-fixture')
        combined = self.root / 'must-not-load-private-key.pem'
        contents = self.authority.ca_file.read_bytes() + private_key.read_bytes()
        combined.write_bytes(contents)
        await self.assert_bad_ca_configuration(combined)
        self.assertEqual(combined.read_bytes(), contents)

    async def test_oversized_public_bundle_is_rejected_before_headers(self):
        oversized = self.root / 'oversized-ca.pem'
        oversized.write_bytes(b' ' * (runner_transport.MAX_RUNNER_CA_BYTES + 1))
        await self.assert_bad_ca_configuration(oversized)
        self.assertEqual(oversized.stat().st_size, runner_transport.MAX_RUNNER_CA_BYTES + 1)

    async def test_plain_http_loopback_still_works(self):
        server = await self.serve(self.synthetic_runner(), tls=False)
        await self.assert_health_and_process(self.remote(server))

    async def assert_redirects_never_reach_destination(self, path, *, websocket):
        target_requests = []

        async def target(request):
            target_requests.append((request.path, request.headers.get('Authorization')))
            return web.json_response({'fixture': 'must not be reached'})

        plain_target = web.Application()
        plain_target.router.add_get('/redirect-target', target)
        plain_server = await self.serve(plain_target, tls=False)
        secure_target = web.Application()
        secure_target.router.add_get('/redirect-target', target)
        secure_server = await self.serve(secure_target, name='redirect-destination')

        destination = ''
        status = 302
        source_requests = []

        async def redirect(request):
            source_requests.append((request.path, request.headers.get('Authorization')))
            return web.json_response({'error': 'Disposable redirect fixture'}, status=status, headers={'Location': destination})

        source = web.Application()
        source.router.add_get(path, redirect)
        source.router.add_get('/redirect-target', target)
        source_server = await self.serve(source, name='redirect-source')
        os.environ['BUDDY_RUNNER_CA_FILE'] = str(self.authority.ca_file)
        remote = self.remote(source_server)
        destinations = (
            str(source_server.make_url('/redirect-target')),
            str(secure_server.make_url('/redirect-target')),
            str(plain_server.make_url('/redirect-target')),
        )
        for status in (301, 302, 303, 307, 308):
            for destination in destinations:
                with self.subTest(status=status, destination=destination):
                    with self.assertRaises(SubscriptionError) as captured:
                        if websocket:
                            await remote.start_process(['synthetic-command'], str(self.root))
                        else:
                            await remote.info()
                    self.assertNotIsInstance(captured.exception, machines.StartupCleanupUnconfirmedError)
                    self.assertEqual(target_requests, [])
        self.assertEqual(len(source_requests), 15)
        self.assertTrue(all(value == f'Bearer {self.key}' for _, value in source_requests))

    async def test_http_redirects_never_forward_requests_or_keys(self):
        await self.assert_redirects_never_reach_destination('/health', websocket=False)

    async def test_websocket_redirects_never_forward_requests_or_keys(self):
        await self.assert_redirects_never_reach_destination('/process', websocket=True)


if __name__ == '__main__':
    unittest.main()
