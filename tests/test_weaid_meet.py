"""Connection-scoped meeting lifecycle tests without the Pipecat runner."""

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI


def load_meet():
    runner = types.ModuleType("pipecat.runner.run")
    runner.app = FastAPI()
    spec = importlib.util.spec_from_file_location(
        "_weaid_meet_test", Path(__file__).resolve().parents[1] / "weaid_meet.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"pipecat.runner.run": runner, spec.name: module}):
        spec.loader.exec_module(module)
    return module


meet = load_meet()


class FakeWebSocket:
    def __init__(self, headers=None):
        self.headers = headers if headers is not None else {"host": "meet.example"}
        self.incoming = asyncio.Queue()
        self.messages = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        while True:
            event = await self.incoming.get()
            if isinstance(event, asyncio.Event):
                event.set()
            else:
                return event

    async def send_text(self, text):
        if self.closed is not None:
            raise RuntimeError("Closed")
        self.messages.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = code
        await self.incoming.put({"type": "websocket.disconnect"})

    async def send(self, message):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps(message)})
        await self.flush()

    async def flush(self):
        barrier = asyncio.Event()
        await self.incoming.put(barrier)
        await asyncio.wait_for(barrier.wait(), 2)

    def take(self, kind):
        return [message for message in self.messages if message["type"] == kind]


class MeetingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        meet._rooms = meet.RoomManager()
        self.connections = []

    async def asyncTearDown(self):
        for ws, task in self.connections:
            if not task.done():
                await ws.incoming.put({"type": "websocket.disconnect"})
        await asyncio.gather(*(task for _, task in self.connections))

    async def connect(self, did, room="room", **kwargs):
        ws = FakeWebSocket()
        task = asyncio.create_task(meet.meet_signaling(ws, room))
        self.connections.append((ws, task))
        await ws.flush()
        await ws.incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "join",
                        "did": did,
                        "name": did,
                        "audio": True,
                        "video": True,
                        **kwargs,
                    }
                ),
            }
        )
        # Rejected joins exit rather than receiving another message.
        barrier = asyncio.Event()
        await ws.incoming.put(barrier)
        waiter = asyncio.create_task(barrier.wait())
        done, _ = await asyncio.wait([task, waiter], timeout=2, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            self.fail("Join timed out")
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        return ws

    async def disconnect(self, ws):
        await ws.incoming.put({"type": "websocket.disconnect"})
        await next(task for socket, task in self.connections if socket is ws)

    async def admit(self, host, did):
        await host.send({"type": "admit", "did": did})

    async def test_first_host_and_waiting_admission(self):
        host = await self.connect("did:host", audio=False, video=False)
        self.assertEqual(
            host.take("joined")[0],
            {
                "type": "joined",
                "did": "did:host",
                "room": "room",
                "peers": [],
                "chat_history": [],
                "host": "did:host",
                "locked": False,
            },
        )
        guest = await self.connect("did:guest", video=False)
        room = meet._rooms.rooms["room"]
        self.assertNotIn("did:guest", room.participants)
        self.assertEqual(guest.take("joined"), [])
        self.assertEqual(host.take("waiting-list")[-1]["participants"][0]["did"], "did:guest")
        await self.admit(host, "did:guest")
        self.assertTrue(room.owns(room.participants["did:guest"]))
        self.assertEqual(guest.take("joined")[0]["peers"][0]["audio"], False)
        self.assertEqual(host.take("peer-joined")[-1]["video"], False)
        self.assertEqual(host.take("waiting-list")[-1]["participants"], [])

    async def test_pending_has_no_privileges_or_room_events(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        host.messages.clear()
        for message in [
            {"type": "chat", "text": "intrusion"},
            {"type": "offer", "to": "did:host", "sdp": "offer"},
            {"type": "admit", "did": "did:guest"},
            {"type": "room-lock", "locked": True},
            {"type": "hand-state", "raised": True},
            {"type": "recording-state", "recording": True},
            {"type": "caption", "text": "intrusion"},
        ]:
            await guest.send(message)
        self.assertEqual(host.messages, [])
        self.assertEqual(meet._rooms.rooms["room"].chat, [])
        await host.send({"type": "chat", "text": "private"})
        self.assertEqual(guest.take("chat"), [])

    async def test_rejected_connection_cannot_inject_room_events(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        room = meet._rooms.rooms["room"]
        rejected = room.pending["did:guest"]
        await host.send({"type": "reject", "did": "did:guest"})
        self.assertTrue(guest.take("rejected"))
        host.messages.clear()
        for message in [
            {"type": "caption", "text": "intrusion"},
            {"type": "recording-state", "recording": True},
            {"type": "hand-state", "raised": True},
            {"type": "chat", "text": "intrusion"},
            {"type": "offer", "to": "did:host", "sdp": "intrusion"},
            {"type": "answer", "to": "did:host", "sdp": "intrusion"},
            {"type": "ice", "to": "did:host", "candidate": {"candidate": "intrusion"}},
        ]:
            await meet._handle_message(room, rejected, message, "room")
        self.assertEqual(host.messages, [])
        self.assertEqual(room.chat, [])
        self.assertFalse(rejected.recording)
        self.assertFalse(rejected.raised)

    async def test_duplicate_and_repeat_join_cannot_replace_owner(self):
        host = await self.connect("did:host")
        duplicate = await self.connect("did:host")
        self.assertTrue(duplicate.take("rejected"))
        await host.send({"type": "join", "did": "did:other", "name": "Other"})
        self.assertTrue(host.take("error"))
        self.assertIs(meet._rooms.rooms["room"].participants["did:host"].ws, host)
        self.assertNotIn("did:other", meet._rooms.rooms["room"].participants)
        guest = await self.connect("did:guest")
        duplicate_pending = await self.connect("did:guest")
        self.assertTrue(duplicate_pending.take("rejected"))
        self.assertIs(meet._rooms.rooms["room"].pending["did:guest"].ws, guest)

    async def test_connection_identity_not_did_authorizes(self):
        host = await self.connect("did:host")
        room = meet._rooms.rooms["room"]
        attacker = meet.Participant("did:host", "Attacker", FakeWebSocket())
        await meet._handle_message(room, attacker, {"type": "room-lock", "locked": True}, "room")
        self.assertFalse(room.locked)
        await meet._rooms.leave("room", room, attacker)
        self.assertIs(room.participants["did:host"].ws, host)

    async def test_host_only_actions_and_succession(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        pending = await self.connect("did:pending")
        for message in [
            {"type": "admit", "did": "did:pending", "host": "did:host"},
            {"type": "reject", "did": "did:pending"},
            {"type": "room-lock", "locked": True},
        ]:
            await guest.send(message)
        self.assertEqual(len(guest.take("error")), 3)
        await self.disconnect(host)
        self.assertEqual(guest.take("room-state")[-1]["host"], "did:guest")
        self.assertEqual(guest.take("waiting-list")[-1]["participants"][0]["did"], "did:pending")
        await self.admit(guest, "did:pending")
        self.assertEqual(pending.take("joined")[0]["host"], "did:guest")

    async def test_locked_rejected_and_existing_pending_can_be_admitted(self):
        host = await self.connect("did:host")
        pending = await self.connect("did:pending")
        await host.send({"type": "room-lock", "locked": True})
        blocked = await self.connect("did:blocked")
        self.assertEqual(blocked.take("rejected")[-1]["message"], "Meeting is locked")
        await self.admit(host, "did:pending")
        self.assertTrue(pending.take("joined")[-1]["locked"])
        await host.send({"type": "room-lock", "locked": False})
        guest = await self.connect("did:guest")
        self.assertTrue(guest.take("waiting"))

    async def test_reject_disconnect_and_room_retirement(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await host.send({"type": "reject", "did": "did:guest"})
        self.assertTrue(guest.take("rejected"))
        another = await self.connect("did:another")
        old_room = meet._rooms.rooms["room"]
        old_pending = old_room.pending["did:another"]
        await self.disconnect(host)
        self.assertTrue(another.take("rejected"))
        self.assertNotIn("room", meet._rooms.rooms)
        replacement = await self.connect("did:another")
        await meet._rooms.leave("room", old_room, old_pending)
        self.assertIs(meet._rooms.rooms["room"].participants["did:another"].ws, replacement)
        self.assertEqual(replacement.take("joined")[0]["chat_history"], [])

    async def test_pending_disconnect_updates_host(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await self.disconnect(guest)
        self.assertEqual(host.take("waiting-list")[-1]["participants"], [])

    async def test_oldest_admitted_connection_inherits_host(self):
        host = await self.connect("did:host")
        later_admitted = await self.connect("did:later")
        first_admitted = await self.connect("did:first")
        await self.admit(host, "did:first")
        await self.admit(host, "did:later")
        await self.disconnect(host)
        self.assertEqual(meet._rooms.rooms["room"].host, "did:first")
        await self.disconnect(first_admitted)
        self.assertEqual(later_admitted.take("room-state")[-1]["host"], "did:later")

    async def test_old_connection_cleanup_preserves_same_label_replacement(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        room = meet._rooms.rooms["room"]
        old = room.participants["did:guest"]
        await self.disconnect(guest)
        replacement = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        await meet._rooms.leave("room", room, old)
        self.assertIs(room.participants["did:guest"].ws, replacement)

    async def test_signaling_sender_is_server_owned_and_room_scoped(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        other = await self.connect("did:other", room="other")
        for kind, key, value in [
            ("offer", "sdp", "offer"),
            ("answer", "sdp", "answer"),
            ("ice", "candidate", {"candidate": "candidate"}),
        ]:
            await guest.send(
                {"type": kind, "to": "did:host", "from": "did:forged", "host": True, key: value}
            )
            self.assertEqual(
                host.take(kind)[-1],
                {"type": kind, "to": "did:host", "from": "did:guest", key: value},
            )
        await guest.send({"type": "offer", "to": "did:other", "sdp": "secret"})
        self.assertEqual(other.take("offer"), [])

    async def test_hand_recording_caption_and_media_states(self):
        host = await self.connect("did:host")
        guest = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        await guest.send({"type": "hand-state", "raised": True, "did": "did:host"})
        await guest.send({"type": "recording-state", "recording": True})
        await guest.send({"type": "caption", "text": "x" * 800, "name": "Forged"})
        self.assertEqual(host.take("hand-state")[-1]["did"], "did:guest")
        self.assertEqual(guest.take("recording-state")[-1]["name"], "did:guest")
        self.assertEqual(len(host.take("caption")[-1]["text"]), 500)
        self.assertEqual(meet._rooms.rooms["room"].chat, [])
        await guest.send({"type": "media-state", "audio": False})
        await guest.send({"type": "caption", "text": "muted"})
        self.assertEqual(len(host.take("caption")), 1)
        newcomer = await self.connect("did:new")
        await self.admit(host, "did:new")
        info = newcomer.take("joined")[0]["peers"][1]
        self.assertTrue(info["raised"])
        self.assertTrue(info["recording"])
        self.assertFalse(info["audio"])
        await guest.send({"type": "hand-state", "raised": False})
        await guest.send({"type": "recording-state", "recording": False})
        self.assertFalse(host.take("hand-state")[-1]["raised"])
        self.assertFalse(guest.take("recording-state")[-1]["recording"])

    async def test_chat_is_typed_trimmed_bounded_and_replayed(self):
        host = await self.connect("did:host")
        for text in [None, {}, [], 1, "", "  "]:
            await host.send({"type": "chat", "text": text})
        self.assertEqual(meet._rooms.rooms["room"].chat, [])
        await host.send({"type": "chat", "text": "  hello  ", "from": "fake", "name": "fake"})
        self.assertEqual(host.take("chat")[-1]["text"], "hello")
        self.assertEqual(host.take("chat")[-1]["from"], "did:host")
        for i in range(205):
            await host.send({"type": "chat", "text": str(i)})
        self.assertEqual(len(meet._rooms.rooms["room"].chat), 200)
        guest = await self.connect("did:guest")
        await self.admit(host, "did:guest")
        history = guest.take("joined")[0]["chat_history"]
        self.assertEqual(len(history), 30)
        self.assertEqual(history[-1]["text"], "204")

    async def test_malformed_messages_do_not_disconnect_admitted_connection(self):
        host = await self.connect("did:host")
        for raw in ["null", "[]", "1", "{", '{"type":[]}', "[" * 2000, "x" * 65537]:
            await host.incoming.put({"type": "websocket.receive", "text": raw})
            await host.flush()
        await host.incoming.put({"type": "websocket.receive", "bytes": b"binary"})
        await host.flush()
        for message in [
            {"type": "offer", "to": [], "sdp": {}},
            {"type": "reject", "did": {}},
            {"type": "media-state", "audio": "false"},
            {"type": "room-lock", "locked": []},
            {"type": "hand-state", "raised": 1},
            {"type": "recording-state", "recording": "true"},
        ]:
            await host.send(message)
        await host.send({"type": "chat", "text": "still connected"})
        self.assertEqual(host.take("chat")[-1]["text"], "still connected")
        self.assertTrue(meet._rooms.rooms["room"].participants["did:host"].audio)

    async def test_invalid_join_can_be_corrected(self):
        ws = FakeWebSocket()
        task = asyncio.create_task(meet.meet_signaling(ws, "room"))
        self.connections.append((ws, task))
        for did, name, audio in [
            ([], "Name", True),
            ("did:", "Name", True),
            ("did:" + "x" * 256, "Name", True),
            ("did:valid", {}, True),
            ("did:valid", "Name", "false"),
            ("did:valid", "x" * 41, True),
            ("did:valid", "bad\nname", True),
        ]:
            await ws.send({"type": "join", "did": did, "name": name, "audio": audio})
        self.assertEqual(meet._rooms.rooms, {})
        await ws.send({"type": "join", "did": "did:valid", "name": "Name", "audio": False})
        self.assertTrue(ws.take("joined"))
        self.assertFalse(meet._rooms.rooms["room"].participants["did:valid"].audio)

    async def test_capacity_bounds(self):
        host = await self.connect("did:host")
        with patch.object(meet, "MAX_PENDING", 1):
            await self.connect("did:pending")
            overflow = await self.connect("did:overflow")
            self.assertTrue(overflow.take("rejected"))
        with patch.object(meet, "MAX_PARTICIPANTS", 1):
            await self.admit(host, "did:pending")
            self.assertIn("did:pending", meet._rooms.rooms["room"].pending)
            self.assertTrue(host.take("error"))
        with patch.object(meet, "MAX_ROOMS", 1):
            overflow_room = await self.connect("did:other", room="other")
            self.assertTrue(overflow_room.take("rejected"))
            self.assertNotIn("other", meet._rooms.rooms)

    async def test_origin_and_room_validation_before_accept(self):
        for room, origin in [
            ("a/b", None),
            ("x" * 129, None),
            ("", None),
            ("room", "https://evil.example"),
            ("room", "null"),
            ("room", "https://meet.example.evil"),
            ("room", "https://user@meet.example"),
        ]:
            ws = FakeWebSocket({"host": "meet.example", **({"origin": origin} if origin else {})})
            await meet.meet_signaling(ws, room)
            self.assertFalse(ws.accepted)
            self.assertEqual(ws.closed, 1008)
        self.assertTrue(meet._same_origin(FakeWebSocket()))
        self.assertTrue(
            meet._same_origin(
                FakeWebSocket({"host": "meet.example", "origin": "https://meet.example"})
            )
        )

    async def test_config_default_turn_validation_and_no_store(self):
        self.assertIn("/api/meet/config", [route.path for route in meet.app.routes])
        with patch.dict("os.environ", {}, clear=True):
            response = await meet.meet_config()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            json.loads(response.body), {"iceServers": [{"urls": "stun:stun.l.google.com:19302"}]}
        )
        turn = [
            {
                "urls": ["turn:relay.example:3478?transport=udp", "turns:relay.example:5349"],
                "username": "public-browser-user",
                "credential": "example-public-credential",
            }
        ]
        with patch.dict("os.environ", {"WEAID_MEET_ICE_SERVERS": json.dumps(turn)}):
            self.assertEqual(meet._ice_servers(), turn)
        for value in [
            "{}",
            "null",
            "[]",
            "invalid",
            '[{"urls":"https://evil.example"}]',
            '[{"urls":"turn:relay.example","credential":42}]',
            '[{"urls":"turn:relay.example:99999"}]',
            '[{"urls":"turn:relay.example:0"}]',
            '[{"urls":"turn:[:]"}]',
        ]:
            with patch.dict("os.environ", {"WEAID_MEET_ICE_SERVERS": value}):
                self.assertEqual(meet._ice_servers(), [{"urls": "stun:stun.l.google.com:19302"}])


if __name__ == "__main__":
    unittest.main()
