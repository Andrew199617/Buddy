"""Run the Runner connected out to a Buddy server.

The Buddy app starts it like this, writing the connection as one JSON line
({server_url, machine_id, credential}) on stdin, so the credential is never
written to disk; later stdin lines may say {"command": "stop"}, and stdin
closing also stops the Runner:

    buddy-runner run --connection-stdin [--root DIR ...] [--write] [--allow-host-execution]

Without the app, for example on a server, enroll once with a token from
Buddy and run from the saved, owner-only connection file:

    buddy-runner enroll --server https://buddy.example.com --token <token>
    buddy-runner run

Status goes to stdout as JSON lines, {"event": "status", "state", "message"},
for the app to show; logs go to stderr.
"""

import argparse
import asyncio
import json
import logging
import socket
import sys
from pathlib import Path

import aiohttp

from buddy_runner import protocol
from buddy_runner.keys import read_private_text, write_private_text
from buddy_runner.runner import DEFAULT_STATE_DIR, RUNNER_VERSION, Runner
from buddy_runner.tunnel import PermanentDisconnect, run_tunnel, server_ssl_context
from buddy_runner.urls import normalize_server_url
from buddy_runner.workspace_capabilities import WorkspaceCapabilities

log = logging.getLogger('buddy_runner.connect')

CONNECTION_FILE_NAME = 'connection.json'
EXIT_USAGE = 2
EXIT_REMOVED = 3
EXIT_NOT_ALLOWED = 4
EXIT_REPLACED = 5
EXIT_NEEDS_UPDATE = 6
EXIT_CODES = {
    protocol.CLOSE_UNKNOWN_MACHINE: EXIT_REMOVED,
    protocol.CLOSE_NOT_ALLOWED: EXIT_NOT_ALLOWED,
    protocol.CLOSE_REPLACED: EXIT_REPLACED,
    protocol.CLOSE_UNSUPPORTED_VERSION: EXIT_NEEDS_UPDATE,
}
STOPPED_STATES = {
    protocol.CLOSE_UNKNOWN_MACHINE: 'removed',
    protocol.CLOSE_NOT_ALLOWED: 'refused',
    protocol.CLOSE_REPLACED: 'replaced',
    protocol.CLOSE_UNSUPPORTED_VERSION: 'needs_update',
}


def emit_status(state: str, message: str = '') -> None:
    print(json.dumps({'event': 'status', 'state': state, 'message': message}), flush=True)


def parse_connection(text: str) -> dict:
    """The server address, machine and credential, validated."""
    try:
        connection = json.loads(text)
    except ValueError as error:
        raise ValueError('The connection is not valid JSON.') from error
    if not isinstance(connection, dict):
        raise ValueError('The connection must be a JSON object.')
    machine_id = connection.get('machine_id')
    credential = connection.get('credential')
    if not isinstance(machine_id, str) or not machine_id or not isinstance(credential, str) or not credential:
        raise ValueError('The connection needs a machine ID and credential.')
    return {
        'server_url': normalize_server_url(connection.get('server_url') or ''),
        'machine_id': machine_id,
        'credential': credential,
    }


def host_details(name: str | None = None) -> dict:
    return {'name': name or socket.gethostname(), 'platform': sys.platform, 'version': str(RUNNER_VERSION)}


async def enroll(server_url: str, token: str, name: str | None) -> dict:
    """Trade an enrollment token from Buddy for this computer's connection."""
    server_url = normalize_server_url(server_url)
    ssl_context = None
    if server_url.startswith('https://'):
        ssl_context = server_ssl_context()
    payload = {'token': token, 'name': name, 'host': host_details(name)}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f'{server_url}{protocol.ENROLL_PATH}', json=payload, ssl=ssl_context, allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            body = await response.json(content_type=None)
            if response.status != 200:
                detail = body.get('detail') if isinstance(body, dict) else None
                raise ValueError(detail or f'Buddy refused the connection request (HTTP {response.status}).')
    return {
        'server_url': server_url,
        'machine_id': body['machine_id'],
        'credential': body['credential'],
        'name': body.get('name') or '',
    }


async def _watch_stdin(stop: asyncio.Event) -> None:
    """Stop on {"command": "stop"}, or when the app that started us goes away."""
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if isinstance(command, dict) and command.get('command') == 'stop':
            break
    stop.set()


