"""Verified transport for the Runner's privileged, server-held credential."""

import ipaddress
import os
import ssl
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from open_webui.utils.subscriptions.events import SubscriptionError

MAX_RUNNER_CA_BYTES = 1024 * 1024


def normalize_runner_url(value: str) -> str:
    """Accept one HTTPS origin, or an HTTP origin naming literal loopback."""
    if not isinstance(value, str):
        raise SubscriptionError('The Runner address must be a URL origin.')
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise SubscriptionError('The Runner address cannot contain control characters.')
    value = value.strip()
    if any(character.isspace() for character in value) or any(character in value for character in ('\\', '?', '#')):
        raise SubscriptionError('The Runner address cannot contain whitespace, backslashes, a query, or a fragment.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SubscriptionError('Enter a verified HTTPS Runner origin or a loopback HTTP origin.') from error
    if (
        parsed.scheme not in ('http', 'https') or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in ('', '/') or parsed.query or parsed.fragment
        or parsed.netloc.endswith(':') or '%' in parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SubscriptionError('The Runner address must be an HTTPS origin or a loopback HTTP origin, without credentials or a path.')

    bracketed = '[' in parsed.netloc or ']' in parsed.netloc
    if bracketed:
        closing = parsed.netloc.find(']')
        suffix = parsed.netloc[closing + 1:]
        if (
            not parsed.netloc.startswith('[') or closing < 0
            or (suffix and (not suffix.startswith(':') or not suffix[1:].isascii() or not suffix[1:].isdigit()))
        ):
            raise SubscriptionError('The Runner address has an invalid IPv6 authority.')

    host = parsed.hostname
    loopback = host == 'localhost'
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if bracketed:
            raise SubscriptionError('Only literal IPv6 addresses may use brackets in the Runner address.')
        try:
            host = host.encode('idna').decode('ascii').lower()
        except UnicodeError as error:
            raise SubscriptionError('The Runner address has an invalid hostname.') from error
    else:
        host = address.compressed
        loopback = address.is_loopback
    if parsed.scheme == 'http' and not loopback:
        raise SubscriptionError('Use verified HTTPS for every Runner outside loopback, including Docker and Tailscale hosts.')

    authority = f'[{host}]' if ':' in host else host
    default_port = 443 if parsed.scheme == 'https' else 80
    if port is not None and port != default_port:
        authority += f':{port}'
    return urlunsplit((parsed.scheme, authority, '', '', ''))


def runner_ssl_context() -> ssl.SSLContext:
    """Keep platform trust and optionally add an application-specific public CA."""
    # Unlike create_default_context(), this never enables inherited SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    ca_file = os.environ.get('BUDDY_RUNNER_CA_FILE')
    if ca_file:
        try:
            with open(ca_file, 'rb') as handle:
                certificate_data = handle.read(MAX_RUNNER_CA_BYTES + 1)
            if len(certificate_data) > MAX_RUNNER_CA_BYTES:
                raise ValueError('The public Runner trust bundle is too large.')
            if b'PRIVATE KEY-----' in certificate_data:
                raise ValueError('The Runner trust bundle must not contain a private key.')
            # Load these validated public bytes rather than reopening a path
            # that could have changed after the private-key and size checks.
            context.load_verify_locations(cadata=certificate_data.decode('ascii'))
        except (OSError, ValueError) as error:
            raise SubscriptionError(
                'BUDDY_RUNNER_CA_FILE must name a readable public PEM certificate bundle '
                'of at most 1 MiB, without private keys.'
            ) from error
    return context


async def _reject_redirect(session, trace_context, parameters) -> None:
    # aiohttp's WebSocket handshake follows redirects through its HTTP client.
    # This signal runs before it forwards a request to the Location destination.
    parameters.response.close()
    raise aiohttp.ClientError('Runner redirects are disabled. Verify the final Runner origin explicitly.')


def runner_trace_config() -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_reject_redirect)
    return trace
