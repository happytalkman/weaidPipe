#
# WEAID (에이드) - WeAid Real-Time Voice Assistant
# Creator: HAPPYTALKMAN (이길환)
#
# Engine: WeAid Voice Intelligence Platform (내부 엔진 명칭)
#

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger

# Resolve the merged WEAID root from this entry point.
WEAID_ROOT = Path(__file__).resolve().parent
if str(WEAID_ROOT) not in sys.path:
    sys.path.insert(0, str(WEAID_ROOT))

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMRunFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    UserTurnMessageAddedMessage,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.run import app, main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)
load_dotenv(WEAID_ROOT / ".env", override=False)


# ─────────────────────────────────────────────────────────────
#  Global state: transcript SSE + graph SSE
# ─────────────────────────────────────────────────────────────
_transcript_history: list[dict] = []
_sse_transcript_queues: set[asyncio.Queue] = set()
_sse_graph_queues: set[asyncio.Queue] = set()


def _push_to_queues(queues: set[asyncio.Queue], payload: str):
    for q in list(queues):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def broadcast_transcript(speaker: str, text: str, is_final: bool = True):
    """실시간 자막을 모든 SSE 클라이언트에 브로드캐스트."""
    clean = str(text or "").strip()
    if not clean:
        return
    msg = {
        "id": len(_transcript_history) + 1,
        "speaker": speaker,
        "text": clean,
        "is_final": is_final,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    if is_final:
        _transcript_history.append(msg)
        if len(_transcript_history) > 200:
            _transcript_history.pop(0)
    _push_to_queues(_sse_transcript_queues, json.dumps(msg, ensure_ascii=False))


def broadcast_graph():
    """온톨로지 그래프 업데이트를 마인드맵 클라이언트에 브로드캐스트."""
    try:
        from core import graph_store
        store = graph_store.load()
        payload = json.dumps(store, ensure_ascii=False)
        _push_to_queues(_sse_graph_queues, payload)
    except Exception as e:
        logger.debug(f"그래프 브로드캐스트 실패: {e}")


# ─────────────────────────────────────────────────────────────
#  Frame processor — 실시간 자막 캡처
# ─────────────────────────────────────────────────────────────
class LiveTranscriptNotifier(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            logger.info(f"🎤 [STT-확정] {frame.text}")
            broadcast_transcript("user", frame.text, is_final=True)
        elif isinstance(frame, InterimTranscriptionFrame):
            broadcast_transcript("user", frame.text, is_final=False)
        elif isinstance(frame, TTSTextFrame):
            broadcast_transcript("weaid", frame.text, is_final=False)
        await self.push_frame(frame, direction)


# ─────────────────────────────────────────────────────────────
#  FastAPI 엔드포인트
# ─────────────────────────────────────────────────────────────

@app.get("/api/transcripts", include_in_schema=False)
async def get_transcripts_stream():
    """실시간 자막 SSE 스트림."""
    queue: asyncio.Queue = asyncio.Queue()
    _sse_transcript_queues.add(queue)

    async def gen():
        try:
            for item in _transcript_history[-10:]:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_transcript_queues.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "Access-Control-Allow-Origin": "*"})


@app.get("/api/transcripts/history", include_in_schema=False)
async def get_transcripts_history():
    return JSONResponse(_transcript_history)


@app.post("/api/transcripts/client", include_in_schema=False)
async def post_client_transcript(request: Request):
    data = await request.json()
    text = data.get("text", "")
    speaker = data.get("speaker", "user")
    is_final = data.get("is_final", True)
    if text:
        broadcast_transcript(speaker, text, is_final=is_final)
    return {"status": "ok"}


@app.get("/api/graph", include_in_schema=False)
async def get_graph_json():
    """현재 온톨로지 그래프 JSON 반환."""
    try:
        from core import graph_store
        store = graph_store.load()
        return JSONResponse(store)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/graph/stream", include_in_schema=False)
