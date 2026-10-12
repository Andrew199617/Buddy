"""The tunnel between a Runner and its Buddy server.

The Runner connects out to ``wss://<buddy>/api/v1/runners/connect`` and keeps
that one WebSocket open, so it works behind home routers and needs no inbound
port or certificate of its own. Every message is a JSON object with a ``t``
(type) field.

Opening:
  Runner → ``hello``   {protocol, machine_id, credential, runner: {version, instance_id}, info}
  Buddy  → ``welcome`` {protocol, machine: {id, name}}, or closes with a code below.

Calls, Buddy → Runner, answered once each:
  ``call``   {id, method, params}
  ``result`` {id, ok: true, result} or {id, ok: false, error: {status, message}}
  Methods: health, tools.find, paths.check, process.stop, workspace.request,
  folders.list, folders.suggest.

Process streams carry the same messages as a listen-mode Runner's process
WebSocket, wrapped with a stream number ``s`` that Buddy chooses:
  Buddy  → ``open``   {s, start}      start is {type: 'start', args, cwd, env, temp_files, start_id}
  either → ``stream`` {s, message}    commands to the Runner, events to Buddy
  either → ``close``  {s}             that side closed the stream; the Runner then stops the process
"""

PROTOCOL_VERSION = 1

# Close codes. A Runner stops reconnecting after the permanent ones.
CLOSE_BAD_HELLO = 4400
CLOSE_UNKNOWN_MACHINE = 4401
CLOSE_NOT_ALLOWED = 4403
CLOSE_REPLACED = 4409
CLOSE_UNSUPPORTED_VERSION = 4426
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_GOING_AWAY = 1001
PERMANENT_CLOSE_CODES = frozenset({
    CLOSE_BAD_HELLO, CLOSE_UNKNOWN_MACHINE, CLOSE_NOT_ALLOWED, CLOSE_REPLACED, CLOSE_UNSUPPORTED_VERSION,
})

CONNECT_PATH = '/api/v1/runners/connect'
ENROLL_PATH = '/api/v1/runners/enroll'

# Limits shared by both sides.
MAX_HELLO_BYTES = 64 * 1024
HELLO_TIMEOUT_SECONDS = 10
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
# Stdin travels in pieces this size, so one large conversation does not hold
# up the other streams on the shared connection for long.
STDIN_PIECE_CHARS = 256 * 1024
MAX_PENDING_CALLS = 64
MAX_OPEN_STREAMS = 32
CALL_TIMEOUT_SECONDS = 30
HEARTBEAT_SECONDS = 20


class ProtocolError(ValueError):
    """A message that does not follow the tunnel protocol."""


def frame_type(frame) -> str:
    if not isinstance(frame, dict) or not isinstance(frame.get('t'), str):
        raise ProtocolError('Tunnel messages must be objects with a type.')
    return frame['t']


def stream_number(frame: dict) -> int:
    number = frame.get('s')
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ProtocolError('Tunnel stream messages need a stream number.')
    return number


def call_id(frame: dict) -> int:
    number = frame.get('id')
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ProtocolError('Tunnel calls need a numeric ID.')
    return number
