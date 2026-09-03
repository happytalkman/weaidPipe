"""Authenticated multi-user HTTP and Gemini Live WebSocket API."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.api_key_validator import normalize_gemini_api_key, validate_gemini_api_key
from core.tenant import tenant_scope
from memory.memory_manager import format_memory_for_prompt, load_memory

from .auth import get_current_user, websocket_user
from .config import settings
from .database import SessionLocal, get_db, init_db
from .models import (
    CallLog,
    Contact,
    ConversationThread,
    EvidenceRecord,
    ScheduledEvent,
    ThreadMessage,
    User,
)
from .rate_limit import limiter
from .repositories import add_chat_message, recent_chat_messages
from .schemas import (
    CallCreate,
    CallEnd,
    ChatRequest,
    ChatResponse,
    ContactCreate,
    GeminiKeyRequest,
    LoginRequest,
    MessageCreate,
    ScheduleCreate,
    SessionView,
    ThreadCreate,
    TripleProposal,
    UserCreate,
    UserView,
)
from .secret_service import (
    delete_user_secret,
    get_user_secret,
    has_user_secret,
    set_user_secret,
)
from .security import create_access_token, hash_password, verify_password
from .websocket_client import WebSocketClient


class LiveSessionRegistry:
    def __init__(self):
        self._sessions: dict[str, tuple[Any, WebSocketClient, asyncio.Task]] = {}
        self._lock = asyncio.Lock()

    async def replace(self, user_id: str, engine, client: WebSocketClient, task: asyncio.Task) -> None:
        async with self._lock:
            previous = self._sessions.pop(user_id, None)
            self._sessions[user_id] = (engine, client, task)
        if previous:
            previous[0].request_shutdown()
            previous[2].cancel()

    async def remove(self, user_id: str, task: asyncio.Task) -> None:
        async with self._lock:
            current = self._sessions.get(user_id)
            if current and current[2] is task:
                self._sessions.pop(user_id, None)

    def status(self, user_id: str) -> dict:
        current = self._sessions.get(user_id)
        if not current:
            return {"state": "OFFLINE", "connected": False}
        engine, client, task = current
        return {
            "state": client.state,
            "connected": not task.done(),
            "voice": engine._get_current_voice(),
            "cloud_safe": True,
        }

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for engine, client, task in sessions:
            engine.request_shutdown()
            task.cancel()
            await client.close()


live_sessions = LiveSessionRegistry()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production()
    if settings.auto_create_tables:
        await asyncio.to_thread(init_db)
    await limiter.connect()
    try:
        yield
    finally:
        await live_sessions.close_all()
        await limiter.close()


app = FastAPI(
    title="JARVIS Cloud API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _user_view(user: User, db: Session) -> UserView:
    return UserView(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        gemini_configured=has_user_secret(db, user.id, "gemini_api_key"),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "jarvis-api"}


@app.post("/rlaif/preferences", status_code=201)
async def record_rlaif_preference(payload: dict[str, Any]) -> dict:
    """RLAIF preference recording endpoint (Constitutional AI data pipeline).

    Accepts original vs revised response pairs with the chosen answer and the
    constitution score. Records are appended as JSONL for future fine-tuning;
    no model weights are touched by this service.
    """
    try:
        from core.constitution import record_rlaif
        path = record_rlaif(
            question=str(payload.get("question", "")),
            original=str(payload.get("original", "")),
            revised=str(payload.get("revised", "")),
            chosen=str(payload.get("chosen", "revised")),
            score=int(payload.get("score", 0)),
            reasons=[str(r) for r in (payload.get("reasons") or [])],
        )
        return {"ok": True, "recorded": str(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid rlaif record: {exc}")


@app.get("/ontology/graph")
def ontology_graph(user: User = Depends(get_current_user)) -> dict:
    """누적 대화 그래프 (온톨로지 대시보드 초기 로드용)."""
    try:
        from core import graph_store
        return graph_store.load_for_user(user.id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/ontology/propose")
def propose_ontology(payload: TripleProposal, user: User = Depends(get_current_user)) -> dict:
    """Validate proposed facts before committing them to the mindmap graph."""
    from core import graph_store
    from core.shacl_validator import validate_batch
    from core.triple_extractor import extract_triples_from_turn

    proposals = payload.proposed_triples or extract_triples_from_turn(payload.question, payload.answer)
    results = validate_batch(proposals)
    valid = [item["triple"] for item in results if item["valid"]]
    store = graph_store.load_for_user(user.id)
    if valid:
        graph_store.merge_turn(store, payload.question, payload.answer, heur_triples=valid)
        graph_store.save_for_user(store, user.id)
    return {
        "committed": len(valid),
        "valid_count": len(valid),
        "validated_triples": valid,
        "errors": [item for item in results if not item["valid"]],
    }


@app.get("/identity/did")
def get_did(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    did = get_user_secret(db, user.id, "did")
    if not did:
        did = f"did:weaid:{user.id}"
        set_user_secret(db, user.id, "did", did)
    return {"did": did, "method": "weaid", "controller": user.id}


@app.post("/connecting/contacts", status_code=201)
def add_contact(payload: ContactCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(Contact).where(Contact.user_id == user.id, Contact.friend_did == payload.friend_did))
    if existing:
        raise HTTPException(status_code=409, detail="Contact already exists")
    row = Contact(user_id=user.id, friend_did=payload.friend_did, display_name=payload.display_name)
    db.add(row)
    db.commit()
    return {"id": row.id, "friend_did": row.friend_did, "display_name": row.display_name}


@app.get("/connecting/contacts")
def list_contacts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Contact).where(Contact.user_id == user.id).order_by(Contact.created_at.desc())).all()
    return [{"id": row.id, "friend_did": row.friend_did, "display_name": row.display_name, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/connecting/threads", status_code=201)
def create_thread(payload: ThreadCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = ConversationThread(owner_id=user.id, participant_dids=payload.participant_dids, topic=payload.topic)
    db.add(row)
    db.commit()
    return {"id": row.id, "participant_dids": row.participant_dids, "topic": row.topic}


@app.post("/connecting/threads/{thread_id}/messages", status_code=201)
def post_thread_message(thread_id: str, payload: MessageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    thread = db.scalar(select(ConversationThread).where(ConversationThread.id == thread_id, ConversationThread.owner_id == user.id))
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    did = get_did(user, db)["did"]
    row = ThreadMessage(thread_id=thread.id, sender_did=did, content=payload.content)
    db.add(row)
    db.add(EvidenceRecord(user_id=user.id, event_type="message", source=thread.id, payload={"sender_did": did, "content": payload.content}))
    db.commit()
    return {"id": row.id, "thread_id": row.thread_id, "sender_did": row.sender_did, "content": row.content, "created_at": row.created_at.isoformat()}


@app.get("/connecting/threads/{thread_id}/messages")
def list_thread_messages(thread_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    thread = db.scalar(select(ConversationThread).where(ConversationThread.id == thread_id, ConversationThread.owner_id == user.id))
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    rows = db.scalars(select(ThreadMessage).where(ThreadMessage.thread_id == thread.id).order_by(ThreadMessage.created_at)).all()
    return [{"id": row.id, "sender_did": row.sender_did, "content": row.content, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/connecting/schedule", status_code=201)
def create_schedule(payload: ScheduleCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = ScheduledEvent(user_id=user.id, title=payload.title, scheduled_at=payload.scheduled_at, participant_dids=payload.participant_dids)
    db.add(row)
    db.add(EvidenceRecord(user_id=user.id, event_type="schedule", source=payload.title, payload={"scheduled_at": payload.scheduled_at.isoformat(), "participant_dids": payload.participant_dids}))
    db.commit()
    return {"id": row.id, "title": row.title, "scheduled_at": row.scheduled_at.isoformat(), "status": row.status}


@app.get("/connecting/schedule")
def list_schedule(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ScheduledEvent).where(ScheduledEvent.user_id == user.id).order_by(ScheduledEvent.scheduled_at)).all()
    return [{"id": row.id, "title": row.title, "scheduled_at": row.scheduled_at.isoformat(), "participant_dids": row.participant_dids, "status": row.status} for row in rows]


@app.get("/connecting/evidence")
def list_evidence(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(EvidenceRecord).where(EvidenceRecord.user_id == user.id).order_by(EvidenceRecord.created_at.desc()).limit(200)).all()
    return [{"id": row.id, "event_type": row.event_type, "source": row.source, "payload": row.payload, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/connecting/calls", status_code=201)
def initiate_call(payload: CallCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = CallLog(user_id=user.id, recipient_did=payload.recipient_did, media=payload.media)
    db.add(row)
    db.add(EvidenceRecord(user_id=user.id, event_type="call", source=row.id, payload={"recipient_did": payload.recipient_did, "media": payload.media, "status": row.status}))
    db.commit()
    return {"id": row.id, "recipient_did": row.recipient_did, "media": row.media, "status": row.status}


@app.post("/connecting/calls/{call_id}/end")
def end_call(call_id: str, payload: CallEnd, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(CallLog).where(CallLog.id == call_id, CallLog.user_id == user.id))
    if not row:
        raise HTTPException(status_code=404, detail="Call not found")
    row.status = "completed"
    row.duration_seconds = payload.duration_seconds
    db.add(EvidenceRecord(user_id=user.id, event_type="call_end", source=row.id, payload={"duration_seconds": row.duration_seconds, "status": row.status}))
    db.commit()
    return {"id": row.id, "status": row.status, "duration_seconds": row.duration_seconds}


@app.get("/self-improve/status")
def self_improve_status() -> dict:
    """자가진단 스냅샷: 그래프·헌법진화·RLAIF·엔진 리포트."""
    try:
        from core.diagnosis import diagnosis_status
        return diagnosis_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/self-improve/reports")
def self_improve_reports() -> list[dict]:
    """자가진화 엔진 사이클 리포트 (최신순 요약)."""
    try:
        from core.diagnosis import load_reports, summarize_report
        return [summarize_report(r) for r in load_reports(limit=20)]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/ontology/trends")
def ontology_trends(q: str = "", granularity: str = "hour") -> dict:
    """D2: 주제 트렌드 (버킷별 상위 주제 + 특정 주제 추세)."""
    try:
        from core import graph_store, trends
        store = graph_store.load()
        buckets = trends.topic_buckets(store, granularity=granularity or "hour")
        trend = None
        if q:
            trend = trends.topic_trend(store, q, granularity=granularity or "hour")
        return {"buckets": buckets, "trend": trend}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/ontology/search")
def ontology_search(q: str = "") -> dict:
    """D1: 온톨로지 웹 탐색 (라벨 기반 서브그래프 검색)."""
    try:
        from core import graph_store
        store = graph_store.load()
        if not q:
            return {"term": "", "matched": [], "entities": [], "relations": []}
        return graph_store.query(store, [q])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/ontology/rdf")
def ontology_rdf() -> PlainTextResponse:
    """RDF/OWL2 (Turtle) 온톨로지 내보내기."""
    try:
        from core.rdf_export import build_turtle
        return PlainTextResponse(
            build_turtle(),
            media_type="text/turtle",
            headers={"Content-Disposition": "attachment; filename=weaid_ontology.ttl"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.websocket("/ws/ontology")
async def ontology_socket(websocket: WebSocket) -> None:
    """온톨로지 실시간 동기화.

    데스크톱 앱이 누적 그래프(conversation_graph.json)를 갱신할 때마다
    연결된 모든 대시보드 클라이언트에 전체 그래프를 브로드캐스트한다.
    """
    await websocket.accept()
    try:
        from core import graph_store
        last_mtime = -1.0
        while True:
            await asyncio.sleep(0.8)
            path = graph_store.GRAPH_PATH
            try:
                mtime = path.stat().st_mtime if path.exists() else -1.0
            except Exception:
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                try:
                    await websocket.send_json(graph_store.load())
                except Exception:
                    return
    except WebSocketDisconnect:
        return
    except Exception:
        return


@app.post("/auth/signup", response_model=SessionView, status_code=201)
async def signup(payload: UserCreate, db: Session = Depends(get_db)) -> SessionView:
    await limiter.consume(payload.email.lower(), "signup", 5, 3600)
    user = User(
        email=payload.email.lower().strip(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account already exists for this email") from exc
    db.refresh(user)
    return SessionView(access_token=create_access_token(user.id), user=_user_view(user, db))


@app.post("/auth/login", response_model=SessionView)
async def login(payload: LoginRequest, db: Session = Depends(get_db)) -> SessionView:
    await limiter.consume(payload.email.lower(), "login", 10, 300)
    user = db.scalar(select(User).where(User.email == payload.email.lower().strip()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    return SessionView(access_token=create_access_token(user.id), user=_user_view(user, db))


@app.get("/auth/me", response_model=UserView)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserView:
    return _user_view(user, db)


@app.post("/auth/logout", status_code=204)
def logout(_: User = Depends(get_current_user)) -> None:
    return None


@app.post("/me/gemini-key")
async def save_gemini_key(
    payload: GeminiKeyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    await limiter.consume(user.id, "secret", 8, 3600)
    key = normalize_gemini_api_key(payload.api_key)
    if payload.verify_key:
        validation = await asyncio.to_thread(validate_gemini_api_key, key)
        if not validation.valid:
            raise HTTPException(status_code=422, detail=validation.message)
    set_user_secret(db, user.id, "gemini_api_key", key)
    return {"configured": True}


@app.delete("/me/gemini-key", status_code=204)
def remove_gemini_key(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    delete_user_secret(db, user.id, "gemini_api_key")


@app.get("/status")
def get_status(user: User = Depends(get_current_user)) -> dict:
    return live_sessions.status(user.id)


@app.get("/actions")
def get_actions(_: User = Depends(get_current_user)) -> dict:
    from main import get_tool_declarations

    tools = get_tool_declarations(cloud_safe=True)
    return {
        "actions": [
            {"name": item["name"], "description": item.get("description", "")}
            for item in tools
        ]
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    await limiter.consume(user.id, "chat", 30, 60)
    api_key = get_user_secret(db, user.id, "gemini_api_key")
    if not api_key:
        raise HTTPException(status_code=409, detail="Add a Gemini API key before starting JARVIS")

    with tenant_scope(user.id):
        history = recent_chat_messages(user.id, limit=12)
        add_chat_message(user.id, "user", payload.message)
        memory = format_memory_for_prompt(load_memory())

        def generate() -> str:
            from google import genai
            from google.genai import types
            from main import _load_system_prompt

            client = genai.Client(api_key=api_key)
            context = "\n".join(
                f"{message.role.title()}: {message.content}" for message in history
            )
            prompt = (
                f"Recent conversation:\n{context}\n\n"
                f"Current user message: {payload.message}"
            )
            response = client.models.generate_content(
                model=settings.chat_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_load_system_prompt() + "\n\n" + memory,
                ),
            )
            return str(response.text or "").strip()

        response_text = await asyncio.to_thread(generate)
        if not response_text:
            raise HTTPException(status_code=502, detail="Gemini returned an empty response")
        add_chat_message(user.id, "assistant", response_text)
    return ChatResponse(response=response_text)


@app.websocket("/ws")
async def live_socket(websocket: WebSocket) -> None:
    from main import JarvisLive

    with SessionLocal() as db:
        try:
            user = websocket_user(websocket, db)
            await limiter.consume(user.id, "websocket", 12, 60)
            api_key = get_user_secret(db, user.id, "gemini_api_key")
        except HTTPException as exc:
            await websocket.close(code=4401, reason=str(exc.detail))
            return

    await websocket.accept()
    if not api_key:
        await websocket.send_json({
            "type": "error",
            "code": "gemini_key_required",
            "message": "Add a Gemini API key before starting JARVIS.",
        })
        await websocket.close(code=4403)
        return

    client = WebSocketClient(websocket, user.id)
    engine = JarvisLive(
        client,
        cloud_safe=True,
        api_key=api_key,
        external_audio=True,
    )
    sender_task = asyncio.create_task(client.send_events())
    with tenant_scope(user.id):
        engine_task = asyncio.create_task(engine.run())
    await live_sessions.replace(user.id, engine, client, engine_task)
    client._emit({
        "type": "ready",
        "state": "CONNECTING",
        "audio": {"input_rate": 16000, "output_rate": 24000, "encoding": "pcm_s16le"},
    })

    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                await engine.send_audio_chunk(message["bytes"])
                continue
            raw = message.get("text")
            if raw is None:
                continue
            import json

            event = json.loads(raw)
            event_type = event.get("type")
            if event_type == "text":
                await limiter.consume(user.id, "live_text", 60, 60)
                with tenant_scope(user.id):
                    await engine.send_text(str(event.get("content", "")))
            elif event_type == "audio":
                data = base64.b64decode(event.get("data", ""), validate=True)
                await engine.send_audio_chunk(
                    data,
                    str(event.get("mime_type") or "audio/pcm;rate=16000"),
                )
            elif event_type == "mute":
                client.muted = bool(event.get("muted", True))
                client.set_state("MUTED" if client.muted else "LISTENING")
            elif event_type == "ping":
                client._emit({
                    "type": "pong",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            elif event_type == "close":
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)[:240]})
        except Exception:
            pass
    finally:
        engine.request_shutdown()
        engine_task.cancel()
        await asyncio.gather(engine_task, return_exceptions=True)
        await live_sessions.remove(user.id, engine_task)
        await client.close()
        try:
            await sender_task
        except (Exception, asyncio.CancelledError):
            pass
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