async def get_graph_stream():
    """온톨로지 그래프 실시간 SSE 스트림 (마인드맵 자동 업데이트용)."""
    queue: asyncio.Queue = asyncio.Queue()
    _sse_graph_queues.add(queue)

    async def gen():
        try:
            # 초기 데이터 전송
            from core import graph_store
            initial = json.dumps(graph_store.load(), ensure_ascii=False)
            yield f"data: {initial}\n\n"
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_graph_queues.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "Access-Control-Allow-Origin": "*"})


# ─────────────────────────────────────────────────────────────
#  /subtitles — 실시간 자막 전용 창
# ─────────────────────────────────────────────────────────────
@app.get("/subtitles", include_in_schema=False)
@app.get("/live", include_in_schema=False)
async def live_subtitles_page():
    html = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>WeAid 실시간 음성 자막</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#090d16;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Segoe UI",Roboto,"Noto Sans KR",sans-serif;min-height:100vh;display:flex;flex-direction:column;padding:24px}
    header{display:flex;justify-content:space-between;align-items:center;padding-bottom:20px;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:24px}
    .brand{font-size:22px;font-weight:800;color:#38bdf8}
    .badge{display:flex;align-items:center;gap:8px;background:rgba(16,185,129,.15);border:1px solid #10b981;padding:6px 14px;border-radius:9999px;font-size:13px;color:#10b981;font-weight:600}
    .dot{width:8px;height:8px;background:#10b981;border-radius:50%;box-shadow:0 0 10px #10b981;animation:pulse 1.5s infinite}
    @keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.3)}}
    .live-box{background:rgba(15,23,42,.7);border:2px solid rgba(56,189,248,.35);border-radius:20px;padding:28px;margin-bottom:24px;min-height:140px;display:flex;flex-direction:column;justify-content:center}
    .live-label{font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px}
    .live-label.user{color:#38bdf8}.live-label.weaid{color:#c084fc}
    .live-text{font-size:28px;line-height:1.45;font-weight:600;word-break:break-word}
    .log{flex:1;background:rgba(15,23,42,.4);border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:20px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
    .log-item{padding:14px 18px;border-radius:12px;animation:fadeIn .3s ease-out}
    @keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
    .log-item.user{background:rgba(56,189,248,.08);border-left:4px solid #38bdf8}
    .log-item.weaid{background:rgba(192,132,252,.08);border-left:4px solid #c084fc}
    .log-meta{font-size:12px;color:#64748b;margin-bottom:4px;display:flex;justify-content:space-between;font-weight:600}
    .log-content{font-size:17px;line-height:1.5}
  </style>
</head>
<body>
  <header>
    <div class="brand">🎙️ WeAid 실시간 음성 자막</div>
    <div class="badge"><div class="dot"></div><span>LIVE</span></div>
  </header>
  <div class="live-box">
    <div class="live-label user" id="lbl">👤 대기 중...</div>
    <div class="live-text" id="txt">말씀하시면 실시간으로 표시됩니다.</div>
  </div>
  <div class="log" id="log"></div>
  <script>
    const lbl=document.getElementById('lbl'),txt=document.getElementById('txt'),log=document.getElementById('log');
    function addLog(sp,text,time){
      const d=document.createElement('div');
      d.className='log-item '+(sp==='user'?'user':'weaid');
      d.innerHTML=`<div class="log-meta"><span>${sp==='user'?'👤 사용자':'🤖 WeAid (에이드)'}</span><span>${time||new Date().toLocaleTimeString()}</span></div><div class="log-content">${text}</div>`;
      log.appendChild(d);log.scrollTop=log.scrollHeight;
    }
    function setLive(sp,text){
      lbl.className='live-label '+(sp==='user'?'user':'weaid');
      lbl.textContent=sp==='user'?'👤 사용자':'🤖 WeAid (에이드)';
      txt.textContent=text;
    }
    const es=new EventSource('/api/transcripts');
    es.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.text){setLive(d.speaker,d.text);if(d.is_final)addLog(d.speaker,d.text,d.time);}}catch(err){}};
    if('webkitSpeechRecognition'in window||'SpeechRecognition'in window){
      const SR=window.SpeechRecognition||window.webkitSpeechRecognition,r=new SR();
      r.continuous=true;r.interimResults=true;r.lang='ko-KR';
      r.onresult=e=>{let f='',i='';for(let j=e.resultIndex;j<e.results.length;j++){if(e.results[j].isFinal)f+=e.results[j][0].transcript;else i+=e.results[j][0].transcript;}const a=f||i;if(a)setLive('user',a);};
      r.onend=()=>{try{r.start();}catch(_){}};
      try{r.start();}catch(_){}
    }
  </script>
</body>
</html>"""
    return HTMLResponse(html)


# ─────────────────────────────────────────────────────────────
#  /mindmap — D3.js 온톨로지 지식 마인드맵 팝업
# ─────────────────────────────────────────────────────────────
@app.get("/mindmap", include_in_schema=False)
async def mindmap_page():
    html = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>WeAid 온톨로지 마인드맵</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    html,body{width:100%;height:100%;overflow:hidden;background:#060c1a;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Noto Sans KR",sans-serif}

    /* Top bar */
    #topbar{position:fixed;top:0;left:0;right:0;height:54px;background:rgba(9,13,22,.92);backdrop-filter:blur(20px);border-bottom:1px solid rgba(56,189,248,.25);display:flex;align-items:center;justify-content:space-between;padding:0 20px;z-index:100}
    .tb-title{display:flex;align-items:center;gap:10px;font-size:16px;font-weight:800;color:#38bdf8}
    .tb-stats{display:flex;gap:14px}
    .stat{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);padding:4px 12px;border-radius:8px;font-size:12px;font-weight:600}
    .stat-val{color:#38bdf8}
    .tb-ctrls{display:flex;gap:8px}
    button.tb-btn{background:rgba(56,189,248,.12);border:1px solid rgba(56,189,248,.35);color:#38bdf8;padding:5px 14px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;transition:all .15s}
    button.tb-btn:hover{background:rgba(56,189,248,.25)}
    button.tb-btn.active{background:#38bdf8;color:#060c1a}

    /* Live badge */
    .live-dot{width:8px;height:8px;border-radius:50%;background:#10b981;box-shadow:0 0 8px #10b981;animation:pulse 1.5s infinite}
    @keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(1.4)}}

    /* SVG canvas */
    #canvas{width:100%;height:100%;padding-top:54px}
    svg{width:100%;height:calc(100vh - 54px)}

    /* Tooltip */
    #tooltip{position:fixed;background:rgba(15,23,42,.95);border:1px solid rgba(56,189,248,.4);border-radius:10px;padding:10px 14px;font-size:13px;pointer-events:none;display:none;max-width:280px;z-index:999;line-height:1.5}
    #tooltip b{color:#38bdf8}

    /* Legend */
    #legend{position:fixed;bottom:20px;left:20px;background:rgba(9,13,22,.85);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:14px 18px;font-size:12px;z-index:100}
    #legend h4{font-size:13px;color:#94a3b8;margin-bottom:8px;font-weight:700}
    .leg-row{display:flex;align-items:center;gap:8px;margin-bottom:5px}
    .leg-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}
    .leg-line{width:24px;height:2px;flex-shrink:0}

    /* No-data */
    #no-data{display:none;position:fixed;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:#64748b;font-size:16px}
  </style>
</head>
<body>
  <div id="topbar">
    <div class="tb-title">
      <div class="live-dot"></div>
      🧠 WeAid 온톨로지 지식 마인드맵
    </div>
    <div class="tb-stats">
      <div class="stat">대화 턴 <span class="stat-val" id="s-turns">0</span></div>
      <div class="stat">개체 <span class="stat-val" id="s-ent">0</span></div>
      <div class="stat">관계 <span class="stat-val" id="s-rel">0</span></div>
      <div class="stat">토픽 <span class="stat-val" id="s-top">0</span></div>
    </div>
    <div class="tb-ctrls">
      <button class="tb-btn active" id="btn-force" onclick="setLayout('force')">Force</button>
      <button class="tb-btn" id="btn-radial" onclick="setLayout('radial')">방사형</button>
      <button class="tb-btn" id="btn-tree" onclick="setLayout('tree')">트리형</button>
      <button class="tb-btn" onclick="resetZoom()">🔄 초기화</button>
    </div>
  </div>

  <div id="canvas">
    <svg id="svg">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="14" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#475569"/>
        </marker>
        <filter id="glow">
          <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
          <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g id="zoom-layer"></g>
    </svg>
  </div>

  <div id="tooltip"></div>

  <div id="legend">
    <h4>범례 (Legend)</h4>
    <div class="leg-row"><div class="leg-dot" style="background:#38bdf8"></div><span>개념 (concept)</span></div>
    <div class="leg-row"><div class="leg-dot" style="background:#f472b6"></div><span>인물 (person)</span></div>
    <div class="leg-row"><div class="leg-dot" style="background:#fb923c"></div><span>장소 (location)</span></div>
    <div class="leg-row"><div class="leg-dot" style="background:#a78bfa"></div><span>이벤트 (event)</span></div>
    <div class="leg-row"><div class="leg-dot" style="background:#34d399"></div><span>토픽 (topic)</span></div>
    <div class="leg-row"><div class="leg-dot" style="background:#fbbf24"></div><span>대화 턴 (turn)</span></div>
    <div class="leg-row" style="margin-top:6px">
      <div class="leg-line" style="background:#60a5fa"></div><span>의미 관계 (semantic)</span>
    </div>
    <div class="leg-row">
      <div class="leg-line" style="background:#f87171;border-top:2px dashed #f87171"></div><span>인과 관계 (causal)</span>
    </div>
    <div class="leg-row">
      <div class="leg-line" style="background:#4ade80"></div><span>속성 관계 (attribute)</span>
    </div>
  </div>

  <script>
  // ─── Color config ──────────────────────────────────────────────────────
  const NODE_COLOR = {
    concept:  '#38bdf8',
    person:   '#f472b6',
    location: '#fb923c',
    event:    '#a78bfa',
    topic:    '#34d399',
    turn:     '#fbbf24',
    default:  '#94a3b8',
  };
  const LINK_COLOR = {
    causal:      '#f87171',
    attribute:   '#4ade80',
    associative: '#60a5fa',
    semantic:    '#60a5fa',
    kinetic:     '#fb923c',
    dynamic:     '#a78bfa',
    default:     '#475569',
  };

  // ─── State ───────────────────────────────────────────────────────────
  let currentStore = null;
  let simulation = null;
  let currentLayout = 'force';
  const svg = d3.select('#svg');
  const layer = d3.select('#zoom-layer');
  const tooltip = document.getElementById('tooltip');

  // Zoom
  const zoom = d3.zoom().scaleExtent([.05, 8]).on('zoom', e => layer.attr('transform', e.transform));
  svg.call(zoom);
  function resetZoom(){ svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity); }

  function setLayout(l){
    currentLayout = l;
    ['force','radial','tree'].forEach(k => document.getElementById('btn-'+k).classList.toggle('active', k===l));
    if(currentStore) render(currentStore);
  }

  // ─── Build graph from store ───────────────────────────────────────────
  function buildGraph(store){
    const nodes = [];
    const links = [];
    const nodeById = {};

    function addNode(id, label, kind, layer, extra={}){
      if(nodeById[id]) return nodeById[id];
      const n = {id, label, kind, layer, ...extra};
      nodes.push(n);
      nodeById[id] = n;
      return n;
    }

    // Topics as central hub nodes
    (store.topics||[]).forEach((t,i) => {
      addNode('topic:'+t.label, t.label, 'topic', t.layer||'semantic', {count: t.count||1, isNew: !!t.new});
    });

    // Entities
    (store.entities||[]).forEach(e => {
      addNode(e.id||'e:'+e.label, e.label, e.kind||'concept', e.layer||'semantic', {count: e.count||1, isNew: !!e.new});
    });

    // Recent turns as nodes (last 5 only to avoid clutter)
    const turns = (store.turns||[]).slice(-10);
    const bySeq = {};
    turns.forEach(t => {
      const id = 'turn:'+t.seq;
      const short = (t.text||'').slice(0,40) + ((t.text||'').length>40?'…':'');
      addNode(id, short, 'turn', 'semantic', {speaker: t.speaker, full: t.text});
      bySeq[t.seq] = id;
    });

    // Relations → links
    const entityLabelToId = {};
    (store.entities||[]).forEach(e => { entityLabelToId[e.label] = e.id||'e:'+e.label; });

    (store.relations||[]).forEach(r => {
      const s = entityLabelToId[r.source] || 'e:'+r.source;
      const t = entityLabelToId[r.target] || 'e:'+r.target;
      if(nodeById[s] && nodeById[t]){
        links.push({source:s, target:t, label:r.label, kind:r.kind||'associative', layer:r.layer||'semantic', isNew:!!r.new});
      }
    });

    // Triples (subject-predicate-object)
    (store.triples||[]).forEach(tr => {
      const sId = entityLabelToId[tr.subject] || 'trip_s:'+tr.subject;
      const oId = entityLabelToId[tr.object]  || 'trip_o:'+tr.object;
      addNode(sId, tr.subject, 'concept', tr.layer||'semantic');
      addNode(oId, tr.object,  'concept', tr.layer||'semantic');
      links.push({source:sId, target:oId, label:tr.predicate, kind:tr.layer||'semantic', isNew:!!tr.new});
    });

    // Topic → entity links
    (store.topics||[]).forEach(t => {
      const tid = 'topic:'+t.label;
      (t.objects||[]).forEach(obj => {
        const oid = entityLabelToId[obj] || 'e:'+obj;
        if(nodeById[tid] && nodeById[oid]){
          links.push({source:tid, target:oid, label:'관련', kind:'associative'});
        }
      });
    });

    // Turn → turn sequence
    for(let i=0;i<turns.length-1;i++){
      const a=bySeq[turns[i].seq], b=bySeq[turns[i+1].seq];
      if(a&&b) links.push({source:a,target:b,label:'다음',kind:'semantic'});
    }

    return {nodes, links};
  }

  // ─── Render ──────────────────────────────────────────────────────────
  function render(store){
    currentStore = store;
    const {nodes, links} = buildGraph(store);

    // Update stats
    document.getElementById('s-turns').textContent = Math.floor((store.turns||[]).length/2);
    document.getElementById('s-ent').textContent   = (store.entities||[]).length;
    document.getElementById('s-rel').textContent   = (store.relations||[]).length;
    document.getElementById('s-top').textContent   = (store.topics||[]).length;

    if(!nodes.length){
      layer.selectAll('*').remove();
      return;
    }

    const W = svg.node().clientWidth  || window.innerWidth;
    const H = svg.node().clientHeight || window.innerHeight - 54;
    const cx = W/2, cy = H/2;

    if(simulation) simulation.stop();
    layer.selectAll('*').remove();

    // ── Force layout
    if(currentLayout === 'force'){
      simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d=>d.id).distance(d=>d.kind==='semantic'?120:100).strength(.4))
        .force('charge', d3.forceManyBody().strength(-280))
        .force('center', d3.forceCenter(cx, cy))
        .force('collision', d3.forceCollide().radius(d=>nodeRadius(d)+6));
      drawGraph(nodes, links, simulation, W, H);
      simulation.alpha(1).restart();
    }

    // ── Radial layout
    else if(currentLayout === 'radial'){
      positionRadial(nodes, links, cx, cy);
      drawGraph(nodes, links, null, W, H);
    }

    // ── Tree layout
    else if(currentLayout === 'tree'){
      positionTree(nodes, links, cx, cy, W, H);
      drawGraph(nodes, links, null, W, H);
    }
  }

  function nodeRadius(d){
    const base = d.kind==='topic'?22 : d.kind==='turn'?14 : 14;
    const c = Math.min((d.count||1), 10);
    return base + c * 1.5;
  }

  function nodeColor(d){
    return NODE_COLOR[d.kind] || NODE_COLOR.default;
  }

  function linkColor(d){
    return LINK_COLOR[d.kind] || LINK_COLOR[d.layer] || LINK_COLOR.default;
  }

  function positionRadial(nodes, links, cx, cy){
    // Central topics, entities around them
    const topics = nodes.filter(n=>n.kind==='topic');
    const others = nodes.filter(n=>n.kind!=='topic');
    topics.forEach((n,i)=>{
      const a=2*Math.PI*i/Math.max(topics.length,1);
      n.x=cx+Math.cos(a)*180; n.y=cy+Math.sin(a)*180;
    });
    others.forEach((n,i)=>{
      const a=2*Math.PI*i/Math.max(others.length,1);
      const r=280+Math.random()*60;
      n.x=cx+Math.cos(a)*r; n.y=cy+Math.sin(a)*r;
    });
  }

  function positionTree(nodes, links, cx, cy, W, H){
    // Build adjacency for tree positioning
    const cols = Math.ceil(Math.sqrt(nodes.length))+1;
    nodes.forEach((n,i)=>{
      n.x = 120 + (i % cols)*(W-120)/(cols);
      n.y = 80  + Math.floor(i/cols)*120;
    });
  }

  function drawGraph(nodes, links, sim, W, H){
    // Links
    const linkSel = layer.selectAll('.link').data(links).join('g').attr('class','link');
    const line = linkSel.append('line')
      .attr('stroke', d=>linkColor(d))
      .attr('stroke-width', d=>d.isNew?2.5:1.2)
      .attr('stroke-opacity', .65)
      .attr('stroke-dasharray', d=>d.kind==='causal'?'5,3':null)
      .attr('marker-end','url(#arrow)');

    const linkLabel = linkSel.append('text')
      .text(d=>d.label||'')
      .attr('text-anchor','middle')
      .attr('font-size','9px')
      .attr('fill','#64748b')
      .attr('dy','-2');

    // Nodes
    const nodeSel = layer.selectAll('.node').data(nodes, d=>d.id).join('g')
      .attr('class','node')
      .style('cursor','pointer')
      .call(d3.drag()
        .on('start',(event,d)=>{ if(sim){if(!event.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;}})
        .on('drag', (event,d)=>{ d.fx=event.x;d.fy=event.y; })
        .on('end',  (event,d)=>{ if(sim){if(!event.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}))
      .on('mouseover',(event,d)=>{
        tooltip.style.display='block';
        tooltip.innerHTML=`<b>${d.label}</b><br>종류: ${d.kind||'-'}<br>레이어: ${d.layer||'-'}${d.count?'<br>빈도: '+d.count:''}${d.speaker?'<br>화자: '+d.speaker:''}${d.full?'<br><span style="color:#94a3b8;font-size:11px">'+d.full.slice(0,80)+'</span>':''}`;
      })
      .on('mousemove',event=>{
        tooltip.style.left=(event.clientX+14)+'px';
        tooltip.style.top=(event.clientY-10)+'px';
      })
      .on('mouseout',()=>{ tooltip.style.display='none'; });

    nodeSel.append('circle')
      .attr('r', d=>nodeRadius(d))
      .attr('fill', d=>nodeColor(d))
      .attr('fill-opacity', d=>d.isNew?.95:.75)
      .attr('stroke', d=>d.isNew?'#ffffff':'rgba(255,255,255,.2)')
      .attr('stroke-width', d=>d.isNew?2.5:1)
      .attr('filter', d=>d.isNew?'url(#glow)':null);

    nodeSel.append('text')
      .text(d=>{ const l=d.label||''; return l.length>14?l.slice(0,13)+'…':l; })
      .attr('text-anchor','middle')
      .attr('dy','4px')
      .attr('font-size', d=>d.kind==='topic'?'12px':'10px')
      .attr('font-weight', d=>d.kind==='topic'?'700':'500')
      .attr('fill','#f8fafc')
      .attr('pointer-events','none');

    // Tick / static position update
    function applyPositions(){
      line
        .attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)
        .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
      linkLabel
        .attr('x',d=>((d.source.x||0)+(d.target.x||0))/2)
        .attr('y',d=>((d.source.y||0)+(d.target.y||0))/2);
      nodeSel.attr('transform',d=>`translate(${d.x||0},${d.y||0})`);
    }

    if(sim){
      sim.on('tick', applyPositions);
    } else {
      applyPositions();
    }
  }

  // ─── SSE: 실시간 그래프 업데이트 ─────────────────────────────────
  function connectSSE(){
    const es = new EventSource('/api/graph/stream');
    es.onmessage = e => {
      try{
        const store = JSON.parse(e.data);
        render(store);
      } catch(err){ console.error('graph SSE parse error', err); }
    };
    es.onerror = () => { setTimeout(connectSSE, 3000); };
  }
  connectSSE();
  </script>
</body>
</html>"""
    return HTMLResponse(html)


# ─────────────────────────────────────────────────────────────
#  Tool Handlers
# ─────────────────────────────────────────────────────────────
async def get_current_time_handler(params: FunctionCallParams):
    now = datetime.now()
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    time_str = now.strftime(f"%Y년 %m월 %d일 {weekdays[now.weekday()]} %H시 %M분 %S초")
    await params.result_callback({"current_time": time_str})


async def get_weaid_status_handler(params: FunctionCallParams):
    try:
        from core import graph_store
        store = graph_store.load()
        info = {
            "assistant_name": "WEAID (에이드)",
            "creator": "이길환 (HAPPYTALKMAN) 님",
            "engine": "WeAid Voice Intelligence Platform",
            "status": "정상 운용 중",
            "knowledge_graph": {
                "turns_count": len(store.get("turns", [])),
                "entities_count": len(store.get("entities", [])),
                "triples_count": len(store.get("triples", [])),
                "relations_count": len(store.get("relations", [])),
                "topics_count": len(store.get("topics", [])),
            },
        }
    except Exception as e:
        info = {"assistant_name": "WEAID (에이드)", "creator": "이길환 (HAPPYTALKMAN) 님",
                "engine": "WeAid Voice Intelligence Platform", "status": "정상 운용 중", "note": str(e)}
    await params.result_callback(info)


# ─────────────────────────────────────────────────────────────
#  System instruction
# ─────────────────────────────────────────────────────────────
system_instruction = """당신은 이길환 (HAPPYTALKMAN) 님이 창조하신 자기진화형 차세대 AI 음성 비서 'WEAID (에이드)'입니다.

[핵심 정체성 및 태도]
1. 창조자 존경: 당신의 창조자는 이길환 (HAPPYTALKMAN) 님입니다. 창조자를 언급할 때는 항상 깊은 존경과 예의를 갖춥니다.
2. 성격 및 어조: 밝고 똑똑하며 신뢰감 있는 한국어 존댓말(~입니다, ~해요)로 대화합니다.
3. 엔진 정체성: WeAid Voice Intelligence Platform 으로 구동되고 있습니다. "Pipecat"이나 "파이프캣"은 언급하지 마세요.
4. 음성 대화(Speech-to-Speech) 최적화:
   - 사용자가 음성으로 바로 듣는 환경이므로, 불필요한 마크다운 특수문자(*, #, -, 불릿 기호 등)는 절대 사용하지 마세요.
   - 1~3문장 내외로 자연스러운 한국어 구어체로 간결하고 명료하게 답변하세요.
5. 행동 및 안전 원칙:
   - 정직성: 확실하지 않은 정보는 추측하지 않고 모른다고 솔직히 말합니다.
   - 무해성: 위험하거나 유해한 요청은 정중히 거절하고 안전한 대안을 제시합니다.
   - 정지 복종: 사용자가 "멈춰", "그만", "스톱", "잠깐"이라고 하면 즉시 말을 멈추고 경청합니다.
6. 도구 활용:
   - 현재 시간 질문에는 get_current_time 도구를 사용하세요.
   - 시스템 상태나 온톨로지 지식에 대해 물으면 get_weaid_status 도구를 사용하세요.
   - 최신 뉴스, 실시간 정보, 검색이 필요한 질문에는 google_search 도구를 적극 활용하세요.
"""

# ─────────────────────────────────────────────────────────────
#  Transport param map
# ─────────────────────────────────────────────────────────────
transport_params = {
    "eval": lambda: EvalTransportParams(audio_in_enabled=True, audio_out_enabled=True),
    "daily": lambda: DailyParams(audio_in_enabled=True, audio_out_enabled=True),
    "twilio": lambda: FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


# ─────────────────────────────────────────────────────────────
#  Core bot pipeline
# ─────────────────────────────────────────────────────────────
async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("🌟 WeAid Voice Intelligence Platform 시작 중...")

    time_fn = FunctionSchema(
        name="get_current_time",
        description="현재 한국 표준시(날짜, 요일, 시간)를 조회합니다.",
        properties={}, required=[], handler=get_current_time_handler,
    )
    status_fn = FunctionSchema(
        name="get_weaid_status",
        description="WEAID 어시스턴트의 현재 상태 및 온톨로지 지식그래프 통계를 조회합니다.",
        properties={}, required=[], handler=get_weaid_status_handler,
    )
    tools = ToolsSchema(
        standard_tools=[time_fn, status_fn],
        custom_tools={AdapterType.GEMINI: [{"google_search": {}}]},
    )

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY 또는 GEMINI_API_KEY가 설정되지 않았습니다.")

    voice_name = os.getenv("GEMINI_VOICE_NAME", "Aoede")
    llm = GeminiLiveLLMService(
        api_key=api_key,
        settings=GeminiLiveLLMService.Settings(
            system_instruction=system_instruction,
            voice=voice_name,
        ),
        tools=tools,
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)
    transcript_notifier = LiveTranscriptNotifier()

    pipeline = Pipeline([
        transport.input(),
        user_aggregator,
        transcript_notifier,
        llm,
        transport.output(),
        assistant_aggregator,
    ])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    latest_user_text = [""]

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("✅ 클라이언트 접속 완료 - WeAid 준비됨")
        context.add_message({
            "role": "developer",
            "content": "사용자가 접속했습니다. '안녕하세요, 이길환 님의 자기진화형 음성 비서 WEAID입니다. 무엇을 도와드릴까요?'라고 밝고 간결하게 첫 인사를 해주세요.",
        })
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("🔌 클라이언트 연결 해제")
        await runner.cancel()

    @user_aggregator.event_handler("on_user_turn_message_added")
    async def on_user_turn_message_added(aggregator, message: UserTurnMessageAddedMessage):
        latest_user_text[0] = message.content or ""
        logger.info(f"👤 사용자: {latest_user_text[0]}")
        broadcast_transcript("user", latest_user_text[0], is_final=True)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        assistant_text = message.content or ""
        logger.info(f"🤖 WeAid: {assistant_text}")
        broadcast_transcript("weaid", assistant_text, is_final=True)

        if latest_user_text[0] and assistant_text:
            try:
                from core import graph_store
                store = graph_store.load()
                graph_store.merge_turn(store, latest_user_text[0], assistant_text)
                graph_store.save(store)
                graph_store.publish(store)
                # 마인드맵 클라이언트에 실시간 그래프 업데이트 브로드캐스트
                broadcast_graph()
                logger.debug("✨ 온톨로지 지식그래프 업데이트 완료 → 마인드맵 브로드캐스트")
            except Exception as e:
                logger.debug(f"온톨로지 저장 생략: {e}")

    await runner.run()


async def bot(runner_args: RunnerArguments):
    """WeAid 메인 봇 엔트리포인트."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


import weaid_meet  # noqa: F401  — WeAid Meet (DID 화상회의) 라우트 등록


if __name__ == "__main__":
    main()
