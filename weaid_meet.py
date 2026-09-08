"""WeAid Meet — DID 기반 탈중앙화 실시간 다자간 음성·영상·채팅.

Creator: HAPPYTALKMAN (이길환)

표준:
  - W3C DID Core 1.0  (did:key, Ed25519)
  - Web Crypto API    (SubtleCrypto — 브라우저 내장)
  - WebRTC            (음성·영상·데이터채널)
  - FastAPI WebSocket (시그널링 서버)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from pipecat.runner.run import app

# ─────────────────────────────────────────────────────────────────
#  Room state
# ─────────────────────────────────────────────────────────────────

MAX_ROOMS = 256
MAX_PARTICIPANTS = 32
MAX_PENDING = 64
MAX_MESSAGE_BYTES = 65536


@dataclass
class Participant:
    """A connection-scoped participant; DID labels are not authenticated identities."""

    did: str
    name: str
    ws: WebSocket
    audio: bool = True
    video: bool = True
    raised: bool = False
    recording: bool = False
    joined_at: float = field(default_factory=time.time)

    def info(self) -> dict:
        """Return the public participant state."""
        return {
            "did": self.did,
            "name": self.name,
            "audio": self.audio,
            "video": self.video,
            "raised": self.raised,
            "recording": self.recording,
        }


@dataclass
class MeetingRoom:
    """Admitted and waiting connections with serialized room transitions."""

    participants: dict[str, Participant] = field(default_factory=dict)
    pending: dict[str, Participant] = field(default_factory=dict)
    chat: list[dict] = field(default_factory=list)
    host: str = ""
    locked: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def owns(self, participant: Participant) -> bool:
        """Check membership by connection identity rather than a supplied label."""
        return self.participants.get(participant.did) is participant

    def state(self) -> dict:
        """Return host and admission controls."""
        return {"host": self.host, "locked": self.locked}


async def _send(websocket: WebSocket, message: dict) -> None:
    try:
        await asyncio.wait_for(
            websocket.send_text(json.dumps(message, ensure_ascii=False)), timeout=2
        )
    except Exception:
        pass


async def _reject(participant: Participant, reason: str) -> None:
    await _send(participant.ws, {"type": "rejected", "message": reason})
    try:
        await asyncio.wait_for(participant.ws.close(code=1008), timeout=2)
    except Exception:
        pass


class RoomManager:
    """Maintain process-local meetings and connection-scoped admission."""

    def __init__(self):
        """Initialize an empty meeting registry."""
        self.rooms: dict[str, MeetingRoom] = {}

    def room_list(self) -> list:
        """List active rooms without exposing waiting participants."""
        return [
            {"id": rid, "count": len(room.participants)}
            for rid, room in self.rooms.items()
            if room.participants
        ]

    async def broadcast(self, room: MeetingRoom, message: dict, exclude: str = "") -> None:
        """Send an event only to admitted connections."""
        await asyncio.gather(
            *(_send(p.ws, message) for did, p in room.participants.items() if did != exclude)
        )

    async def waiting_list(self, room: MeetingRoom) -> None:
        """Send pending admission requests to the current host."""
        host = room.participants.get(room.host)
        if host:
            await _send(
                host.ws,
                {"type": "waiting-list", "participants": [p.info() for p in room.pending.values()]},
            )

    async def admitted(self, room_id: str, room: MeetingRoom, p: Participant) -> None:
        """Send admission state and announce the participant to existing peers."""
        peers = [peer.info() for did, peer in room.participants.items() if did != p.did]
        await _send(
            p.ws,
            {
                "type": "joined",
                "did": p.did,
                "room": room_id,
                "peers": peers,
                "chat_history": room.chat[-30:],
                **room.state(),
            },
        )
        await self.broadcast(room, {"type": "peer-joined", **p.info()}, exclude=p.did)

    async def leave(self, room_id: str, room: MeetingRoom, p: Participant) -> None:
        """Remove the owning connection and transfer hosting or retire its room."""
        async with room.lock:
            if self.rooms.get(room_id) is not room:
                return
            if room.pending.get(p.did) is p:
                del room.pending[p.did]
                await self.waiting_list(room)
            elif room.owns(p):
                del room.participants[p.did]
                if not room.participants:
                    # Retire the room before awaiting I/O so an old socket cannot recreate it.
                    del self.rooms[room_id]
                    pending = list(room.pending.values())
                    room.pending.clear()
                    await asyncio.gather(*(_reject(peer, "Meeting ended") for peer in pending))
                    return
                await self.broadcast(room, {"type": "peer-left", "did": p.did, "name": p.name})
                if room.host == p.did:
                    room.host = next(iter(room.participants))
                    await self.broadcast(room, {"type": "room-state", **room.state()})
                    await self.waiting_list(room)


_rooms = RoomManager()


# ─────────────────────────────────────────────────────────────────
#  REST endpoints
# ─────────────────────────────────────────────────────────────────


def _ice_servers() -> list[dict]:
    """Read public browser ICE configuration, including intentionally public TURN credentials."""
    default = [{"urls": "stun:stun.l.google.com:19302"}]
    raw = os.environ.get("WEAID_MEET_ICE_SERVERS")
    if not raw:
        return default
    try:
        if len(raw) > 16384:
            raise ValueError("ICE configuration too large")
        servers = json.loads(raw)
        if not isinstance(servers, list) or not 1 <= len(servers) <= 16:
            raise ValueError("ICE servers must be a nonempty list")
        validated = []
        for server in servers:
            if not isinstance(server, dict):
                raise ValueError("Invalid ICE server")
            urls = server.get("urls")
            items = [urls] if isinstance(urls, str) else urls
            if not isinstance(items, list) or not 1 <= len(items) <= 16:
                raise ValueError("Invalid ICE URLs")
            for url in items:
                if (
                    not isinstance(url, str)
                    or len(url) > 2048
                    or not re.fullmatch(
                        r"(?:stun|stuns|turn|turns):(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])"
                        r"(?::[0-9]{1,5})?(?:\?transport=(?:udp|tcp))?",
                        url,
                    )
                ):
                    raise ValueError("Invalid ICE URL")
                address = urlsplit("//" + url.split(":", 1)[1])
                if address.port == 0:
                    raise ValueError("Invalid ICE port")
            clean = {"urls": urls}
            for key in ("username", "credential"):
                if key in server:
                    if not isinstance(server[key], str) or len(server[key]) > 2048:
                        raise ValueError("Invalid ICE credential")
                    clean[key] = server[key]
            validated.append(clean)
        return validated
    except (ValueError, TypeError, RecursionError):
        logger.warning("[WeAid Meet] Invalid WEAID_MEET_ICE_SERVERS; using default STUN")
        return default


@app.get("/api/meet/config", include_in_schema=False)
async def meet_config():
    """Expose browser-public ICE servers without caching TURN credentials."""
    return JSONResponse({"iceServers": _ice_servers()}, headers={"Cache-Control": "no-store"})


@app.get("/api/meet/rooms", include_in_schema=False)
async def list_rooms():
    """Return active meeting identifiers and admitted participant counts."""
    return JSONResponse({"rooms": _rooms.room_list()})


# ─────────────────────────────────────────────────────────────────
#  WebSocket Signaling
# ─────────────────────────────────────────────────────────────────


def _same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in ("http", "https")
            and parsed.netloc.lower() == websocket.headers.get("host", "").lower()
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _label(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


async def _handle_message(room: MeetingRoom, p: Participant, msg: dict, room_id: str) -> None:
    mtype = msg.get("type")
    if not room.owns(p):
        await _send(p.ws, {"type": "error", "message": "Admission required"})
        return
    if mtype in ("admit", "reject", "room-lock"):
        if room.host != p.did:
            await _send(p.ws, {"type": "error", "message": "Host only"})
            return
        if mtype == "room-lock":
            if type(msg.get("locked")) is not bool:
                return
            room.locked = msg["locked"]
            await _rooms.broadcast(room, {"type": "room-state", **room.state()})
            return
        did = msg.get("did")
        if not isinstance(did, str) or did not in room.pending:
            return
        if mtype == "admit" and len(room.participants) >= MAX_PARTICIPANTS:
            await _send(p.ws, {"type": "error", "message": "Meeting is full"})
            return
        peer = room.pending.pop(did)
        if mtype == "admit":
            room.participants[did] = peer
            await _rooms.admitted(room_id, room, peer)
        else:
            await _reject(peer, "Host declined admission")
        await _rooms.waiting_list(room)
    elif mtype in ("offer", "answer", "ice"):
        target = msg.get("to")
        if not isinstance(target, str) or target == p.did:
            return
        peer = room.participants.get(target)
        key = "candidate" if mtype == "ice" else "sdp"
        value = msg.get(key)
        if mtype == "ice":
            if value is not None and not isinstance(value, dict):
                return
        elif not isinstance(value, str) or not value:
            return
        if peer:
            await _send(peer.ws, {"type": mtype, "from": p.did, "to": target, key: value})
    elif mtype in ("chat", "caption"):
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip() or (mtype == "caption" and not p.audio):
            return
        text = text.strip()[: 1000 if mtype == "chat" else 500]
        if mtype == "chat":
            message = {
                "type": "chat",
                "from": p.did,
                "name": p.name,
                "text": text,
                "time": datetime.now().strftime("%H:%M"),
            }
            room.chat.append(message)
            del room.chat[:-200]
        else:
            message = {"type": "caption", "did": p.did, "name": p.name, "text": text}
        await _rooms.broadcast(room, message)
    elif mtype == "media-state":
        if any(key in msg and type(msg[key]) is not bool for key in ("audio", "video")):
            return
        p.audio = msg.get("audio", p.audio)
        p.video = msg.get("video", p.video)
        await _rooms.broadcast(
            room, {"type": mtype, "did": p.did, "audio": p.audio, "video": p.video}, exclude=p.did
        )
    elif mtype in ("hand-state", "recording-state"):
        key = "raised" if mtype == "hand-state" else "recording"
        if type(msg.get(key)) is not bool:
            return
        setattr(p, key, msg[key])
        message = {"type": mtype, "did": p.did, key: msg[key]}
        if mtype == "recording-state":
            message["name"] = p.name
        await _rooms.broadcast(room, message)


@app.websocket("/meet/ws/{room_id}")
async def meet_signaling(websocket: WebSocket, room_id: str):
    """Run an origin-checked, connection-authorized meeting signaling session."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", room_id) or not _same_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    participant: Participant | None = None
    room: MeetingRoom | None = None

    try:
        while True:
            event = await websocket.receive()
            if event["type"] == "websocket.disconnect":
                break
            raw = event.get("text")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await _send(
                    websocket, {"type": "error", "message": "Invalid message size or encoding"}
                )
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                continue

            if msg["type"] == "join":
                if participant is not None:
                    await _send(
                        websocket, {"type": "error", "message": "Already joined or waiting"}
                    )
                    continue
                did, name = msg.get("did"), msg.get("name", "Anonymous")
                if (
                    not _label(did, 256)
                    or not did.startswith("did:")
                    or len(did) <= 4
                    or not _label(name, 40)
                    or any(type(msg.get(key, True)) is not bool for key in ("audio", "video"))
                ):
                    await _send(websocket, {"type": "error", "message": "Invalid participant"})
                    continue
                candidate = Participant(
                    did, name, websocket, audio=msg.get("audio", True), video=msg.get("video", True)
                )
                room = _rooms.rooms.get(room_id)
                if room is None:
                    if len(_rooms.rooms) >= MAX_ROOMS:
                        await _reject(candidate, "Server is full")
                        break
                    room = MeetingRoom()
                    _rooms.rooms[room_id] = room
                async with room.lock:
                    if _rooms.rooms.get(room_id) is not room:
                        await _reject(candidate, "Meeting ended")
                        break
                    if room.locked:
                        await _reject(candidate, "Meeting is locked")
                        break
                    if did in room.participants or did in room.pending:
                        await _reject(candidate, "Participant label already in use")
                        break
                    if (
                        len(room.pending) >= MAX_PENDING
                        or len(room.participants) >= MAX_PARTICIPANTS
                    ):
                        await _reject(candidate, "Meeting is full")
                        break
                    participant = candidate
                    if not room.participants:
                        room.participants[did] = participant
                        room.host = did
                        await _rooms.admitted(room_id, room, participant)
                    else:
                        room.pending[did] = participant
                        await _send(websocket, {"type": "waiting", "did": did, "room": room_id})
                        await _rooms.waiting_list(room)
            elif participant is not None and room is not None:
                async with room.lock:
                    if _rooms.rooms.get(room_id) is not room:
                        break
                    await _handle_message(room, participant, msg, room_id)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WeAid Meet] WS error: {e}")
    finally:
        if participant is not None and room is not None:
            await _rooms.leave(room_id, room, participant)


