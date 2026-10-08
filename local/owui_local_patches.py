"""Local runtime patches for Open WebUI, applied by local/serve.py at startup.

Nothing here edits Open WebUI's files. Each patch wraps one function, checks
that the function still looks the way the patch expects, and skips itself
(with a log line) if an upgrade changed it, so upgrading never breaks startup.

Patch: keep MCP OAuth sessions that have no refresh token
---------------------------------------------------------
Some MCP gateways (e.g. Slack PSS) issue access tokens with an expiry but no
refresh token. When such a token reaches its advertised expiry, Open WebUI
cannot refresh it, so it deletes the session and you must sign in again.

Instead, this asks the MCP server whether the token still works (an MCP
`initialize` call with the token). If the server accepts it, the session is
kept and checked again later. If the server rejects it (401/403), Open WebUI's
normal behavior runs and asks you to sign in. If the server can't be reached
(VPN down), the session is kept and retried in a few minutes.

Outcomes are logged (never tokens) to open-webui-data/local-patches.log.

Patch: local web scripts
------------------------
Open WebUI loads /static/loader.js on every page, a hook for custom scripts,
and recopies that file from its bundled frontend on each start. After that
copy, this appends local/web scripts for the thinking-level chip and mobile
chat layout, phone typography, adaptive model picker, usage details, visible
model activity, modern chat action menus, a theme-aware chat header, and
fullscreen phone Settings navigation that reuses native category panels,
and direct pen shortcuts beside native Workspace model menus.
"""

import functools
import inspect
import logging
import os
import time
from pathlib import Path

log = logging.getLogger('owui_local_patches')

RECHECK_SECONDS = int(os.getenv('MCP_OAUTH_RECHECK_SECONDS', '1800'))
UNREACHABLE_RETRY_SECONDS = 300
PROBE_TIMEOUT_SECONDS = 8
# Open WebUI refreshes tokens 5 minutes before expires_at.
CORE_REFRESH_MARGIN_SECONDS = 300


WEB_DIR = Path(__file__).resolve().parent / 'web'
WEB_SCRIPTS = [
    WEB_DIR / 'reasoning-chip.js',
    WEB_DIR / 'mobile-chat-layout.js',
    WEB_DIR / 'mobile-ui-polish.js',
    WEB_DIR / 'chat-usage-info.js',
    WEB_DIR / 'model-activity.js',
    WEB_DIR / 'chat-actions-menu.js',
    WEB_DIR / 'chat-header.js',
    WEB_DIR / 'mobile-settings.js',
    WEB_DIR / 'model-edit-shortcut.js',
]
LOADER_MARKER = '/* --- local/web scripts (added by owui_local_patches.py) --- */'


def apply() -> None:
    _log_to_file()
    _apply_patch(keep_mcp_oauth_sessions_without_refresh_tokens)
    _apply_patch(add_web_scripts_to_loader)
    _apply_patch(track_chat_run_usage)


def _apply_patch(patch) -> None:
    try:
        patch()
    except Exception:
        log.exception('Local patch %s failed; continuing without it', patch.__name__)


def track_chat_run_usage() -> None:
    import usage_tracking

    usage_tracking.apply()


def _log_to_file() -> None:
    log.setLevel(logging.INFO)
    if any(isinstance(h, logging.FileHandler) for h in log.handlers):
        return
    data_dir = Path(os.getenv('DATA_DIR', Path(__file__).resolve().parents[1] / 'open-webui-data'))
    try:
        handler = logging.FileHandler(data_dir / 'local-patches.log', encoding='utf-8')
    except OSError:
        return
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)


def keep_mcp_oauth_sessions_without_refresh_tokens() -> None:
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.utils.oauth import OAuthClientManager

    original = getattr(OAuthClientManager, 'get_oauth_token', None)
    if original is None or getattr(original, '__local_patch__', False):
        return
    params = list(inspect.signature(original).parameters)
    if params[:4] != ['self', 'user_id', 'client_id', 'force_refresh']:
        log.warning('Skipping MCP OAuth keep-alive: get_oauth_token%s changed upstream', params)
        return

    @functools.wraps(original)
    async def get_oauth_token(self, user_id, client_id, force_refresh=False):
        if str(client_id).startswith('mcp:') and not force_refresh:
            try:
                session = await OAuthSessions.get_session_by_provider_and_user_id(client_id, user_id)
                token = (session.token or {}) if session else {}
                if session and token.get('access_token') and not token.get('refresh_token') and _is_due(session):
                    kept = await _recheck(self, OAuthSessions, session)
                    if kept is not None:
                        return kept
            except Exception:
                log.exception('MCP OAuth keep-alive check failed for %s; using default behavior', client_id)
        return await original(self, user_id, client_id, force_refresh=force_refresh)

    get_oauth_token.__local_patch__ = True
    OAuthClientManager.get_oauth_token = get_oauth_token
    log.info('Local patch active: MCP OAuth sessions without refresh tokens are re-checked, not dropped')


