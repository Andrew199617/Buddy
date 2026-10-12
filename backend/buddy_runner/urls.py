"""Which Buddy server addresses a Runner may send its credential to.

The machine credential lets the server run commands on this computer, so it
only ever travels over verified HTTPS, or plain HTTP to this same computer.
"""

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def _is_loopback(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_server_url(url: str) -> str:
    """The server's origin, such as ``https://buddy.example.com``, or ValueError."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Enter the Buddy address, such as https://buddy.example.com.')
    url = url.strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError('Enter a valid Buddy address.') from error
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not host:
        raise ValueError('Enter a Buddy address that starts with https://.')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('The Buddy address cannot include a user name or password.')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Enter only the Buddy address, without a path.')
    if parsed.scheme == 'http' and not _is_loopback(host):
        raise ValueError('Use https:// for a Buddy server on another computer.')
    netloc = host
    if ':' in host:
        netloc = f'[{host}]'
    if port is not None:
        netloc = f'{netloc}:{port}'
    return urlunsplit((parsed.scheme, netloc, '', '', ''))


def websocket_url(server_url: str, path: str) -> str:
    """The WebSocket address for ``path`` on a normalized server origin."""
    scheme = 'wss' if server_url.startswith('https://') else 'ws'
    return f'{scheme}://{server_url.split("://", 1)[1]}{path}'