# ─────────────────────────────────────────────────────────────────
#  /meet  — Landing page (DID 신원 + 방 입장)
# ─────────────────────────────────────────────────────────────────

_MEET_HOME_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"><title>WeAid Meet</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{min-height:100vh;background:radial-gradient(ellipse at 20% 20%,#0d1829 0%,#060c1a 70%);color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Noto Sans KR",sans-serif;display:flex;flex-direction:column}
/* Nav */
nav{display:flex;align-items:center;justify-content:space-between;padding:0 32px;height:60px;background:rgba(9,13,22,.85);backdrop-filter:blur(20px);border-bottom:1px solid rgba(56,189,248,.18)}
.logo{font-size:20px;font-weight:800;color:#38bdf8;display:flex;align-items:center;gap:10px}
.logo span{opacity:.7;font-size:13px;font-weight:500}
/* Hero */
.hero{flex:1;display:flex;align-items:center;justify-content:center;padding:40px 24px}
.card{width:100%;max-width:520px;display:flex;flex-direction:column;gap:24px}
h1{font-size:36px;font-weight:800;line-height:1.2;letter-spacing:-1px}
h1 em{color:#38bdf8;font-style:normal}
.sub{color:#94a3b8;font-size:16px;line-height:1.6}
/* DID Card */
.did-card{background:rgba(15,23,42,.75);border:1px solid rgba(56,189,248,.25);border-radius:18px;padding:20px 24px;display:flex;flex-direction:column;gap:14px}
.did-label{font-size:12px;font-weight:700;letter-spacing:1.5px;color:#64748b;text-transform:uppercase}
.did-value{font-family:"Courier New",monospace;font-size:11.5px;color:#38bdf8;word-break:break-all;background:rgba(56,189,248,.07);padding:10px 14px;border-radius:10px;line-height:1.6;cursor:pointer;transition:.15s}
.did-value:hover{background:rgba(56,189,248,.15)}
.did-actions{display:flex;gap:8px}
.btn{border:none;cursor:pointer;font-size:13px;font-weight:700;padding:8px 16px;border-radius:9px;transition:.15s;display:flex;align-items:center;gap:6px}
.btn-ghost{background:rgba(255,255,255,.08);color:#cbd5e1;border:1px solid rgba(255,255,255,.1)}
.btn-ghost:hover{background:rgba(255,255,255,.15)}
.btn-danger{background:rgba(239,68,68,.1);color:#f87171;border:1px solid rgba(239,68,68,.2)}
.btn-danger:hover{background:rgba(239,68,68,.2)}
.did-new{background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.3);border-radius:18px;padding:20px 24px;display:flex;flex-direction:column;gap:14px;align-items:center;text-align:center}
.did-new p{color:#94a3b8;font-size:14px}
.btn-primary{background:linear-gradient(135deg,#0ea5e9,#38bdf8);color:#060c1a;padding:12px 24px;border-radius:10px;font-size:14px;font-weight:800;border:none;cursor:pointer;transition:.15s;display:flex;align-items:center;gap:8px}
.btn-primary:hover{filter:brightness(1.1)}
/* Join form */
.join-form{display:flex;flex-direction:column;gap:14px}
.field{display:flex;flex-direction:column;gap:6px}
.field label{font-size:12px;font-weight:700;color:#64748b;letter-spacing:.5px}
.field input{background:rgba(15,23,42,.8);border:1px solid rgba(255,255,255,.15);color:#f8fafc;padding:12px 16px;border-radius:10px;font-size:15px;outline:none;transition:.15s}
.field input:focus{border-color:#38bdf8;box-shadow:0 0 0 3px rgba(56,189,248,.15)}
.btn-join{background:linear-gradient(135deg,#0ea5e9,#6366f1);color:#fff;padding:14px;border-radius:12px;font-size:16px;font-weight:800;border:none;cursor:pointer;width:100%;transition:.15s;display:flex;align-items:center;justify-content:center;gap:10px}
.btn-join:hover{filter:brightness(1.12);transform:translateY(-1px)}
.btn-join:disabled{opacity:.4;cursor:not-allowed;transform:none}
/* Active rooms */
.rooms-section{margin-top:4px}
.rooms-title{font-size:12px;font-weight:700;color:#64748b;letter-spacing:1px;margin-bottom:8px}
.rooms-list{display:flex;flex-direction:column;gap:6px}
.room-chip{display:flex;align-items:center;justify-content:space-between;background:rgba(15,23,42,.6);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:10px 14px;cursor:pointer;transition:.15s}
.room-chip:hover{border-color:rgba(56,189,248,.3);background:rgba(56,189,248,.06)}
.room-chip-name{font-size:14px;font-weight:600}
.room-chip-meta{font-size:12px;color:#64748b;display:flex;align-items:center;gap:6px}
.green-dot{width:7px;height:7px;background:#10b981;border-radius:50%;box-shadow:0 0 6px #10b981}
.spinner{width:18px;height:18px;border:2px solid rgba(56,189,248,.3);border-top-color:#38bdf8;border-radius:50%;animation:spin .7s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:rgba(16,185,129,.9);color:#fff;padding:10px 22px;border-radius:10px;font-weight:700;font-size:14px;display:none;z-index:999}
</style>
</head>
<body>
<nav>
  <div class="logo">🎥 WeAid Meet <span>탈중앙화 DID 화상회의</span></div>
  <div id="did-mini" style="font-size:11px;color:#64748b;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>
</nav>
<div class="hero">
  <div class="card">
    <div>
      <h1>🔐 <em>DID</em>로 로그인,<br>지금 바로 대화 시작</h1>
      <p class="sub" style="margin-top:10px">블록체인·계정 없이 브라우저가 발급하는 탈중앙화 신원으로<br>음성·영상·채팅 다자간 통화를 즐기세요.</p>
    </div>

    <!-- DID Section -->
    <div id="did-section"></div>

    <!-- Join Form -->
    <div class="join-form" id="join-form" style="display:none">
      <div class="field">
        <label>표시 이름 (Display Name)</label>
        <input id="disp-name" placeholder="나의 이름" maxlength="30">
      </div>
      <div class="field">
        <label>방 이름 (Room ID)</label>
        <input id="room-input" placeholder="weaid-room-1" maxlength="40">
      </div>
      <button class="btn-join" id="join-btn" disabled onclick="joinRoom()">
        <span>🚀 방 입장 / 생성</span>
      </button>
    </div>

    <!-- Active rooms -->
    <div class="rooms-section">
      <div class="rooms-title">🟢 활성 방</div>
      <div class="rooms-list" id="rooms-list"><div style="color:#475569;font-size:13px">불러오는 중...</div></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── Base58btc (for did:key encoding) ─────────────────────────────
const B58_ALPHA = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function base58Encode(bytes) {
  let n = BigInt('0x' + Array.from(bytes).map(b => b.toString(16).padStart(2,'0')).join(''));
  let r = '';
  while (n > 0n) { r = B58_ALPHA[Number(n % 58n)] + r; n /= 58n; }
  for (const b of bytes) { if (b !== 0) break; r = '1' + r; }
  return r;
}

// ── IndexedDB helpers ────────────────────────────────────────────
const DB_NAME = 'weaid-identity', STORE = 'keys', VERSION = 1;
function openDB() {
  return new Promise((res,rej) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = e => e.target.result.createObjectStore(STORE);
    req.onsuccess = e => res(e.target.result);
    req.onerror = rej;
  });
}
async function dbGet(key) {
  const db = await openDB();
  return new Promise((res,rej) => {
    const req = db.transaction(STORE).objectStore(STORE).get(key);
    req.onsuccess = e => res(e.target.result);
    req.onerror = rej;
  });
}
async function dbPut(key, val) {
  const db = await openDB();
  return new Promise((res,rej) => {
    const req = db.transaction(STORE,'readwrite').objectStore(STORE).put(val, key);
    req.onsuccess = e => res(e.target.result);
    req.onerror = rej;
  });
}
async function dbDel(key) {
  const db = await openDB();
  return new Promise((res,rej) => {
    const req = db.transaction(STORE,'readwrite').objectStore(STORE).delete(key);
    req.onsuccess = () => res(); req.onerror = rej;
  });
}

// ── DID:key generation (Ed25519 via SubtleCrypto) ────────────────
async function generateDID() {
  let kp;
  // Try Ed25519 first (Chrome 113+, Firefox 115+)
  try {
    kp = await crypto.subtle.generateKey({name:'Ed25519'}, true, ['sign','verify']);
  } catch (_) {
    // Fallback: ECDSA P-256 — encode as did:key with P-256 multicodec 0x1200
    kp = await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'}, true, ['sign','verify']);
    const spki = await crypto.subtle.exportKey('spki', kp.publicKey);
    const arr = new Uint8Array(spki);
    // Multicodec 0x1200 for P-256
    const mc = new Uint8Array([0x12, 0x00, ...arr]);
    const did = 'did:key:z' + base58Encode(mc);
    return {did, keyPair: kp, algo: 'ECDSA'};
  }
  const raw = await crypto.subtle.exportKey('raw', kp.publicKey);
  // Ed25519 multicodec: varint 0xed01
  const mc = new Uint8Array([0xed, 0x01, ...new Uint8Array(raw)]);
  const did = 'did:key:z' + base58Encode(mc);
  return {did, keyPair: kp, algo: 'Ed25519'};
}

// ── Sign a challenge ─────────────────────────────────────────────
async function signChallenge(privateKey, algo, challenge) {
  const enc = new TextEncoder().encode(challenge);
  const sig = await crypto.subtle.sign(
    algo === 'Ed25519' ? 'Ed25519' : {name:'ECDSA', hash:'SHA-256'},
    privateKey, enc
  );
  return btoa(String.fromCharCode(...new Uint8Array(sig)));
}

// ── Persist / load identity ───────────────────────────────────────
async function saveIdentity(did, keyPair, algo) {
  const privJwk = await crypto.subtle.exportKey('jwk', keyPair.privateKey);
  const pubJwk  = await crypto.subtle.exportKey('jwk', keyPair.publicKey);
  await dbPut('identity', {did, privJwk, pubJwk, algo, created: new Date().toISOString()});
}
async function loadIdentity() {
  return await dbGet('identity');
}
async function deleteIdentity() {
  await dbDel('identity');
}

// ── Import stored key pair ────────────────────────────────────────
async function importKeyPair(stored) {
  let params;
  if (stored.algo === 'Ed25519') {
    params = {name:'Ed25519'};
  } else {
    params = {name:'ECDSA', namedCurve:'P-256'};
  }
  const privateKey = await crypto.subtle.importKey('jwk', stored.privJwk, params, true, ['sign']);
  const publicKey  = await crypto.subtle.importKey('jwk', stored.pubJwk,  params, true, ['verify']);
  return {privateKey, publicKey};
}

// ── UI helpers ───────────────────────────────────────────────────
function showToast(msg, ms=2200) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  setTimeout(()=>t.style.display='none',ms);
}
function copyDid(did) {
  navigator.clipboard.writeText(did).then(()=>showToast('✅ DID 복사됨'));
}

// ── Render DID state ─────────────────────────────────────────────
function renderDID(identity) {
  const sec = document.getElementById('did-section');
  const miniDid = document.getElementById('did-mini');
  const form = document.getElementById('join-form');
  const joinBtn = document.getElementById('join-btn');

  if (identity) {
    miniDid.textContent = identity.did.slice(0,36)+'…';
    sec.innerHTML = `
      <div class="did-card">
        <div class="did-label">🔑 나의 DID 신원 (${identity.algo})</div>
        <div class="did-value" title="클릭해서 복사" onclick="copyDid('${identity.did}')">${identity.did}</div>
        <div style="font-size:11px;color:#475569">생성: ${identity.created ? new Date(identity.created).toLocaleString('ko-KR') : '알 수 없음'} · 클릭하여 복사</div>
        <div class="did-actions">
          <button class="btn btn-ghost" onclick="regenDID()">🔄 새 신원 생성</button>
          <button class="btn btn-danger" onclick="deleteDID()">🗑 삭제</button>
        </div>
      </div>`;
    form.style.display = 'flex';
    joinBtn.disabled = false;
  } else {
    miniDid.textContent = '';
    sec.innerHTML = `
      <div class="did-new">
        <div style="font-size:36px">🆔</div>
        <p>DID(탈중앙화 신원)가 없습니다.<br>브라우저가 직접 암호키를 생성합니다. 계정·블록체인 불필요!</p>
        <button class="btn-primary" onclick="createDID()">⚡ DID 신원 생성하기</button>
      </div>`;
    form.style.display = 'none';
  }
}

// ── DID actions ───────────────────────────────────────────────────
let _identity = null;

async function createDID() {
  const sec = document.getElementById('did-section');
  sec.innerHTML = '<div style="text-align:center;padding:24px"><div class="spinner"></div><p style="margin-top:12px;color:#64748b">Ed25519 키 생성 중...</p></div>';
  try {
    const {did, keyPair, algo} = await generateDID();
    await saveIdentity(did, keyPair, algo);
    _identity = await loadIdentity();
    renderDID(_identity);
    showToast('✅ DID 생성 완료! 이 신원은 브라우저에만 저장됩니다.');
  } catch(e) {
    sec.innerHTML = `<div style="color:#f87171;padding:16px">오류: ${e.message}</div>`;
  }
}

async function regenDID() {
  if (!confirm('현재 DID를 삭제하고 새로 생성합니까?\n기존 방 초대 링크는 무효화됩니다.')) return;
  await createDID();
}

async function deleteDID() {
  if (!confirm('DID를 삭제합니까? 이 작업은 되돌릴 수 없습니다.')) return;
  await deleteIdentity();
  _identity = null;
  renderDID(null);
}

// ── Join room ────────────────────────────────────────────────────
async function joinRoom() {
  if (!_identity) return;
  const name = document.getElementById('disp-name').value.trim() || 'Anonymous';
  let room = document.getElementById('room-input').value.trim();
  if (!room) { showToast('⚠ 방 이름을 입력하세요'); return; }
  room = room.replace(/[^a-zA-Z0-9가-힣\-_]/g,'').slice(0,40);
  if (!room) { showToast('⚠ 유효한 방 이름을 입력하세요'); return; }
  // store name preference
  localStorage.setItem('weaid-meet-name', name);
  window.location.href = `/meet/room?room=${encodeURIComponent(room)}&name=${encodeURIComponent(name)}`;
}

// ── Load active rooms ────────────────────────────────────────────
async function loadRooms() {
  try {
    const r = await fetch('/api/meet/rooms');
    const d = await r.json();
    const el = document.getElementById('rooms-list');
    if (!d.rooms.length) {
      el.innerHTML = '<div style="color:#475569;font-size:13px">활성 방이 없습니다. 새 방을 만들어보세요!</div>';
      return;
    }
    el.replaceChildren();
    for (const rm of d.rooms) {
      const chip = document.createElement('button');
      chip.className = 'room-chip';
      chip.textContent = `🏠 ${rm.id} · ${rm.count}명 참가 중`;
      chip.onclick = () => quickJoin(rm.id);
      el.appendChild(chip);
    }
  } catch(_) {}
}

function quickJoin(roomId) {
  document.getElementById('room-input').value = roomId;
  document.getElementById('join-btn').focus();
}

// ── Init ─────────────────────────────────────────────────────────
(async () => {
  _identity = await loadIdentity();
  renderDID(_identity);
  const savedName = localStorage.getItem('weaid-meet-name');
  if (savedName) document.getElementById('disp-name').value = savedName;
  const invitedRoom = new URLSearchParams(location.search).get('room');
  if (invitedRoom) document.getElementById('room-input').value = invitedRoom;
  loadRooms();
  setInterval(loadRooms, 5000);

  // Enter key to join
  document.getElementById('room-input').addEventListener('keydown', e => { if(e.key==='Enter') joinRoom(); });
})();
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────
#  /meet/room  — 통화 방 (Google Meet 스타일 UI)
# ─────────────────────────────────────────────────────────────────

_MEET_ROOM_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"><title>WeAid Meet — 통화 중</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;background:#111827;color:#f8fafc;
  font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Noto Sans KR",sans-serif}

/* Layout */
#app{display:flex;height:100vh;flex-direction:column}
#main{flex:1;display:flex;overflow:hidden;position:relative}
#video-area{flex:1;display:flex;flex-direction:column;padding:8px;gap:8px;overflow:hidden;position:relative}

/* Top bar */
#topbar{height:56px;background:rgba(17,24,39,.9);backdrop-filter:blur(10px);border-bottom:1px solid rgba(255,255,255,.08);display:flex;align-items:center;justify-content:space-between;padding:0 20px;flex-shrink:0}
.tb-room{font-size:15px;font-weight:700;color:#38bdf8;display:flex;align-items:center;gap:10px}
.tb-did{font-size:11px;color:#475569;font-family:monospace;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tb-count{background:rgba(56,189,248,.12);border:1px solid rgba(56,189,248,.25);padding:3px 10px;border-radius:6px;font-size:12px;font-weight:700;color:#38bdf8}

/* Video grid */
#video-grid{flex:1;display:grid;gap:8px;overflow:hidden;align-content:center;justify-content:center}
.video-tile{position:relative;background:#1e293b;border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center;aspect-ratio:16/9}
.video-tile video{width:100%;height:100%;object-fit:cover}
.tile-name{position:absolute;bottom:10px;left:12px;background:rgba(0,0,0,.6);backdrop-filter:blur(6px);padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px}
.tile-did{position:absolute;top:10px;left:12px;background:rgba(56,189,248,.15);border:1px solid rgba(56,189,248,.3);padding:3px 8px;border-radius:5px;font-size:10px;font-family:monospace;color:#38bdf8;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile-muted{position:absolute;top:10px;right:10px;background:rgba(239,68,68,.8);border-radius:6px;padding:3px 7px;font-size:11px}
.tile-avatar{width:80px;height:80px;border-radius:50%;background:linear-gradient(135deg,#0ea5e9,#6366f1);display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:700}
.tile-local-badge{position:absolute;bottom:10px;right:12px;background:rgba(16,185,129,.7);padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700}

/* Local video (pip style when others are present) */
#local-pip{position:absolute;bottom:80px;right:16px;width:200px;border-radius:14px;overflow:hidden;box-shadow:0 8px 28px rgba(0,0,0,.6);border:2px solid rgba(56,189,248,.3);background:#1e293b;aspect-ratio:16/9;z-index:50;cursor:grab;display:none}
#local-pip video{width:100%;height:100%;object-fit:cover}
#local-pip .tile-name{font-size:10px;padding:3px 8px}

/* Bottom toolbar */
#toolbar{height:80px;background:rgba(17,24,39,.95);backdrop-filter:blur(10px);border-top:1px solid rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;gap:12px;flex-shrink:0}
.tool-btn{width:52px;height:52px;border-radius:50%;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;transition:.15s;flex-shrink:0}
.tool-btn:hover{transform:scale(1.08)}
.tool-btn.on{background:rgba(255,255,255,.12);color:#f8fafc}
.tool-btn.off{background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.25)}
.tool-btn.danger{background:#ef4444;color:#fff}
.tool-btn.danger:hover{background:#dc2626}
.tool-btn.accent{background:rgba(56,189,248,.12);color:#38bdf8;border:1px solid rgba(56,189,248,.25)}
.tool-btn.active-share{background:rgba(16,185,129,.2);color:#10b981;border:1px solid #10b981}
.tool-sep{width:1px;height:40px;background:rgba(255,255,255,.1);margin:0 4px}

/* Right panel */
#right-panel{width:0;overflow:hidden;background:#0f172a;border-left:1px solid rgba(255,255,255,.08);display:flex;flex-direction:column;transition:width .25s ease}
#right-panel.open{width:320px}
.panel-tabs{display:flex;border-bottom:1px solid rgba(255,255,255,.08)}
.panel-tab{flex:1;padding:12px;text-align:center;font-size:13px;font-weight:700;color:#64748b;cursor:pointer;transition:.15s}
.panel-tab.active{color:#38bdf8;border-bottom:2px solid #38bdf8}
.panel-body{flex:1;overflow-y:auto;padding:12px}
/* Chat */
.chat-list{display:flex;flex-direction:column;gap:10px}
.chat-msg{padding:8px 12px;border-radius:10px;background:rgba(255,255,255,.05)}
.chat-msg.mine{background:rgba(56,189,248,.1);border-left:3px solid #38bdf8}
.chat-meta{font-size:11px;color:#64748b;margin-bottom:3px;display:flex;justify-content:space-between}
.chat-text{font-size:13px;line-height:1.45;word-break:break-word}
.chat-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid rgba(255,255,255,.08)}
.chat-input{flex:1;background:rgba(15,23,42,.8);border:1px solid rgba(255,255,255,.12);color:#f8fafc;padding:9px 12px;border-radius:9px;font-size:13px;outline:none}
.chat-input:focus{border-color:#38bdf8}
.send-btn{background:#0ea5e9;border:none;color:#fff;padding:9px 14px;border-radius:9px;cursor:pointer;font-weight:700;font-size:13px}
/* Participants */
.peer-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;background:rgba(255,255,255,.04);margin-bottom:6px}
.peer-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#0ea5e9,#6366f1);display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0}
.peer-info{flex:1;min-width:0}
.peer-name{font-size:13px;font-weight:600}
.peer-did-mini{font-size:10px;color:#475569;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.peer-status{display:flex;gap:4px;font-size:14px}

/* Toast */
#toast{position:fixed;bottom:96px;left:50%;transform:translateX(-50%);background:rgba(30,41,59,.95);border:1px solid rgba(255,255,255,.15);color:#f8fafc;padding:9px 20px;border-radius:10px;font-size:13px;font-weight:600;display:none;z-index:9999;backdrop-filter:blur(10px)}

/* Connection state banner */
#conn-banner{display:none;position:absolute;top:8px;left:50%;transform:translateX(-50%);background:rgba(251,191,36,.15);border:1px solid #fbbf24;border-radius:8px;padding:6px 18px;font-size:12px;color:#fbbf24;font-weight:700;z-index:100}
#video-grid{overflow-y:auto;align-content:start}
#toolbar{height:auto;min-height:88px;flex-wrap:wrap;padding:12px;gap:8px}
.tool-btn{border-radius:16px;width:auto;min-width:64px;height:58px;padding:8px;flex-direction:column;gap:4px;font-size:17px}
.tool-btn small{font-size:11px}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #38bdf8;outline-offset:3px}
button:disabled{opacity:.4;cursor:not-allowed;transform:none}
.meeting-action{padding:9px 14px;background:#334155;color:#f8fafc;border:0;border-radius:8px;cursor:pointer}
.meeting-action.primary{background:#0284c7}
#prejoin{position:fixed;inset:0;z-index:200;background:#111827;overflow:auto;padding:40px 24px;display:flex;align-items:center;justify-content:center}
.prejoin-card{width:min(100%,1000px);display:grid;grid-template-columns:3fr 2fr;gap:32px;align-items:center}
.preview-wrap{aspect-ratio:16/9;background:#1e293b;border-radius:20px;overflow:hidden;position:relative}
#preview-video{height:100%;width:100%;object-fit:cover;transform:scaleX(-1)}
.preview-controls{position:absolute;bottom:16px;width:100%;display:flex;justify-content:center;gap:12px}
.join-details{display:flex;flex-direction:column;gap:16px}
.join-details input,.join-details select{width:100%;padding:10px;background:#1e293b;color:white;border:1px solid #64748b;border-radius:8px}
.muted-copy{color:#94a3b8;font-size:13px;line-height:1.6}
#captions{position:absolute;bottom:12px;left:10%;width:80%;background:#000c;border-radius:10px;padding:14px;z-index:60;pointer-events:none}
#captions:empty{display:none}
#recording-notice{color:#fca5a5;font-size:12px}
#waiting-list{padding:12px;border-bottom:1px solid #334155}
.waiting-item{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
#host-controls{display:flex;gap:8px;padding:8px 12px}
[hidden]{display:none!important}
.tile-hand{position:absolute;right:10px;bottom:10px;background:#0c4a6e;padding:5px;border-radius:6px}
@media(max-width:700px){
  .prejoin-card{grid-template-columns:1fr;gap:20px}
  #prejoin{align-items:flex-start;padding:20px}
  #topbar{padding:0 10px}.tb-did{display:none}
  #right-panel.open{position:absolute;inset:0 0 0 auto;width:min(320px,90vw);z-index:80}
  #local-pip{width:120px;bottom:12px}
  .tool-btn{min-width:54px;height:52px}
}
</style>
</head>
<body>
<section id="prejoin" aria-label="회의 입장 준비">
  <div class="prejoin-card">
    <div class="preview-wrap">
      <video id="preview-video" autoplay muted playsinline></video>
      <div class="preview-controls">
        <button class="meeting-action" id="preview-mic" onclick="toggleMic()">마이크 켜짐</button>
        <button class="meeting-action" id="preview-cam" onclick="toggleCam()">카메라 켜짐</button>
      </div>
    </div>
    <div class="join-details">
      <h1>회의에 참여할 준비가 되셨나요?</h1>
      <p id="preview-room" class="muted-copy"></p>
      <label for="join-name">참가자 이름</label>
      <input id="join-name" maxlength="40" autocomplete="nickname">
      <p id="preview-status" role="status">카메라와 마이크를 확인하고 있습니다…</p>
      <button class="meeting-action" onclick="prepareMedia()">장치 권한 다시 확인</button>
      <button class="meeting-action primary" id="join-meeting" onclick="joinMeeting()" disabled>참가 요청</button>
      <a href="/meet" onclick="cleanup()" style="color:#38bdf8">홈으로 돌아가기</a>
      <p class="muted-copy">첫 참가자가 호스트가 됩니다. 이후 참가자는 호스트 승인 후 연결됩니다. 미리보기 영상은 입장 전 다른 사람에게 전송되지 않습니다.</p>
    </div>
  </div>
</section>
<div id="app">
  <!-- Top bar -->
  <div id="topbar">
    <div class="tb-room">
      🎥 <span id="room-name-display">로딩 중...</span>
    </div>
    <div class="tb-did" id="my-did-display">DID 로딩 중...</div>
    <button class="meeting-action" onclick="copyInvite()">초대 링크</button>
    <div class="tb-count">👥 <span id="peer-count">1</span>명</div>
  </div>

  <!-- Main -->
  <div id="main">
    <div id="video-area">
      <div id="conn-banner">🔄 피어 연결 중...</div>
      <!-- Video grid -->
      <div id="video-grid"></div>
      <div id="captions" aria-live="polite"></div>
      <!-- Local PiP (shown when others are present) -->
      <div id="local-pip">
        <video id="local-pip-video" autoplay muted playsinline></video>
        <div class="tile-name">나 (PiP)</div>
      </div>
    </div>

    <!-- Right panel -->
    <div id="right-panel">
      <div class="panel-tabs">
        <button class="meeting-action panel-tab active" id="tab-chat" onclick="switchTab('chat')">💬 채팅</button>
        <button class="meeting-action panel-tab" id="tab-peers" onclick="switchTab('peers')">👥 참가자</button>
        <button class="meeting-action" onclick="togglePanel()" aria-label="패널 닫기">×</button>
      </div>
      <div id="host-controls" hidden>
        <button class="meeting-action" id="btn-lock" onclick="wsSend({type:'room-lock',locked:!roomLocked})">회의 잠그기</button>
      </div>
      <div id="waiting-list" hidden></div>
      <div class="panel-body" id="panel-body-chat">
        <div class="chat-list" id="chat-list"></div>
      </div>
      <div class="panel-body" id="panel-body-peers" style="display:none">
        <div id="peers-list"></div>
      </div>
      <div class="chat-input-row">
        <input class="chat-input" id="chat-input" placeholder="메시지 입력..." maxlength="500"
               onkeydown="if(event.key==='Enter')sendChat()">
        <button class="send-btn" onclick="sendChat()">전송</button>
      </div>
    </div>
  </div>

  <!-- Toolbar -->
  <div id="toolbar">
    <button class="tool-btn on" id="btn-mic" onclick="toggleMic()" title="마이크">🎤<small>마이크</small></button>
    <button class="tool-btn on" id="btn-cam" onclick="toggleCam()" title="카메라">📹<small>카메라</small></button>
    <button class="tool-btn accent" id="btn-screen" onclick="toggleScreen()" title="화면 공유">🖥️<small>화면 공유</small></button>
    <button class="tool-btn accent" id="btn-hand" onclick="toggleHand()" aria-pressed="false">✋<small>손들기</small></button>
    <button class="tool-btn accent" id="btn-record" onclick="toggleRecording()" aria-pressed="false">⏺<small>녹화</small></button>
    <button class="tool-btn accent" id="btn-captions" onclick="toggleCaptions()" aria-pressed="false">CC<small>자막</small></button>
    <button class="tool-btn accent" id="btn-chat" onclick="togglePanel()" title="채팅">💬<small>채팅·참가자</small></button>
    <button class="tool-btn danger" onclick="leaveRoom()" title="나가기">📴<small>나가기</small></button>
  </div>
  <p id="recording-notice" role="status"></p>
</div>

<div id="toast"></div>

<script>
// ─── URL params ─────────────────────────────────────────────────
const params = new URLSearchParams(location.search);
const ROOM_ID = params.get('room') || 'weaid-room';
const DISPLAY_NAME = params.get('name') || localStorage.getItem('weaid-meet-name') || 'Anonymous';
document.getElementById('room-name-display').textContent = '🏠 ' + ROOM_ID;

// ─── State ──────────────────────────────────────────────────────
let myDID = null, myName = DISPLAY_NAME;
let localStream = null, screenStream = null;
let ws = null;
const peers = Object.create(null);      // did → RTCPeerConnection
const peerInfo = Object.create(null);   // did → participant state
const peerStreams = Object.create(null); // did → MediaStream
const pendingIce = Object.create(null);
let micOn = true, camOn = true, screenOn = false, panelOpen = false;
let joined = false, leaving = false, joining = false, hostDid = null, roomLocked = false;
let raised = false, preparing = false, sharing = false, shareVersion = 0;
let recorder = null, recordingStream = null, recordingPending = false;
let recognition = null, captionsOn = false, captionTimer = null;
let audioContext = null, audioDestination = null, recordingTimer = null;
const audioSources = new Map();

let ICE_SERVERS = [
  {urls:'stun:stun.l.google.com:19302'},
  {urls:'stun:stun1.l.google.com:19302'},
  {urls:'stun:stun2.l.google.com:19302'},
];

// ─── Base58 + DID helpers (same as landing page) ────────────────
const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58enc(bytes) {
  let n = BigInt('0x'+Array.from(bytes).map(b=>b.toString(16).padStart(2,'0')).join(''));
  let r=''; while(n>0n){r=B58[Number(n%58n)]+r;n/=58n;}
  for(const b of bytes){if(b!==0)break;r='1'+r;} return r;
}

// IndexedDB helpers
const DB='weaid-identity';
function openDB(){return new Promise((res,rej)=>{const r=indexedDB.open(DB,1);r.onupgradeneeded=e=>e.target.result.createObjectStore('keys');r.onsuccess=e=>res(e.target.result);r.onerror=rej;});}
async function dbGet(k){const db=await openDB();return new Promise((res,rej)=>{const r=db.transaction('keys').objectStore('keys').get(k);r.onsuccess=e=>res(e.target.result);r.onerror=rej;});}

async function loadIdentity() {
  const stored = await dbGet('identity');
  if (!stored) return null;
  let priv;
  try {
    const algo = stored.algo === 'Ed25519' ? {name:'Ed25519'} : {name:'ECDSA',namedCurve:'P-256'};
    priv = await crypto.subtle.importKey('jwk', stored.privJwk, algo, true, ['sign']);
  } catch(e) { console.warn('Key import failed', e); priv = null; }
  return {...stored, privateKey: priv};
}

// ─── Toast ─────────────────────────────────────────────────────
function toast(msg, ms=2500) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  setTimeout(()=>t.style.display='none',ms);
}

// ─── Video grid management ───────────────────────────────────────
function updateGrid() {
  const count = Object.keys(peerInfo).length + (joined ? 1 : 0);
  document.getElementById('peer-count').textContent = count;
  const grid = document.getElementById('video-grid');
  const pip  = document.getElementById('local-pip');

  if (Object.keys(peerInfo).length === 0) {
    // Only me: full screen local video
    pip.style.display = 'none';
    grid.innerHTML = '';
    const tile = makeTile('local', screenStream || localStream, myName + ' (나)', myDID, true,
      {audio:micOn,video:screenOn || camOn,raised});
    grid.appendChild(tile);
    setGridCols(grid, 1);
  } else {
    // Others: show local as PiP
    pip.style.display = 'block';
    const pipVid = document.getElementById('local-pip-video');
    pipVid.srcObject = screenStream || localStream;
    pipVid.style.visibility = screenOn || camOn ? 'visible' : 'hidden';

    // Build grid with remote peers
    grid.innerHTML = '';
    for (const [did, info] of Object.entries(peerInfo)) {
      const tile = makeTile(did, peerStreams[did], info.name || did.slice(0,16)+'…', did, false, info);
      grid.appendChild(tile);
    }
    const n = Object.keys(peerInfo).length;
    setGridCols(grid, n);
  }
  updatePeersList();
  updateRecordingNotice();
}

function setGridCols(grid, n) {
  const cols = n === 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4;
  grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
}

function makeTile(id, stream, name, did, isLocal, info={}) {
  const div = document.createElement('div');
  div.className = 'video-tile'; div.id = 'tile-'+id;
  const vid = document.createElement('video');
  vid.autoplay = true; vid.playsInline = true;
  if (isLocal) vid.muted = true;
  vid.srcObject = stream || null;
  div.appendChild(vid);

  // Avatar when no video
  const av = document.createElement('div');
  av.className = 'tile-avatar'; av.id = 'av-'+id;
  av.textContent = (name||'?')[0].toUpperCase();
  const hasVideo = info.video !== false && stream && stream.getVideoTracks().some(t => t.readyState === 'live');
  av.style.display = hasVideo ? 'none' : 'flex';
  vid.style.display = hasVideo ? 'block' : 'none';
  div.appendChild(av);

  // DID badge
  const didBadge = document.createElement('div');
  didBadge.className = 'tile-did';
  didBadge.textContent = (did||'').slice(0,30)+'…';
  div.appendChild(didBadge);

  // Name badge
  const nb = document.createElement('div');
  nb.className = 'tile-name';
  nb.textContent = name + (did === hostDid ? ' · 호스트' : '');
  div.appendChild(nb);

  // Muted indicator
  if (info.audio === false) {
    const m = document.createElement('div');
    m.className = 'tile-muted'; m.textContent = '🔇';
    div.appendChild(m);
  }

  if (isLocal) {
    const lb = document.createElement('div');
    lb.className = 'tile-local-badge'; lb.textContent = '나';
    div.appendChild(lb);
  }
  if (info.raised) {
    const hand = document.createElement('span');
    hand.className = 'tile-hand'; hand.textContent = '✋ 손들기';
    div.appendChild(hand);
  }
  return div;
}

// ─── WebRTC peer management ──────────────────────────────────────
function createPC(remoteDid) {
  if (peers[remoteDid]) return peers[remoteDid];
  const pc = new RTCPeerConnection({iceServers: ICE_SERVERS});

  // Fixed transceivers support screen sharing even without a camera track.
  for (const kind of ['audio', 'video']) {
    const stream = kind === 'video' && screenStream ? screenStream : localStream;
    const track = stream && stream.getTracks().find(t => t.kind === kind);
    pc.addTransceiver(track || kind, {direction:'sendrecv', streams:track ? [stream] : []});
  }

  pc.onicecandidate = e => {
    if (e.candidate) wsSend({type:'ice', to:remoteDid, candidate:e.candidate.toJSON()});
  };
  pc.ontrack = e => {
    const stream = peerStreams[remoteDid] || new MediaStream();
    if (!stream.getTracks().includes(e.track)) stream.addTrack(e.track);
    peerStreams[remoteDid] = stream;
    e.track.onunmute = () => updateGrid();
    addRecordingAudio(stream);
    updateGrid();
  };
  pc.onconnectionstatechange = () => {
    const s = pc.connectionState;
    const banner = document.getElementById('conn-banner');
    if (s === 'connected') { banner.style.display='none'; }
    else if (s === 'connecting' || s === 'new') { banner.style.display='block'; }
    else if (s === 'failed') {
      toast('⚠ 연결 실패. TURN 설정을 확인하거나 나간 뒤 다시 참가하세요.', 6000);
    }
  };

  peers[remoteDid] = pc;
  return pc;
}

async function makeOffer(remoteDid) {
  const pc = createPC(remoteDid);
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  wsSend({type:'offer', to:remoteDid, sdp:offer.sdp});
}

async function handleOffer(remoteDid, sdp) {
  const pc = createPC(remoteDid);
  await pc.setRemoteDescription({type:'offer', sdp});
  await flushIce(remoteDid);
  const answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);
  wsSend({type:'answer', to:remoteDid, sdp:answer.sdp});
}

async function handleAnswer(remoteDid, sdp) {
  const pc = peers[remoteDid];
  if (pc && pc.signalingState !== 'stable') {
    await pc.setRemoteDescription({type:'answer', sdp});
    await flushIce(remoteDid);
  }
}

async function handleIce(remoteDid, candidate) {
  const pc = peers[remoteDid];
  if (!pc || !pc.remoteDescription) {
    const queue = pendingIce[remoteDid] ||= [];
    if (queue.length < 100) queue.push(candidate);
    return;
  }
  await pc.addIceCandidate(candidate);
}

async function flushIce(did) {
  for (const candidate of pendingIce[did] || []) await peers[did].addIceCandidate(candidate);
  delete pendingIce[did];
}

function removePeer(did) {
  if (peers[did]) { peers[did].close(); delete peers[did]; }
  delete peerStreams[did];
  delete peerInfo[did];
  delete pendingIce[did];
  for (const [track, source] of audioSources) {
    if (track.readyState === 'ended' || !Object.values(peerStreams).some(s => s.getTracks().includes(track)) &&
        !localStream?.getTracks().includes(track)) {
      source.disconnect(); audioSources.delete(track);
    }
  }
  updateGrid();
}

// ─── WebSocket ───────────────────────────────────────────────────
function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/meet/ws/${encodeURIComponent(ROOM_ID)}`);
  const socket = ws;

  ws.onopen = () => {
    wsSend({type:'join', did:myDID, name:myName, audio:micOn, video:camOn});
  };

  let messages = Promise.resolve();
  ws.onmessage = (e) => {
    messages = messages.then(async () => {
    if (ws !== socket || leaving) return;
    let msg;
    try { msg = JSON.parse(e.data); } catch(_){ return; }
    const t = msg.type;

    if (t === 'joined') {
      joined = true; joining = false;
      hostDid = msg.host; roomLocked = !!msg.locked;
      document.getElementById('prejoin').hidden = true;
      document.getElementById('chat-list').replaceChildren();
      updateHostControls();
      // Create offers to all existing peers
      for (const peer of msg.peers) {
        peerInfo[peer.did] = peer;
        try { await makeOffer(peer.did); }
        catch (error) { console.warn(error); toast('피어 연결을 시작하지 못했습니다. 다시 참가하세요.'); }
      }
      // Load chat history
      for (const cm of (msg.chat_history||[])) appendChat(cm, false);
      syncMediaControls();
      toast('✅ 회의에 참가했습니다.');
    }
    else if (t === 'waiting') {
      document.getElementById('preview-status').textContent = '호스트 승인을 기다리고 있습니다. 취소하려면 홈으로 돌아가세요.';
    }
    else if (t === 'waiting-list') {
      renderWaiting(msg.participants || []);
    }
    else if (t === 'rejected') {
      joining = false;
      document.getElementById('preview-status').textContent = msg.message || '참가 요청이 거절되었거나 회의가 종료되었습니다.';
      document.getElementById('join-meeting').disabled = false;
      ws = null; socket.close();
    }
    else if (t === 'error') {
      toast(msg.message || '요청을 처리하지 못했습니다.', 5000);
      if (!joined) {
        document.getElementById('preview-status').textContent = msg.message || '참가 요청 실패';
        joining = false; ws = null; socket.close();
        document.getElementById('join-meeting').disabled = false;
      }
    }
    else if (t === 'room-state') {
      hostDid = msg.host; roomLocked = !!msg.locked;
      updateHostControls(); updateGrid();
    }
    else if (t === 'hand-state') {
      if (msg.did === myDID) raised = msg.raised;
      else if (peerInfo[msg.did]) peerInfo[msg.did].raised = msg.raised;
      updateGrid();
    }
    else if (t === 'recording-state') {
      if (peerInfo[msg.did]) peerInfo[msg.did].recording = msg.recording;
      updateRecordingNotice();
    }
    else if (t === 'caption') {
      if (captionsOn) {
        const box = document.getElementById('captions');
        box.textContent = `${msg.name}: ${msg.text}`;
        clearTimeout(captionTimer);
        captionTimer = setTimeout(() => box.textContent = '', 8000);
      }
    }
    else if (t === 'peer-joined') {
      peerInfo[msg.did] = msg;
      toast(`👋 ${msg.name} 입장`);
      updateGrid();
    }
    else if (t === 'peer-left') {
      removePeer(msg.did);
      toast(`🚪 ${msg.name||msg.did.slice(0,12)} 퇴장`);
    }
    else if (t === 'offer')  { await handleOffer(msg.from, msg.sdp); }
    else if (t === 'answer') { await handleAnswer(msg.from, msg.sdp); }
    else if (t === 'ice')    { await handleIce(msg.from, msg.candidate); }
    else if (t === 'chat')   { appendChat(msg, true); }
    else if (t === 'media-state') {
      if (peerInfo[msg.did]) {
        peerInfo[msg.did].audio = msg.audio;
        peerInfo[msg.did].video = msg.video;
      }
      updateGrid();
    }
    }).catch(error => { console.warn(error); toast('연결 메시지 처리 실패. 다시 참가해 주세요.'); });
  };

  ws.onclose = () => {
    if (ws !== socket || leaving) return;
    ws = null; joined = false; joining = false;
    stopRecording();
    stopCaptions();
    if (screenOn) stopScreen();
    for (const did of Object.keys(peers)) removePeer(did);
    hostDid = null; raised = false;
    document.getElementById('btn-hand').setAttribute('aria-pressed', 'false');
    document.getElementById('btn-hand').className = 'tool-btn accent';
    document.getElementById('prejoin').hidden = false;
    document.getElementById('preview-status').textContent = '서버 연결이 끊겼습니다. 다시 참가를 요청하세요.';
    document.getElementById('join-meeting').disabled = false;
    updateGrid();
  };
  ws.onerror = () => toast('시그널링 서버에 연결할 수 없습니다.');
}

// ─── Chat ────────────────────────────────────────────────────────
function appendChat(msg, scroll=true) {
  const isMine = msg.from === myDID;
  const el = document.createElement('div');
  el.className = 'chat-msg' + (isMine ? ' mine' : '');
  el.innerHTML = `<div class="chat-meta"><span>${isMine?'나':escHtml(msg.name||'?')}</span><span>${escHtml(msg.time||'')}</span></div><div class="chat-text">${escHtml(msg.text)}</div>`;
  const list = document.getElementById('chat-list');
  list.appendChild(el);
  while (list.children.length > 200) list.firstChild.remove();
  if (scroll) { const b=document.getElementById('panel-body-chat'); b.scrollTop=b.scrollHeight; }
}

function sendChat() {
  const inp = document.getElementById('chat-input');
  const text = inp.value.trim();
  if (!text || !joined) return;
  if (!wsSend({type:'chat', text})) { toast('연결을 확인하세요.'); return; }
  inp.value = '';
}

function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── Participants panel ───────────────────────────────────────────
function updatePeersList() {
  const el = document.getElementById('peers-list');
  const allPeers = [{did:myDID, name:myName+'(나)', audio:micOn, video:camOn, raised},
    ...Object.values(peerInfo)];
  el.innerHTML = allPeers.map(p => `
    <div class="peer-item">
      <div class="peer-avatar">${escHtml((p.name||'?')[0].toUpperCase())}</div>
      <div class="peer-info">
        <div class="peer-name">${escHtml(p.name||'')}${p.did === hostDid ? ' · 호스트' : ''}</div>
        <div class="peer-did-mini">${escHtml((p.did||'').slice(0,30))}…</div>
      </div>
      <div class="peer-status">
        ${p.audio===false?'🔇':'🎤'} ${p.video===false?'📷':'📹'} ${p.raised?'✋':''}
      </div>
    </div>`).join('');
}

// ─── Panel / tab ─────────────────────────────────────────────────
function switchTab(tab) {
  document.getElementById('panel-body-chat').style.display = tab==='chat'?'block':'none';
  document.getElementById('panel-body-peers').style.display = tab==='peers'?'block':'none';
  document.getElementById('tab-chat').classList.toggle('active', tab==='chat');
  document.getElementById('tab-peers').classList.toggle('active', tab==='peers');
  if (tab==='peers') updatePeersList();
}

function togglePanel() {
  panelOpen = !panelOpen;
  document.getElementById('right-panel').classList.toggle('open', panelOpen);
  document.getElementById('btn-chat').classList.toggle('on', panelOpen);
}

function updateHostControls() {
  document.getElementById('host-controls').hidden = myDID !== hostDid;
  document.getElementById('btn-lock').textContent = roomLocked ? '잠금 해제' : '회의 잠그기';
  document.getElementById('btn-lock').setAttribute('aria-pressed', String(roomLocked));
  if (myDID !== hostDid) renderWaiting([]);
}

function renderWaiting(participants) {
  const list = document.getElementById('waiting-list');
  list.replaceChildren();
  list.hidden = myDID !== hostDid || !participants.length;
  if (list.hidden) return;
  const title = document.createElement('h3');
  title.textContent = `입장 대기 (${participants.length})`;
  list.appendChild(title);
  for (const p of participants) {
    const item = document.createElement('div');
    item.className = 'waiting-item';
    const label = document.createElement('span');
    label.textContent = p.name;
    item.appendChild(label);
    for (const [type, text] of [['admit','승인'], ['reject','거절']]) {
      const button = document.createElement('button');
      button.className = 'meeting-action'; button.textContent = text;
      button.onclick = () => wsSend({type, did:p.did});
      item.appendChild(button);
    }
    list.appendChild(item);
  }
  if (!panelOpen) togglePanel();
}

async function copyInvite() {
  const url = new URL('/meet/room', location.origin);
  url.searchParams.set('room', ROOM_ID);
  try { await navigator.clipboard.writeText(url.href); toast('초대 링크가 복사되었습니다.'); }
  catch (_) { window.prompt('초대 링크를 복사하세요.', url.href); }
}

async function prepareMedia() {
  if (preparing || joining || joined || !myDID || leaving) return;
  preparing = true;
  document.getElementById('join-meeting').disabled = true;
  const status = document.getElementById('preview-status');
  status.textContent = '카메라와 마이크 확인 중…';
  if (localStream) localStream.getTracks().forEach(t => t.stop());
  localStream = new MediaStream();
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error('카메라와 마이크는 HTTPS 또는 localhost에서 사용할 수 있습니다.');
    }
    // Separate requests allow camera-only and microphone-only participation.
    for (const constraints of [{video:true}, {audio:{echoCancellation:true,noiseSuppression:true}}]) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (leaving) { stream.getTracks().forEach(t => t.stop()); return; }
        stream.getTracks().forEach(t => localStream.addTrack(t));
      } catch (error) { console.info('Media unavailable:', error.name); }
    }
    status.textContent = localStream.getTracks().length
      ? '장치를 확인한 뒤 참가를 요청하세요.'
      : '장치 권한이 없습니다. 장치 없이 참가하거나 권한을 다시 확인할 수 있습니다.';
  } catch (error) { status.textContent = error.message; }
  finally {
    micOn = localStream.getAudioTracks().length > 0;
    camOn = localStream.getVideoTracks().length > 0;
    for (const track of localStream.getTracks()) {
      track.onended = () => {
        if (track.kind === 'audio') { micOn = false; stopRecognition(); }
        else camOn = false;
        syncMediaControls();
      };
    }
    document.getElementById('preview-video').srcObject = localStream;
    preparing = false;
    document.getElementById('join-meeting').disabled = leaving;
    syncMediaControls();
  }
}

async function joinMeeting() {
  if (!myDID || preparing || joining || joined || leaving) return;
  joining = true;
  myName = document.getElementById('join-name').value.trim().slice(0,40) || 'Anonymous';
  try { localStorage.setItem('weaid-meet-name', myName); } catch (_) {}
  document.getElementById('join-meeting').disabled = true;
  document.getElementById('preview-status').textContent = '회의에 연결하고 있습니다…';
  try {
    const response = await fetch('/api/meet/config', {cache:'no-store', signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('연결 설정을 불러올 수 없습니다.');
    const config = await response.json();
    ICE_SERVERS = config.iceServers;
    if (leaving) return;
    connectWS();
  } catch (error) {
    joining = false;
    document.getElementById('preview-status').textContent = '연결 설정을 불러오지 못했습니다. 다시 시도하세요.';
    document.getElementById('join-meeting').disabled = false;
  }
}

function toggleHand() {
  if (!joined) return;
  raised = !raised;
  wsSend({type:'hand-state',raised});
  document.getElementById('btn-hand').setAttribute('aria-pressed', String(raised));
  document.getElementById('btn-hand').className = 'tool-btn ' + (raised ? 'active-share' : 'accent');
  updateGrid();
}

function updateRecordingNotice() {
  const names = Object.values(peerInfo).filter(p => p.recording).map(p => p.name);
  if (recorder && recorder.state !== 'inactive') names.unshift('나');
  document.getElementById('recording-notice').textContent = names.length
    ? `⏺ 녹화 중: ${names.join(', ')} · 녹화에는 참가자 음성이 포함될 수 있습니다.` : '';
}

function addRecordingAudio(stream) {
  if (!audioContext || !stream) return;
  for (const track of stream.getAudioTracks()) {
    if (audioSources.has(track) || track.readyState !== 'live') continue;
    const source = audioContext.createMediaStreamSource(new MediaStream([track]));
    source.connect(audioDestination);
    audioSources.set(track, source);
  }
}

let recordingFinished = Promise.resolve();
async function toggleRecording() {
  if (recordingPending) return;
  if (recorder) { await stopRecording(); return; }
  if (!joined) return;
  if (!window.MediaRecorder || !navigator.mediaDevices?.getDisplayMedia ||
      !window.AudioContext) {
    toast('이 브라우저는 회의 녹화를 지원하지 않습니다.', 5000); return;
  }
  if (!confirm('참가자 모두의 동의를 먼저 받으세요. 녹화할 회의 탭 또는 화면을 선택합니다.\n영상과 회의 참가자의 음성을 이 기기에 저장합니다 (최대 30분 또는 256MB).')) return;
  recordingPending = true;
  document.getElementById('btn-record').disabled = true;
  try {
    recordingStream = await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});
    if (!joined || leaving) { releaseRecording(); return; }
    audioContext = new AudioContext();
    await audioContext.resume();
    if (!joined || leaving || !audioContext) { releaseRecording(); return; }
    audioDestination = audioContext.createMediaStreamDestination();
    addRecordingAudio(localStream);
    Object.values(peerStreams).forEach(addRecordingAudio);
    const stream = new MediaStream([
      ...recordingStream.getVideoTracks(), ...audioDestination.stream.getAudioTracks(),
    ]);
    const mimeType = ['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/mp4','video/webm']
      .find(type => MediaRecorder.isTypeSupported(type));
    recorder = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream);
    const activeRecorder = recorder;
    const chunks = [];
    let bytes = 0;
    activeRecorder.ondataavailable = e => {
      if (e.data.size) { chunks.push(e.data); bytes += e.data.size; }
      if (bytes >= 256 * 1024 * 1024 && activeRecorder.state !== 'inactive') {
        toast('녹화 크기 제한에 도달하여 저장합니다.'); stopRecording();
      }
    };
    recordingFinished = new Promise(resolve => {
      activeRecorder.onstop = () => {
        try {
          if (chunks.length) {
            const blob = new Blob(chunks, {type:activeRecorder.mimeType});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `weaid-meet-${new Date().toISOString().replace(/[:.]/g,'-')}.${blob.type.includes('mp4')?'mp4':'webm'}`;
            document.body.appendChild(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
            toast('녹화 파일을 저장했습니다.');
          }
        } finally {
          recorder = null; releaseRecording();
          wsSend({type:'recording-state',recording:false});
          document.getElementById('btn-record').setAttribute('aria-pressed', 'false');
          document.getElementById('btn-record').innerHTML = '⏺<small>녹화</small>';
          updateRecordingNotice(); resolve();
        }
      };
    });
    activeRecorder.onerror = () => { toast('녹화 중 오류가 발생했습니다.'); stopRecording(); };
    recordingStream.getVideoTracks()[0].onended = () => stopRecording();
    activeRecorder.start(1000);
    recordingTimer = setTimeout(() => { toast('30분 녹화 제한에 도달했습니다.'); stopRecording(); }, 30*60*1000);
    wsSend({type:'recording-state',recording:true});
    document.getElementById('btn-record').setAttribute('aria-pressed', 'true');
    document.getElementById('btn-record').innerHTML = '⏹<small>녹화 저장</small>';
    updateRecordingNotice();
  } catch (error) {
    recorder = null; releaseRecording();
    recordingFinished = Promise.resolve();
    toast('녹화가 취소되었거나 시작하지 못했습니다.', 4000);
  } finally {
    recordingPending = false;
    document.getElementById('btn-record').disabled = false;
  }
}

function releaseRecording() {
  clearTimeout(recordingTimer);
  if (recordingStream) recordingStream.getTracks().forEach(t => { t.onended = null; t.stop(); });
  recordingStream = null;
  for (const source of audioSources.values()) source.disconnect();
  audioSources.clear();
  if (audioDestination) audioDestination.stream.getTracks().forEach(t => t.stop());
  audioDestination = null;
  if (audioContext) audioContext.close().catch(() => {});
  audioContext = null;
}

function stopRecording() {
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  else if (!recorder) releaseRecording();
  return recordingFinished;
}

function toggleCaptions() {
  if (!joined) return;
  if (captionsOn) { stopCaptions(); return; }
  const supported = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (supported && !confirm('자막을 켜면 내 음성이 브라우저의 음성 인식 서비스에 전달될 수 있으며, 인식된 한국어 자막이 회의 참가자에게 공유됩니다. 계속할까요?')) return;
  captionsOn = true;
  document.getElementById('btn-captions').setAttribute('aria-pressed', 'true');
  document.getElementById('btn-captions').className = 'tool-btn active-share';
  if (supported && micOn) startRecognition();
  else toast('다른 참가자가 공유하는 자막을 표시합니다. 내 음성 인식은 사용할 수 없습니다.', 5000);
}

function startRecognition() {
  const API = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!API || recognition || !captionsOn || !micOn || !joined) return;
  const current = new API();
  recognition = current;
  current.lang = 'ko-KR'; current.continuous = true; current.interimResults = false;
  current.onresult = e => {
    if (recognition !== current || !captionsOn || !micOn || !joined) return;
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) wsSend({type:'caption',text:e.results[i][0].transcript.slice(0,500)});
    }
  };
  current.onerror = () => {
    stopRecognition();
    toast('내 음성 자막 인식을 중단했습니다. 브라우저 지원·권한을 확인하세요.', 5000);
  };
  current.onend = () => {
    if (recognition !== current) return;
    recognition = null;
    if (captionsOn && joined && micOn) startRecognition();
  };
  try { current.start(); } catch (_) { recognition = null; toast('음성 인식을 시작하지 못했습니다.'); }
}

function stopRecognition() {
  const current = recognition;
  recognition = null;
  if (current) { current.onend = null; current.abort(); }
}

function stopCaptions() {
  captionsOn = false; stopRecognition();
  clearTimeout(captionTimer);
  document.getElementById('captions').textContent = '';
  document.getElementById('btn-captions').setAttribute('aria-pressed','false');
  document.getElementById('btn-captions').className = 'tool-btn accent';
}

// ─── Media controls ──────────────────────────────────────────────
function toggleMic() {
  if (!localStream?.getAudioTracks().length) { toast('사용 가능한 마이크가 없습니다.'); return; }
  micOn = !micOn;
  if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = micOn);
  if (!micOn) stopRecognition();
  else if (captionsOn) startRecognition();
  syncMediaControls();
}

function toggleCam() {
  if (!localStream?.getVideoTracks().length) { toast('사용 가능한 카메라가 없습니다.'); return; }
  camOn = !camOn;
  if (localStream) localStream.getVideoTracks().forEach(t => t.enabled = camOn);
  syncMediaControls();
}

function syncMediaControls() {
  document.getElementById('btn-mic').className = 'tool-btn ' + (micOn?'on':'off');
  document.getElementById('btn-mic').innerHTML = `${micOn?'🎤':'🔇'}<small>마이크</small>`;
  document.getElementById('btn-mic').setAttribute('aria-pressed', String(micOn));
  document.getElementById('btn-cam').className = 'tool-btn ' + (camOn?'on':'off');
  document.getElementById('btn-cam').innerHTML = `${camOn?'📹':'🚫'}<small>카메라</small>`;
  document.getElementById('btn-cam').setAttribute('aria-pressed', String(camOn));
  document.getElementById('preview-mic').textContent = micOn ? '마이크 켜짐' : '마이크 꺼짐';
  document.getElementById('preview-cam').textContent = camOn ? '카메라 켜짐' : '카메라 꺼짐';
  document.getElementById('preview-mic').setAttribute('aria-pressed', String(micOn));
  document.getElementById('preview-cam').setAttribute('aria-pressed', String(camOn));
  document.getElementById('preview-video').style.visibility = camOn ? 'visible' : 'hidden';
  if (joined) wsSend({type:'media-state', audio:micOn, video:camOn || screenOn});
  updateGrid();
}

async function replaceVideo(track) {
  await Promise.all(Object.values(peers).map(async pc => {
    const transceiver = pc.getTransceivers().find(t => t.receiver.track.kind === 'video');
    if (transceiver) await transceiver.sender.replaceTrack(track || null);
  }));
}

async function toggleScreen() {
  if (!joined || sharing) return;
  if (!navigator.mediaDevices?.getDisplayMedia) { toast('이 브라우저는 화면 공유를 지원하지 않습니다.'); return; }
  sharing = true;
  const version = ++shareVersion;
  if (!screenOn) {
    try {
      const captured = await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});
      if (!joined || leaving || version !== shareVersion) {
        captured.getTracks().forEach(t => t.stop()); return;
      }
      screenStream = captured;
      const screenTrack = screenStream.getVideoTracks()[0];
      await replaceVideo(screenTrack);
      screenTrack.onended = () => stopScreen();
      screenOn = true;
      document.getElementById('btn-screen').className = 'tool-btn active-share';
      document.getElementById('btn-screen').innerHTML = '⏹️<small>공유 중지</small>';
      toast('🖥️ 화면 공유 시작');
    } catch(e) { await stopScreen(); toast('화면 공유가 취소되었거나 실패했습니다.'); }
    finally { sharing = false; syncMediaControls(); }
  } else {
    try { await stopScreen(); } finally { sharing = false; }
  }
}

async function stopScreen() {
  ++shareVersion;
  if (screenStream) screenStream.getTracks().forEach(t => { t.onended = null; t.stop(); });
  screenStream = null; screenOn = false;
  try { await replaceVideo(localStream?.getVideoTracks()[0]); } catch(error) { console.warn(error); }
  document.getElementById('btn-screen').className = 'tool-btn accent';
  document.getElementById('btn-screen').innerHTML = '🖥️<small>화면 공유</small>';
  syncMediaControls();
}

function cleanup() {
  leaving = true; joined = false; ++shareVersion;
  stopCaptions(); stopRecording();
  if (ws) ws.close();
  Object.values(peers).forEach(pc=>pc.close());
  if (localStream) localStream.getTracks().forEach(t=>t.stop());
  if (screenStream) screenStream.getTracks().forEach(t=>t.stop());
}

async function leaveRoom() {
  if (recordingPending) { toast('녹화 화면 선택을 먼저 완료하거나 취소하세요.'); return; }
  const finished = stopRecording();
  cleanup();
  await finished;
  window.location.href = '/meet';
}

// ─── Init ────────────────────────────────────────────────────────
(async () => {
  // Load identity
  let identity;
  try { identity = await loadIdentity(); } catch (error) { console.warn(error); }
  if (!identity) {
    document.getElementById('preview-status').textContent = '먼저 홈에서 브라우저 신원을 생성해 주세요.';
    const link = document.querySelector('#prejoin a');
    link.href = '/meet?room=' + encodeURIComponent(ROOM_ID);
    link.textContent = '신원 생성 후 이 회의에 참가하기';
    return;
  }
  myDID = identity.did;
  document.getElementById('my-did-display').textContent = myDID.slice(0,44)+'…';

  document.getElementById('preview-room').textContent = ROOM_ID;
  document.getElementById('join-name').value = myName;
  await prepareMedia();

  // Draggable PiP
  const pip = document.getElementById('local-pip');
  let dragging=false, ox=0, oy=0, sx=0, sy=0;
  pip.addEventListener('mousedown', e=>{
    dragging=true; ox=e.clientX; oy=e.clientY;
    const style = getComputedStyle(pip);
    sx=parseFloat(style.right)||0; sy=parseFloat(style.bottom)||0;
  });
  document.addEventListener('mousemove', e=>{
    if(!dragging)return;
    pip.style.right=(sx-(e.clientX-ox))+'px';
    pip.style.bottom=(sy-(e.clientY-oy))+'px';
  });
  document.addEventListener('mouseup',()=>dragging=false);
})();
window.addEventListener('pagehide', cleanup);
window.addEventListener('beforeunload', e => {
  if (recorder || recordingPending) { e.preventDefault(); e.returnValue = ''; }
});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────
#  Route handlers
# ─────────────────────────────────────────────────────────────────

@app.get("/meet", include_in_schema=False)
async def meet_home_page():
    """WeAid Meet 랜딩 페이지 — DID 신원 생성 + 방 입장"""
    return HTMLResponse(_MEET_HOME_HTML)


@app.get("/meet/room", include_in_schema=False)
async def meet_room_page():
    """WeAid Meet 통화 방 페이지"""
    return HTMLResponse(_MEET_ROOM_HTML)


logger.info("🔐 WeAid Meet 모듈 로드됨 — /meet 에서 DID 기반 화상회의 이용 가능")
