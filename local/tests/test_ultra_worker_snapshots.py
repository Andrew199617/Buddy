"""Offline tests: Ultra workers reach the chat as snapshots, and the chat saves the latest one.

Run from the repository root:
    python -m unittest discover -s local/tests -p test_ultra_worker_snapshots.py -v

A worker starting or ending is sent at once with a status line; changes to
what a worker is doing are sent at most once a second. The chat's event
emitter keeps only the latest snapshot as message.workers. When the chat
stops a reply, the workers still running reach it as stopped.
"""

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / 'backend'))

from test_subscription_preset_rows import load_functions  # noqa: E402

from open_webui.utils.chat_id import is_saved_chat_id  # noqa: E402
from open_webui.utils.subscriptions import streaming  # noqa: E402
from open_webui.utils.subscriptions.events import (  # noqa: E402
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_RUNNING,
    WORKER_STOPPED,
    SubscriptionError,
    TextDelta,
    WorkerUpdate,
)

SOCKET_PATH = ROOT_DIR / 'backend' / 'open_webui' / 'socket' / 'main.py'
MODEL_ID = 'claude-code.fixture-opus'
CHAT_ID = 'fixture-chat'
MESSAGE_ID = 'fixture-message'
START_TIME = 1_800_000_000.0


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = START_TIME):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def research_worker(**changes) -> WorkerUpdate:
    worker = WorkerUpdate(
        worker_id='task-research',
        parent_id=None,
        depth=1,
        label='Research the parser',
        state=WORKER_RUNNING,
        model='sonnet',
        effort='high',
    )
    for name, value in changes.items():
        setattr(worker, name, value)
    return worker


def sub_worker(**changes) -> WorkerUpdate:
    worker = WorkerUpdate(
        worker_id='task-sub',
        parent_id='task-research',
        depth=2,
        label='Read the fixtures',
        state=WORKER_RUNNING,
    )
    for name, value in changes.items():
        setattr(worker, name, value)
    return worker


def snapshot_data(snapshot: dict) -> dict:
    event = snapshot['event']
    if event['type'] != 'chat:message:workers':
        raise AssertionError(f'Not a workers snapshot: {event}')
    return event['data']


async def collect(async_iterable) -> list:
    return [item async for item in async_iterable]


def stream_payloads(events, clock: FakeClock) -> list[dict]:
    """Stream a turn with the snapshot clock under the test's control."""
    real_snapshots = streaming.WorkerSnapshots

    def snapshots_on_the_fixture_clock():
        return real_snapshots(clock=clock)

    with patch.object(streaming, 'WorkerSnapshots', snapshots_on_the_fixture_clock):
        lines = asyncio.run(collect(streaming.stream_events(events, MODEL_ID)))
    return [json.loads(line[6:]) for line in lines if line.startswith('data: {')]


def described(payload: dict) -> str | None:
    """The status line of a status chunk, or None for any other chunk."""
    event = payload.get('event') or {}
    if event.get('type') != 'status':
        return None
    return event['data']['description']


def snapshots_in(payloads: list[dict]) -> list[dict]:
    snapshots = []
    for payload in payloads:
        event = payload.get('event') or {}
        if event.get('type') == 'chat:message:workers':
            snapshots.append(event['data'])
    return snapshots


class WorkerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.workers = streaming.WorkerSnapshots(clock=self.clock)

    def test_a_new_worker_is_sent_at_once(self):
        self.assertTrue(self.workers.update(research_worker()))
        self.assertTrue(self.workers.ready())

        data = snapshot_data(self.workers.snapshot())
        self.assertEqual(data['running'], 1)
        self.assertEqual(
            data['items'],
            [
                {
                    'id': 'task-research',
                    'parent_id': None,
                    'depth': 1,
                    'label': 'Research the parser',
                    'state': WORKER_RUNNING,
                    'model': 'sonnet',
                    'effort': 'high',
                    'activity': None,
                    'summary': None,
                    'tokens': None,
                    'started_at': int(START_TIME),
                    'ended_at': None,
                }
            ],
        )
        self.assertFalse(self.workers.ready())
        self.assertFalse(self.workers.unsent())

    def test_activity_changes_wait_for_the_second_to_pass(self):
        self.workers.update(research_worker())
        self.workers.snapshot()

        self.clock.advance(0.25)
        self.assertFalse(self.workers.update(research_worker(activity='Reading parser.py')))
        self.clock.advance(0.5)
        self.workers.update(research_worker(activity='Running the tests'))
        self.assertFalse(self.workers.ready())
        self.assertTrue(self.workers.unsent())

        self.clock.advance(0.25)
        self.assertTrue(self.workers.ready())
        [item] = snapshot_data(self.workers.snapshot())['items']
        # Only the latest activity goes out; the one in between was never sent.
        self.assertEqual(item['activity'], 'Running the tests')
        self.assertFalse(self.workers.unsent())

    def test_token_changes_are_throttled_like_activity(self):
        self.workers.update(research_worker())
        self.workers.snapshot()

        self.assertFalse(self.workers.update(research_worker(tokens=1200)))
        self.assertFalse(self.workers.ready())
        self.clock.advance(1.0)
        self.assertTrue(self.workers.ready())
        [item] = snapshot_data(self.workers.snapshot())['items']
        self.assertEqual(item['tokens'], 1200)

    def test_a_state_change_is_sent_at_once_even_right_after_a_snapshot(self):
        self.workers.update(research_worker())
        self.clock.advance(0.75)
        self.workers.snapshot()

        # Half a second after the last snapshot, and a second past the start.
        self.clock.advance(0.5)
        finished = research_worker(state=WORKER_DONE, summary='The parser drops nested tasks.')
        self.assertTrue(self.workers.update(finished))
        self.assertTrue(self.workers.ready())

        data = snapshot_data(self.workers.snapshot())
        self.assertEqual(data['running'], 0)
        [item] = data['items']
        self.assertEqual(item['state'], WORKER_DONE)
        self.assertEqual(item['summary'], 'The parser drops nested tasks.')
        self.assertEqual(item['started_at'], int(START_TIME))
        self.assertEqual(item['ended_at'], int(START_TIME) + 1)

    def test_an_unchanged_update_is_not_sent(self):
        self.workers.update(research_worker(activity='Reading parser.py'))
        self.workers.snapshot()

        self.clock.advance(5)
        self.assertFalse(self.workers.update(research_worker(activity='Reading parser.py')))
        self.assertFalse(self.workers.ready())
        self.assertFalse(self.workers.unsent())

    def test_every_snapshot_lists_every_worker_in_start_order(self):
        self.workers.update(research_worker())
        first = snapshot_data(self.workers.snapshot())
        self.workers.update(sub_worker())
        second = snapshot_data(self.workers.snapshot())
        self.workers.update(research_worker(state=WORKER_FAILED))
        third = snapshot_data(self.workers.snapshot())

        self.assertEqual([item['id'] for item in first['items']], ['task-research'])
        self.assertEqual([item['id'] for item in second['items']], ['task-research', 'task-sub'])
        self.assertEqual([item['id'] for item in third['items']], ['task-research', 'task-sub'])
        sub = third['items'][1]
        self.assertEqual((sub['parent_id'], sub['depth']), ('task-research', 2))
        self.assertEqual([second['running'], third['running']], [2, 1])

    def test_a_worker_first_seen_finished_has_its_end_time(self):
        self.assertTrue(self.workers.update(sub_worker(state=WORKER_STOPPED)))
        [item] = snapshot_data(self.workers.snapshot())['items']
        self.assertEqual(item['state'], WORKER_STOPPED)
        self.assertEqual(item['ended_at'], int(START_TIME))

    def test_a_worker_that_runs_again_keeps_its_start_and_loses_its_end(self):
        self.workers.update(research_worker())
        self.clock.advance(30)
        self.workers.update(research_worker(state=WORKER_DONE))
        self.clock.advance(30)
        self.assertTrue(self.workers.update(research_worker()))

        [item] = snapshot_data(self.workers.snapshot())['items']
        self.assertEqual(item['started_at'], int(START_TIME))
        self.assertIsNone(item['ended_at'])

    def test_a_reused_update_object_still_shows_its_changes(self):
        worker = research_worker()
        self.workers.update(worker)
        self.workers.snapshot()

        worker.activity = 'Reading parser.py'
        self.workers.update(worker)
        self.assertTrue(self.workers.unsent())
        self.clock.advance(1)
        worker.state = WORKER_DONE
        self.assertTrue(self.workers.update(worker))
        [item] = snapshot_data(self.workers.snapshot())['items']
        self.assertEqual((item['state'], item['activity']), (WORKER_DONE, 'Reading parser.py'))