def add_web_scripts_to_loader(static_dir: Path | None = None) -> None:
    if static_dir is None:
        from open_webui.config import STATIC_DIR as static_dir

    loader = Path(static_dir) / 'loader.js'
    if not loader.parent.is_dir():
        log.warning('Skipping web scripts: static dir %s not found', loader.parent)
        return
    upstream = loader.read_text(encoding='utf-8') if loader.exists() else ''
    upstream = upstream.split(LOADER_MARKER)[0].rstrip()
    scripts = [path.read_text(encoding='utf-8') for path in WEB_SCRIPTS if path.exists()]
    loader.write_text('\n\n'.join([upstream, LOADER_MARKER, *scripts]).lstrip() + '\n', encoding='utf-8')
    log.info('Local patch active: added %d web script(s) to %s', len(scripts), loader)


def _is_due(session) -> bool:
    return session.expires_at is None or time.time() + CORE_REFRESH_MARGIN_SECONDS >= session.expires_at


async def _recheck(manager, sessions, session):
    """Return the token to keep using it, or None to fall back to Open WebUI's behavior."""
    url = await _mcp_url(manager, session.provider)
    if not url:
        return None

    token = dict(session.token)
    status = await _probe(url, token['access_token'])
    issued_at = token.get('issued_at') or session.created_at
    hours = (time.time() - issued_at) / 3600 if issued_at else float('nan')
    token.setdefault('advertised_expires_at', session.expires_at)

    if status is not None and 200 <= status < 300:
        delay = RECHECK_SECONDS
        log.info(
            '%s: token still accepted %.1fh after sign-in (advertised expiry %s); next check in %d min',
            session.provider,
            hours,
            _fmt(token['advertised_expires_at']),
            delay // 60,
        )
    elif status in (401, 403):
        log.info(
            '%s: token rejected (HTTP %s) %.1fh after sign-in; Open WebUI will ask you to sign in again',
            session.provider,
            status,
            hours,
        )
        return None
    elif status is None or status == 429 or status >= 500:
        delay = UNREACHABLE_RETRY_SECONDS
        log.info('%s: server unavailable (%s); keeping session, retry in %d min', session.provider, status, delay // 60)
    else:
        log.info('%s: unexpected HTTP %s from session check; using default behavior', session.provider, status)
        return None

    token['expires_at'] = int(time.time()) + delay + CORE_REFRESH_MARGIN_SECONDS
    updated = await sessions.update_session_by_id(session.id, token)
    return updated.token if updated else token


async def _mcp_url(manager, client_id: str):
    from open_webui.models.config import Config

    server_id = client_id.split(':', 1)[1]
    for connection in await Config.get('tool_server.connections', []) or []:
        if str((connection.get('info') or {}).get('id')) == server_id and connection.get('url'):
            return connection['url']
    info = await manager.get_client_info(client_id)
    resource = getattr(info, 'resource', None)
    return str(resource) if resource else None


async def _probe(url: str, access_token: str):
    """MCP initialize with the token. Returns the HTTP status, or None if unreachable."""
    import aiohttp

    try:
        from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL as ssl
    except Exception:
        ssl = True

    auth = {'Authorization': f'Bearer {access_token}'}
    body = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'open-webui-session-check', 'version': '1'},
        },
    }
    try:
        timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as http:
            async with http.post(
                url,
                json=body,
                headers={**auth, 'Accept': 'application/json, text/event-stream'},
                ssl=ssl,
            ) as response:
                status = response.status
                mcp_session_id = response.headers.get('Mcp-Session-Id')
            if mcp_session_id:
                # Close the MCP session the check opened.
                try:
                    async with http.delete(url, headers={**auth, 'Mcp-Session-Id': mcp_session_id}, ssl=ssl):
                        pass
                except Exception:
                    pass
            return status
    except Exception as e:
        log.debug('MCP session check to %s failed: %s', url, e)
        return None


def _fmt(epoch) -> str:
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(epoch)) if epoch else 'unknown'
