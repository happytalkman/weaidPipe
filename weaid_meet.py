"""
WeAid Meet — DID 기반 탈중앙화 실시간 다자간 음성·영상·채팅
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
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from pipecat.runner.run import app

# ─────────────────────────────────────────────────────────────────
#  Room state
# ─────────────────────────────────────────────────────────────────

@dataclass
class Participant:
    did: str
    name: str
    ws: WebSocket
    audio: bool = True
    video: bool = True
    joined_at: float = field(default_factory=time.time)

    def info(self) -> dict:
        return {
            "did": self.did,
            "name": self.name,
            "audio": self.audio,
            "video": self.video,
        }


class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, Dict[str, Participant]] = {}
        self._chat_history: Dict[str, list] = {}

    def get_or_create(self, room_id: str) -> Dict[str, Participant]:
        if room_id not in self.rooms:
            self.rooms[room_id] = {}
            self._chat_history[room_id] = []
        return self.rooms[room_id]

    def join(self, room_id: str, p: Participant):
        self.get_or_create(room_id)[p.did] = p

    def leave(self, room_id: str, did: str):
        if room_id in self.rooms:
            self.rooms[room_id].pop(did, None)
            if not self.rooms[room_id]:
                del self.rooms[room_id]
                self._chat_history.pop(room_id, None)

    def peers_of(self, room_id: str, exclude: str = "") -> list:
        return [p.info() for d, p in self.rooms.get(room_id, {}).items() if d != exclude]

    def room_list(self) -> list:
        return [{"id": rid, "count": len(ps)} for rid, ps in self.rooms.items()]

    def chat_history(self, room_id: str) -> list:
        return self._chat_history.get(room_id, [])[-30:]

    def add_chat(self, room_id: str, msg: dict):
        self._chat_history.setdefault(room_id, []).append(msg)
        if len(self._chat_history[room_id]) > 200:
            self._chat_history[room_id] = self._chat_history[room_id][-200:]

    async def broadcast(self, room_id: str, message: dict, exclude: str = ""):
        payload = json.dumps(message, ensure_ascii=False)
        for did, p in list(self.rooms.get(room_id, {}).items()):
            if did != exclude:
                try:
                    await p.ws.send_text(payload)
                except Exception:
                    pass

    async def send_to(self, room_id: str, to_did: str, message: dict):
        room = self.rooms.get(room_id, {})
        if to_did in room:
            try:
                await room[to_did].ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                pass


_rooms = RoomManager()


# ─────────────────────────────────────────────────────────────────
#  REST endpoints
# ─────────────────────────────────────────────────────────────────

@app.get("/api/meet/rooms", include_in_schema=False)
async def list_rooms():
    return JSONResponse({"rooms": _rooms.room_list()})


# ─────────────────────────────────────────────────────────────────
#  WebSocket Signaling
# ─────────────────────────────────────────────────────────────────

@app.websocket("/meet/ws/{room_id}")
async def meet_signaling(websocket: WebSocket, room_id: str):
    await websocket.accept()
    participant: Optional[Participant] = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mtype = msg.get("type", "")

            # ── join ──────────────────────────────────────────────
            if mtype == "join":
                did = str(msg.get("did", "")).strip()
                name = str(msg.get("name", "Anonymous")).strip()[:40]
                if not did.startswith("did:"):
                    await websocket.send_text(json.dumps({"type": "error", "message": "Invalid DID"}))
                    continue

                participant = Participant(did=did, name=name, ws=websocket)
                peers = _rooms.peers_of(room_id)          # snapshot BEFORE join
                _rooms.join(room_id, participant)

                await websocket.send_text(json.dumps({
                    "type": "joined",
                    "did": did,
                    "room": room_id,
                    "peers": peers,
                    "chat_history": _rooms.chat_history(room_id),
                }, ensure_ascii=False))

                await _rooms.broadcast(room_id, {
                    "type": "peer-joined",
                    **participant.info(),
                }, exclude=did)

                logger.info(f"[WeAid Meet] '{name}' ({did[:28]}…) joined '{room_id}' "
                            f"({len(peers)+1} in room)")

            # ── WebRTC signaling (offer / answer / ice) ───────────
            elif mtype in ("offer", "answer", "ice"):
                if not participant:
                    continue
                to = msg.get("to", "")
                await _rooms.send_to(room_id, to, {**msg, "from": participant.did})

            # ── chat ─────────────────────────────────────────────
            elif mtype == "chat":
                if not participant:
                    continue
                chat_msg = {
                    "type": "chat",
                    "from": participant.did,
                    "name": participant.name,
                    "text": str(msg.get("text", "")).strip()[:1000],
                    "time": datetime.now().strftime("%H:%M"),
                }
                _rooms.add_chat(room_id, chat_msg)
                await _rooms.broadcast(room_id, chat_msg)

            # ── media state update ────────────────────────────────
            elif mtype == "media-state":
                if not participant:
                    continue
                participant.audio = bool(msg.get("audio", participant.audio))
                participant.video = bool(msg.get("video", participant.video))
                await _rooms.broadcast(room_id, {
                    "type": "media-state",
                    "did": participant.did,
                    "audio": participant.audio,
                    "video": participant.video,
                }, exclude=participant.did)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WeAid Meet] WS error: {e}")
    finally:
        if participant:
            _rooms.leave(room_id, participant.did)
            await _rooms.broadcast(room_id, {
                "type": "peer-left",
                "did": participant.did,
                "name": participant.name,
            })
            logger.info(f"[WeAid Meet] '{participant.name}' left '{room_id}'")


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
    el.innerHTML = d.rooms.map(rm => `
      <div class="room-chip" onclick="quickJoin('${rm.id}')">
        <span class="room-chip-name">🏠 ${rm.id}</span>
        <span class="room-chip-meta"><div class="green-dot"></div>${rm.count}명 참가 중</span>
      </div>`).join('');
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
</style>
</head>
<body>
<div id="app">
  <!-- Top bar -->
  <div id="topbar">
    <div class="tb-room">
      🎥 <span id="room-name-display">로딩 중...</span>
    </div>
    <div class="tb-did" id="my-did-display">DID 로딩 중...</div>
    <div class="tb-count">👥 <span id="peer-count">1</span>명</div>
  </div>

  <!-- Main -->
  <div id="main">
    <div id="video-area">
      <div id="conn-banner">🔄 피어 연결 중...</div>
      <!-- Video grid -->
      <div id="video-grid"></div>
      <!-- Local PiP (shown when others are present) -->
      <div id="local-pip">
        <video id="local-pip-video" autoplay muted playsinline></video>
        <div class="tile-name">나 (PiP)</div>
      </div>
    </div>

    <!-- Right panel -->
    <div id="right-panel">
      <div class="panel-tabs">
        <div class="panel-tab active" id="tab-chat" onclick="switchTab('chat')">💬 채팅</div>
        <div class="panel-tab" id="tab-peers" onclick="switchTab('peers')">👥 참가자</div>
      </div>
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
    <button class="tool-btn on" id="btn-mic" onclick="toggleMic()" title="마이크">🎤</button>
    <button class="tool-btn on" id="btn-cam" onclick="toggleCam()" title="카메라">📹</button>
    <button class="tool-btn accent" id="btn-screen" onclick="toggleScreen()" title="화면 공유">🖥️</button>
    <div class="tool-sep"></div>
    <button class="tool-btn accent" id="btn-chat" onclick="togglePanel()" title="채팅">💬</button>
    <div class="tool-sep"></div>
    <button class="tool-btn danger" onclick="leaveRoom()" title="나가기">📴</button>
  </div>
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
const peers = {};      // did → RTCPeerConnection
const peerInfo = {};   // did → {name, audio, video}
const peerStreams = {}; // did → MediaStream
let micOn = true, camOn = true, screenOn = false, panelOpen = false;

const ICE_SERVERS = [
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
  const count = Object.keys(peers).length + 1; // including local
  document.getElementById('peer-count').textContent = count;
  const grid = document.getElementById('video-grid');
  const pip  = document.getElementById('local-pip');

  if (Object.keys(peers).length === 0) {
    // Only me: full screen local video
    pip.style.display = 'none';
    grid.innerHTML = '';
    const tile = makeTile('local', localStream, myName + ' (나)', myDID, true);
    grid.appendChild(tile);
    setGridCols(grid, 1);
  } else {
    // Others: show local as PiP
    pip.style.display = 'block';
    const pipVid = document.getElementById('local-pip-video');
    pipVid.srcObject = localStream;

    // Build grid with remote peers
    grid.innerHTML = '';
    for (const [did, stream] of Object.entries(peerStreams)) {
      const info = peerInfo[did] || {};
      const tile = makeTile(did, stream, info.name || did.slice(0,16)+'…', did, false, info);
      grid.appendChild(tile);
    }
    const n = Object.keys(peerStreams).length;
    setGridCols(grid, n);
  }
  updatePeersList();
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
  av.style.display = stream ? 'none' : 'flex';
  div.appendChild(av);

  // DID badge
  const didBadge = document.createElement('div');
  didBadge.className = 'tile-did';
  didBadge.textContent = (did||'').slice(0,30)+'…';
  div.appendChild(didBadge);

  // Name badge
  const nb = document.createElement('div');
  nb.className = 'tile-name';
  nb.innerHTML = `<span>${name}</span>`;
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
  return div;
}

// ─── WebRTC peer management ──────────────────────────────────────
function createPC(remoteDid) {
  if (peers[remoteDid]) return peers[remoteDid];
  const pc = new RTCPeerConnection({iceServers: ICE_SERVERS});

  // Add local tracks
  if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

  pc.onicecandidate = e => {
    if (e.candidate) wsSend({type:'ice', to:remoteDid, candidate:e.candidate.toJSON()});
  };
  pc.ontrack = e => {
    const stream = e.streams[0];
    peerStreams[remoteDid] = stream;
    updateGrid();
  };
  pc.onconnectionstatechange = () => {
    const s = pc.connectionState;
    const banner = document.getElementById('conn-banner');
    if (s === 'connected') { banner.style.display='none'; }
    else if (s === 'connecting' || s === 'new') { banner.style.display='block'; }
    else if (s === 'failed') { toast('⚠ 피어 연결 실패: '+remoteDid.slice(0,16)); }
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
  const answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);
  wsSend({type:'answer', to:remoteDid, sdp:answer.sdp});
}

async function handleAnswer(remoteDid, sdp) {
  const pc = peers[remoteDid];
  if (pc && pc.signalingState !== 'stable') await pc.setRemoteDescription({type:'answer', sdp});
}

async function handleIce(remoteDid, candidate) {
  const pc = peers[remoteDid];
  if (pc) try { await pc.addIceCandidate(candidate); } catch(_){}
}

function removePeer(did) {
  if (peers[did]) { peers[did].close(); delete peers[did]; }
  delete peerStreams[did];
  delete peerInfo[did];
  updateGrid();
}

// ─── WebSocket ───────────────────────────────────────────────────
function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/meet/ws/${encodeURIComponent(ROOM_ID)}`);

  ws.onopen = () => {
    wsSend({type:'join', did:myDID, name:myName});
    toast('✅ 방 연결됨: ' + ROOM_ID);
  };

  ws.onmessage = async (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch(_){ return; }
    const t = msg.type;

    if (t === 'joined') {
      // Create offers to all existing peers
      for (const peer of msg.peers) {
        peerInfo[peer.did] = peer;
        await makeOffer(peer.did);
      }
      // Load chat history
      for (const cm of (msg.chat_history||[])) appendChat(cm, false);
      updateGrid();
    }
    else if (t === 'peer-joined') {
      peerInfo[msg.did] = msg;
      toast(`👋 ${msg.name} 입장`);
      updatePeersList();
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
  };

  ws.onclose = () => {
    toast('⚠ 서버 연결 끊김. 3초 후 재연결...', 3000);
    setTimeout(connectWS, 3000);
  };
}

// ─── Chat ────────────────────────────────────────────────────────
function appendChat(msg, scroll=true) {
  const isMine = msg.from === myDID;
  const el = document.createElement('div');
  el.className = 'chat-msg' + (isMine ? ' mine' : '');
  el.innerHTML = `<div class="chat-meta"><span>${isMine?'나':msg.name||'?'}</span><span>${msg.time||''}</span></div><div class="chat-text">${escHtml(msg.text)}</div>`;
  document.getElementById('chat-list').appendChild(el);
  if (scroll) { const b=document.getElementById('panel-body-chat'); b.scrollTop=b.scrollHeight; }
}

function sendChat() {
  const inp = document.getElementById('chat-input');
  const text = inp.value.trim();
  if (!text) return;
  wsSend({type:'chat', text});
  inp.value = '';
}

function escHtml(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── Participants panel ───────────────────────────────────────────
function updatePeersList() {
  const el = document.getElementById('peers-list');
  const allPeers = [{did:myDID, name:myName+'(나)', audio:micOn, video:camOn},
    ...Object.values(peerInfo)];
  el.innerHTML = allPeers.map(p => `
    <div class="peer-item">
      <div class="peer-avatar">${(p.name||'?')[0].toUpperCase()}</div>
      <div class="peer-info">
        <div class="peer-name">${escHtml(p.name||'')}</div>
        <div class="peer-did-mini">${(p.did||'').slice(0,30)}…</div>
      </div>
      <div class="peer-status">
        ${p.audio===false?'🔇':'🎤'} ${p.video===false?'📷':'📹'}
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

// ─── Media controls ──────────────────────────────────────────────
function toggleMic() {
  micOn = !micOn;
  if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = micOn);
  document.getElementById('btn-mic').className = 'tool-btn ' + (micOn?'on':'off');
  document.getElementById('btn-mic').textContent = micOn ? '🎤' : '🔇';
  wsSend({type:'media-state', audio:micOn, video:camOn});
}

function toggleCam() {
  camOn = !camOn;
  if (localStream) localStream.getVideoTracks().forEach(t => t.enabled = camOn);
  document.getElementById('btn-cam').className = 'tool-btn ' + (camOn?'on':'off');
  document.getElementById('btn-cam').textContent = camOn ? '📹' : '🚫';
  wsSend({type:'media-state', audio:micOn, video:camOn});
}

async function toggleScreen() {
  if (!screenOn) {
    try {
      screenStream = await navigator.mediaDevices.getDisplayMedia({video:true,audio:true});
      const screenTrack = screenStream.getVideoTracks()[0];
      // Replace video track in all peer connections
      for (const pc of Object.values(peers)) {
        const sender = pc.getSenders().find(s=>s.track&&s.track.kind==='video');
        if (sender) await sender.replaceTrack(screenTrack);
      }
      screenTrack.onended = () => { screenOn=true; toggleScreen(); };
      screenOn = true;
      document.getElementById('btn-screen').className = 'tool-btn active-share';
      document.getElementById('btn-screen').textContent = '⏹️';
      toast('🖥️ 화면 공유 시작');
    } catch(e) { toast('화면 공유 취소됨'); }
  } else {
    // Restore camera track
    if (localStream) {
      const camTrack = localStream.getVideoTracks()[0];
      for (const pc of Object.values(peers)) {
        const sender = pc.getSenders().find(s=>s.track&&s.track.kind==='video');
        if (sender && camTrack) await sender.replaceTrack(camTrack);
      }
    }
    if (screenStream) screenStream.getTracks().forEach(t=>t.stop());
    screenStream = null; screenOn = false;
    document.getElementById('btn-screen').className = 'tool-btn accent';
    document.getElementById('btn-screen').textContent = '🖥️';
    toast('🖥️ 화면 공유 종료');
  }
}

function leaveRoom() {
  if (ws) ws.close();
  Object.values(peers).forEach(pc=>pc.close());
  if (localStream) localStream.getTracks().forEach(t=>t.stop());
  window.location.href = '/meet';
}

// ─── Init ────────────────────────────────────────────────────────
(async () => {
  // Load identity
  const identity = await loadIdentity();
  if (!identity) {
    toast('⚠ DID 신원이 없습니다. /meet 에서 생성하세요.', 5000);
    setTimeout(() => window.location.href='/meet', 3000);
    return;
  }
  myDID = identity.did;
  document.getElementById('my-did-display').textContent = myDID.slice(0,44)+'…';

  // Get media
  try {
    localStream = await navigator.mediaDevices.getUserMedia({video:true, audio:true});
  } catch(_) {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({audio:true});
      toast('⚠ 카메라 없이 오디오만 연결됩니다.');
    } catch(__) {
      toast('⚠ 마이크/카메라 접근 거부됨. 오디오 없이 참가합니다.', 4000);
      localStream = new MediaStream();
    }
  }

  updateGrid(); // Show local video immediately

  // Connect WS signaling
  connectWS();

  // Draggable PiP
  const pip = document.getElementById('local-pip');
  let dragging=false, ox=0, oy=0, sx=0, sy=0;
  pip.addEventListener('mousedown', e=>{
    dragging=true; ox=e.clientX; oy=e.clientY;
    sx=pip.offsetRight||0; sy=pip.offsetBottom||0;
  });
  document.addEventListener('mousemove', e=>{
    if(!dragging)return;
    pip.style.right=(sx-(e.clientX-ox))+'px';
    pip.style.bottom=(sy-(e.clientY-oy))+'px';
  });
  document.addEventListener('mouseup',()=>dragging=false);
})();
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