class StreamWorkerTests(unittest.TestCase):
    def test_a_worker_gets_a_status_line_then_a_snapshot_when_it_starts_and_ends(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            clock.advance(2)
            yield research_worker(state=WORKER_DONE, summary='Found it.')

        payloads = stream_payloads(events(), clock)
        self.assertEqual(described(payloads[0]), 'Worker started: Research the parser')
        self.assertEqual(snapshot_data(payloads[1])['running'], 1)
        self.assertEqual(described(payloads[2]), 'Worker finished: Research the parser')
        self.assertEqual(snapshot_data(payloads[3])['running'], 0)
        self.assertFalse(payloads[0]['event']['data']['done'])
        # The status lines bring the usual hidden Done line before the end of the reply.
        self.assertTrue(payloads[4]['event']['data']['hidden'])
        self.assertEqual(payloads[5]['choices'][0]['finish_reason'], 'stop')

    def test_failed_and_stopped_workers_say_so(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            yield sub_worker()
            yield sub_worker(state=WORKER_STOPPED, summary='Stopped: too deep')
            yield research_worker(state=WORKER_FAILED)

        statuses = [described(payload) for payload in stream_payloads(events(), clock)]
        self.assertIn('Worker stopped: Read the fixtures', statuses)
        self.assertIn('Worker failed: Research the parser', statuses)

    def test_activity_waits_for_the_second_and_then_rides_the_next_event(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            clock.advance(0.25)
            yield research_worker(activity='Reading parser.py')
            clock.advance(0.25)
            yield research_worker(activity='Running the tests')
            clock.advance(0.5)
            yield TextDelta('Working on it.')
            clock.advance(0.25)
            yield research_worker(state=WORKER_DONE, activity='Running the tests')

        payloads = stream_payloads(events(), clock)
        snapshots = snapshots_in(payloads)
        activities = [snapshot['items'][0]['activity'] for snapshot in snapshots]
        self.assertEqual(activities, [None, 'Running the tests', 'Running the tests'])
        self.assertEqual([snapshot['running'] for snapshot in snapshots], [1, 1, 0])

        text_index = next(index for index, payload in enumerate(payloads) if payload.get('choices'))
        self.assertEqual(payloads[text_index]['choices'][0]['delta'], {'content': 'Working on it.'})
        # The held activity went out right after the text that arrived once its second had passed.
        self.assertEqual(snapshot_data(payloads[text_index + 1])['items'][0]['activity'], 'Running the tests')
        statuses = [described(payload) for payload in payloads if described(payload)]
        expected = ['Worker started: Research the parser', 'Worker finished: Research the parser', 'Done']
        self.assertEqual(statuses, expected)

    def test_a_held_change_is_sent_before_the_reply_ends(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            clock.advance(0.25)
            yield research_worker(activity='Reading parser.py')

        payloads = stream_payloads(events(), clock)
        snapshots = snapshots_in(payloads)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1]['items'][0]['activity'], 'Reading parser.py')
        # The last snapshot comes just before the hidden Done line and the end of the reply.
        self.assertEqual(snapshot_data(payloads[-3])['items'][0]['activity'], 'Reading parser.py')
        self.assertTrue(payloads[-2]['event']['data']['hidden'])
        self.assertEqual(payloads[-1]['choices'][0]['finish_reason'], 'stop')

    def test_a_held_change_is_sent_after_the_turn_fails(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            clock.advance(0.25)
            yield research_worker(activity='Reading parser.py')
            raise SubscriptionError('Claude stopped answering.')

        payloads = stream_payloads(events(), clock)
        error_index = payloads.index({'error': {'message': 'Claude stopped answering.'}})
        self.assertEqual(snapshot_data(payloads[error_index + 1])['items'][0]['activity'], 'Reading parser.py')

    def test_a_repeated_update_sends_nothing_more(self):
        clock = FakeClock()

        async def events():
            yield research_worker()
            clock.advance(3)
            yield research_worker()

        payloads = stream_payloads(events(), clock)
        self.assertEqual(len(snapshots_in(payloads)), 1)
        self.assertEqual([described(payload) for payload in payloads].count('Worker started: Research the parser'), 1)

    def test_a_reply_without_workers_sends_no_snapshot(self):
        async def events():
            yield TextDelta('Hello')

        payloads = stream_payloads(events(), FakeClock())
        self.assertEqual(snapshots_in(payloads), [])
        self.assertEqual([described(payload) for payload in payloads if described(payload)], [])

    def test_a_collected_reply_leaves_the_workers_out(self):
        async def events():
            yield research_worker()
            yield TextDelta('Hi')
            yield research_worker(state=WORKER_DONE)

        completion = asyncio.run(streaming.collect_events(events(), MODEL_ID))
        self.assertEqual(completion['choices'][0]['message'], {'role': 'assistant', 'content': 'Hi'})
        self.assertNotIn('workers', json.dumps(completion))


class HeldTurn:
    """A turn's events, held until the chat stops the reply.

    Its worker writes on and its sub-worker has finished, or it has only text.
    """

    # What the reply sends before the turn waits: a status line and a snapshot for each of the
    # workers' three updates, or the text of a turn without workers.
    CHUNKS_WITH_WORKERS = 6
    CHUNKS_WITHOUT_WORKERS = 1

    def __init__(self, workers=True):
        self.workers = workers
        self.waiting = asyncio.Event()
        # What happened once the chat stopped the reply, in order.
        self.after_stop = []

    async def events(self):
        try:
            if self.workers:
                yield research_worker(activity='Reading parser.py')
                yield sub_worker()
                yield sub_worker(state=WORKER_DONE, summary='Read them.')
            else:
                yield TextDelta('Working on it.')
            self.waiting.set()
            await asyncio.Event().wait()
        finally:
            # The provider stops the turn's workers here.
            self.after_stop.append('turn closed')

    def chunks_before_waiting(self) -> int:
        if self.workers:
            return self.CHUNKS_WITH_WORKERS
        return self.CHUNKS_WITHOUT_WORKERS


async def read_until_the_turn_waits(turn: HeldTurn, stream) -> list[str]:
    """Read the reply as the chat does, up to its last chunk before the turn waits.

    The chat is then handing that chunk on when Stop lands.
    """
    chunks = []
    for _ in range(turn.chunks_before_waiting()):
        chunks.append(await anext(stream))
    return chunks


class StoppedReplyTests(unittest.IsolatedAsyncioTestCase):
    """The chat stops reading a reply when it stops it, so its workers' last snapshot goes to the emitter given."""

    def setUp(self):
        self.turn = HeldTurn()
        self.emitted = []

    async def emit(self, event):
        self.turn.after_stop.append('snapshot sent')
        self.emitted.append(event)

    def stream(self, emit=None):
        if emit is None:
            emit = self.emit
        return streaming.stream_events(self.turn.events(), MODEL_ID, emit_when_stopped=emit)

    def assert_sent_with_the_running_worker_stopped(self):
        [event] = self.emitted
        self.assertEqual(event['type'], streaming.WORKERS_EVENT_TYPE)
        self.assertEqual(event['data']['running'], 0)
        research, sub = event['data']['items']
        self.assertEqual(research['id'], 'task-research')
        self.assertEqual(research['state'], WORKER_STOPPED)
        self.assertIsNone(research['activity'])
        self.assertEqual(research['summary'], streaming.WORKER_STOPPED_WITH_REPLY)
        self.assertIsInstance(research['ended_at'], int)
        # A worker that had ended keeps how it ended.
        self.assertEqual((sub['state'], sub['summary']), (WORKER_DONE, 'Read them.'))
        # The turn is closed first, so its provider has stopped the workers by then.
        self.assertEqual(self.turn.after_stop, ['turn closed', 'snapshot sent'])

    async def test_a_stop_while_the_chat_hands_on_a_chunk_sends_the_running_worker_stopped(self):
        stream = self.stream()
        chunks = await read_until_the_turn_waits(self.turn, stream)
        self.assertEqual(snapshot_data(json.loads(chunks[-1][6:]))['running'], 1)
        # The chat closes the stream, as middleware.py does when Stop lands while it hands a chunk on.
        await stream.aclose()
        self.assert_sent_with_the_running_worker_stopped()

    async def test_a_stop_while_the_reply_waits_for_its_turn_sends_it_stopped_too(self):
        task = asyncio.create_task(collect(self.stream()))
        await asyncio.wait_for(self.turn.waiting.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_sent_with_the_running_worker_stopped()

    async def test_a_reply_that_ends_on_its_own_sends_nothing_after_it(self):
        async def events():
            yield research_worker()
            yield research_worker(state=WORKER_DONE)

        payloads = await collect(streaming.stream_events(events(), MODEL_ID, emit_when_stopped=self.emit))
        self.assertEqual(self.emitted, [])
        self.assertTrue(payloads)

    async def test_a_stopped_reply_without_workers_sends_nothing(self):
        self.turn = HeldTurn(workers=False)
        stream = self.stream()
        await read_until_the_turn_waits(self.turn, stream)
        await stream.aclose()
        self.assertEqual(self.emitted, [])
        self.assertEqual(self.turn.after_stop, ['turn closed'])

    async def test_a_snapshot_that_cannot_be_sent_still_lets_the_reply_stop(self):
        async def broken_emit(event):
            raise RuntimeError('The chat is gone')

        stream = self.stream(broken_emit)
        await read_until_the_turn_waits(self.turn, stream)
        with self.assertLogs(streaming.log, 'ERROR'):
            await stream.aclose()
        self.assertEqual(self.turn.after_stop, ['turn closed'])


def saved_workers() -> dict:
    """A message's saved workers: a worker that was writing on, and a sub-worker that had finished."""
    workers = streaming.WorkerSnapshots(clock=FakeClock())
    workers.update(research_worker(activity='Reading parser.py'))
    workers.update(sub_worker())
    workers.update(sub_worker(state=WORKER_DONE, summary='Read them.'))
    return workers.snapshot()['event']['data']


class StoppedSavedWorkersTests(unittest.TestCase):
    """A reply that ended without its stream, as after a server restart, keeps no worker running."""

    def test_each_worker_saved_as_running_is_stopped(self):
        saved = saved_workers()
        stopped = streaming.stopped_saved_workers(saved, 1_800_000_100)
        self.assertEqual(stopped['running'], 0)
        research, sub = stopped['items']
        self.assertEqual(research['state'], WORKER_STOPPED)
        self.assertIsNone(research['activity'])
        self.assertEqual(research['summary'], streaming.WORKER_STOPPED_WITH_REPLY)
        self.assertEqual(research['ended_at'], 1_800_000_100)
        self.assertEqual(research['started_at'], saved['items'][0]['started_at'])
        # A worker that had ended keeps how it ended, and the saved snapshot is left as it was.
        self.assertEqual(sub, saved['items'][1])
        self.assertEqual(saved['items'][0]['state'], WORKER_RUNNING)

    def test_a_snapshot_without_running_workers_needs_no_change(self):
        saved = saved_workers()
        saved['items'] = saved['items'][1:]
        saved['running'] = 0
        self.assertIsNone(streaming.stopped_saved_workers(saved, 1_800_000_100))

    def test_anything_that_is_not_a_snapshot_is_left_alone(self):
        for saved in (None, [], {'running': 1}, {'items': 'task-research'}):
            with self.subTest(saved=saved):
                self.assertIsNone(streaming.stopped_saved_workers(saved, 1_800_000_100))


class StandInSocketServer:
    """The Socket.IO server, recording what the emitter sends to the person's browser."""

    def __init__(self):
        self.manager = types.SimpleNamespace(rooms={'/': {'user:fixture-owner': {'fixture-session'}}})
        self.emitted = []

    async def emit(self, event, data, room=None):
        self.emitted.append((event, data, room))


class StandInChats:
    """Chats, keeping one chat's messages in memory with the real merge rule."""

    def __init__(self):
        self.messages = {MESSAGE_ID: {'id': MESSAGE_ID, 'role': 'assistant', 'content': ''}}
        self.upserts = []
        self.metadata_reads = []

    async def upsert_message_to_chat_by_id_and_message_id(self, chat_id, message_id, message, *, touch=True):
        self.upserts.append({'chat_id': chat_id, 'message_id': message_id, 'message': message, 'touch': touch})
        self.messages[message_id] = {**self.messages.get(message_id, {}), **message}

    async def get_message_metadata(self, chat_id, message_id, metadata_key):
        self.metadata_reads.append(metadata_key)
        return self.messages.get(message_id, {}).get(metadata_key)


class SocketWorkersTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sio = StandInSocketServer()
        self.chats = StandInChats()

        async def no_channel_emitter(request_info):
            raise AssertionError('Ultra replies are never channel messages')

        self.socket = {
            'WEBSOCKET_MANAGER': 'local',
            'sio': self.sio,
            'Chats': self.chats,
            'is_saved_chat_id': is_saved_chat_id,
            '_make_channel_emitter': no_channel_emitter,
        }
        load_functions(SOCKET_PATH, {'get_event_emitter'}, self.socket)

    async def emitter(self, chat_id=CHAT_ID):
        request_info = {'user_id': 'fixture-owner', 'chat_id': chat_id, 'message_id': MESSAGE_ID}
        return await self.socket['get_event_emitter'](request_info)

    def streamed_snapshots(self) -> list[dict]:
        """Two snapshots as stream_events sends them: a worker starting, then a second one joining."""
        workers = streaming.WorkerSnapshots(clock=FakeClock())
        workers.update(research_worker())
        first = workers.snapshot()['event']
        workers.update(sub_worker())
        second = workers.snapshot()['event']
        return [first, second]

    async def test_a_snapshot_is_saved_as_the_messages_workers_without_touching_the_chat(self):
        emit = await self.emitter()
        first, _ = self.streamed_snapshots()
        await emit(first)

        [upsert] = self.chats.upserts
        self.assertEqual(upsert['chat_id'], CHAT_ID)
        self.assertEqual(upsert['message_id'], MESSAGE_ID)
        self.assertEqual(upsert['message'], {'workers': first['data']})
        self.assertFalse(upsert['touch'])
        self.assertEqual(self.chats.messages[MESSAGE_ID]['workers']['running'], 1)

    async def test_a_later_snapshot_replaces_the_saved_one(self):
        emit = await self.emitter()
        first, second = self.streamed_snapshots()
        await emit(first)
        await emit(second)

        saved = self.chats.messages[MESSAGE_ID]['workers']
        self.assertEqual(saved, second['data'])
        self.assertEqual([item['id'] for item in saved['items']], ['task-research', 'task-sub'])
        # Replaced rather than appended: the saved workers are never read back.
        self.assertEqual(self.chats.metadata_reads, [])
        self.assertEqual(self.chats.messages[MESSAGE_ID]['content'], '')

    async def test_the_snapshot_still_reaches_the_persons_browser(self):
        emit = await self.emitter()
        first, _ = self.streamed_snapshots()
        await emit(first)

        [(event, data, room)] = self.sio.emitted
        self.assertEqual((event, room), ('events', 'user:fixture-owner'))
        self.assertEqual(data, {'chat_id': CHAT_ID, 'message_id': MESSAGE_ID, 'data': first})

    async def test_a_malformed_snapshot_is_not_saved(self):
        emit = await self.emitter()
        not_snapshots = [
            {'type': 'chat:message:workers'},
            {'type': 'chat:message:workers', 'data': None},
            {'type': 'chat:message:workers', 'data': ['task-research']},
            {'type': 'chat:message:workers', 'data': {'running': 1}},
            {'type': 'chat:message:workers', 'data': {'items': 'task-research', 'running': 1}},
        ]
        for event in not_snapshots:
            with self.subTest(event=event):
                await emit(event)
        self.assertEqual(self.chats.upserts, [])
        self.assertNotIn('workers', self.chats.messages[MESSAGE_ID])

    async def test_a_stopped_replys_last_snapshot_replaces_the_saved_one(self):
        emit = await self.emitter()
        turn = HeldTurn()
        stream = streaming.stream_events(turn.events(), MODEL_ID, emit_when_stopped=emit)
        # The chat hands each snapshot on to its emitter, as middleware.py does, until Stop lands.
        for chunk in await read_until_the_turn_waits(turn, stream):
            event = json.loads(chunk[6:])['event']
            if event['type'] == streaming.WORKERS_EVENT_TYPE:
                await emit(event)
        self.assertEqual(self.chats.messages[MESSAGE_ID]['workers']['running'], 1)
        await stream.aclose()

        saved = self.chats.messages[MESSAGE_ID]['workers']
        self.assertEqual(saved['running'], 0)
        self.assertEqual([item['state'] for item in saved['items']], [WORKER_STOPPED, WORKER_DONE])
        self.assertFalse(self.chats.upserts[-1]['touch'])
        # The person's open chat is told too.
        last_sent = self.sio.emitted[-1][1]['data']
        self.assertEqual(last_sent['data'], saved)

    async def test_a_temporary_chat_is_sent_the_snapshot_but_saves_nothing(self):
        emit = await self.emitter(chat_id='local:fixture-temporary')
        first, _ = self.streamed_snapshots()
        await emit(first)

        self.assertEqual(self.chats.upserts, [])
        self.assertEqual(len(self.sio.emitted), 1)


if __name__ == '__main__':
    unittest.main()