async def serve(connection: dict, runner: Runner, *, watch_stdin: bool) -> int:
    """Stay connected until stopped or refused; returns the process exit code."""
    stop = asyncio.Event()
    watcher = None
    await runner.workspaces.start()
    if watch_stdin:
        watcher = asyncio.create_task(_watch_stdin(stop))
    tunnel = asyncio.create_task(run_tunnel(
        connection['server_url'], connection['machine_id'], connection['credential'], runner, on_status=emit_status,
    ))
    stopping = asyncio.create_task(stop.wait())
    exit_code = 0
    try:
        await asyncio.wait({tunnel, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if tunnel.done():
            try:
                tunnel.result()
            except PermanentDisconnect as disconnect:
                exit_code = EXIT_CODES.get(disconnect.code, 1)
                emit_status(STOPPED_STATES.get(disconnect.code, 'stopped'), str(disconnect))
    finally:
        # Stop every process and acknowledge it to Buddy before the connection closes.
        await runner.close()
        for task in (tunnel, stopping, watcher):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(tunnel, stopping, *(task for task in (watcher,) if task), return_exceptions=True)
    if exit_code == 0:
        emit_status('stopped')
    return exit_code


def _build_runner(args) -> Runner:
    roots = [{'path': directory, 'write': args.write, 'execute': args.allow_host_execution} for directory in args.root]
    workspaces = WorkspaceCapabilities(roots=roots, origins=[], host_name=args.name)
    return Runner(args.state_dir, workspaces)


def _read_connection(args) -> dict:
    if args.connection_stdin:
        return parse_connection(sys.stdin.readline())
    connection_file = args.connection_file or args.state_dir / CONNECTION_FILE_NAME
    return parse_connection(read_private_text(connection_file, state_dir=connection_file.parent))


def run_command(args) -> int:
    try:
        connection = _read_connection(args)
    except FileNotFoundError:
        print('This computer is not connected to Buddy yet. Connect it from the Buddy app, or run enroll.',
              file=sys.stderr)
        return EXIT_USAGE
    except ValueError as error:
        print(f'Could not read the connection: {error}', file=sys.stderr)
        return EXIT_USAGE
    runner = _build_runner(args)
    log.info('Approved project roots: %s; file write %s; host execution %s',
             len(args.root), args.write, args.allow_host_execution)
    if args.allow_host_execution:
        log.warning('Commands run as this OS account. Their working directory is not a sandbox.')
    return asyncio.run(serve(connection, runner, watch_stdin=args.connection_stdin))


def enroll_command(args) -> int:
    try:
        connection = asyncio.run(enroll(args.server, args.token, args.name))
    except (ValueError, aiohttp.ClientError, OSError) as error:
        print(f'Could not connect this computer: {error}', file=sys.stderr)
        return EXIT_USAGE
    connection_file = args.connection_file or args.state_dir / CONNECTION_FILE_NAME
    # Creates the state folder owner-only when it does not exist yet.
    saved = {key: connection[key] for key in ('server_url', 'machine_id', 'credential')}
    write_private_text(connection_file, json.dumps(saved), state_dir=connection_file.parent)
    print(f'Connected as "{connection["name"]}". Start it with: buddy-runner run', flush=True)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='buddy-runner', description='Run Claude Code and Codex for a Buddy server.')
    commands = parser.add_subparsers(dest='command', required=True)

    run = commands.add_parser('run', help='Stay connected to Buddy and run its requests.')
    source = run.add_mutually_exclusive_group()
    source.add_argument('--connection-stdin', action='store_true', help='Read the connection as one JSON line on stdin.')
    source.add_argument('--connection-file', type=Path, default=None, help='Default: <state-dir>/connection.json')
    run.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    run.add_argument('--root', action='append', default=[], help='Approve one absolute project folder; repeat as needed.')
    run.add_argument('--write', action='store_true', help='Approve edits of existing UTF-8 files in the approved folders.')
    run.add_argument('--allow-host-execution', action='store_true',
                     help='Approve commands as this OS account; a working directory is not a sandbox.')
    run.add_argument('--name', default=None, help='Name shown for this computer in project views.')

    enroll_parser = commands.add_parser('enroll', help='Connect this computer with a token from Buddy.')
    enroll_parser.add_argument('--server', required=True, help='Your Buddy address, such as https://buddy.example.com')
    enroll_parser.add_argument('--token', required=True)
    enroll_parser.add_argument('--name', default=None, help='Name shown for this computer in Buddy.')
    enroll_parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    enroll_parser.add_argument('--connection-file', type=Path, default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', stream=sys.stderr)
    if args.command == 'run':
        return run_command(args)
    return enroll_command(args)
