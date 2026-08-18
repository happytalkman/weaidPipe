"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/ontology";
const RDF_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000") + "/ontology/rdf";

type GraphStats = { turns: number; topics: number; entities: number; relations: number };

export default function OntologyPage() {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState("연결 중…");
  const [stats, setStats] = useState<GraphStats | null>(null);

  const post = useCallback((msg: unknown) => {
    const frame = iframeRef.current;
    if (frame?.contentWindow) frame.contentWindow.postMessage(msg, "*");
  }, []);

  useEffect(() => {
    let disposed = false;
    let retry = 0;

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setStatus("실시간 동기화 중");
      ws.onerror = () => setStatus("연결 오류");
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          post({ type: "graph", data });
          setStats({
            turns: Math.floor((data.turns?.length ?? 0) / 2),
            topics: data.topics?.length ?? 0,
            entities: data.entities?.length ?? 0,
            relations: data.relations?.length ?? 0,
          });
        } catch {
          /* 무시 */
        }
      };
      ws.onclose = () => {
        if (disposed) return;
        setStatus(`연결 끊김 — ${3 + retry}s 후 재연결`);
        setTimeout(connect, (3 + retry) * 1000);
        retry = Math.min(retry + 3, 30);
      };
    };

    connect();
    return () => {
      disposed = true;
      wsRef.current?.close();
    };
  }, [post]);

  return (
    <main className="flex h-screen flex-col bg-[#04060c] text-[#eaf0ff]">
      <header className="flex flex-wrap items-center gap-3 border-b border-cyan-400/10 bg-[#0a1224]/55 px-6 py-3 backdrop-blur-xl">
        <h1 className="text-sm font-bold tracking-wide text-cyan-300">WEAID · 온톨로지 대시보드</h1>
        <span className="text-[11px] text-slate-400">창조자 이길환 (HAPPYTALKMAN)</span>
        <span className="ml-2 inline-flex items-center gap-1.5 rounded-full border border-cyan-400/15 bg-cyan-400/5 px-2.5 py-1 text-[11px] text-cyan-200">
          <i className={`h-1.5 w-1.5 rounded-full ${status === "실시간 동기화 중" ? "bg-emerald-400" : "bg-amber-400"}`} />
          {status}
        </span>
        {stats && (
          <span className="text-[11px] text-slate-400">
            턴 {stats.turns} · 주제 {stats.topics} · 엔티티 {stats.entities} · 관계 {stats.relations}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => post({ type: "mode", mode: "mindmap" })}
            className="rounded-[10px] border border-emerald-700/60 bg-emerald-900/25 px-3 py-1.5 text-[11px] font-semibold text-emerald-300 transition hover:bg-emerald-900/45"
          >
            MINDMAP
          </button>
          <button
            onClick={() => post({ type: "mode", mode: "graph" })}
            className="rounded-[10px] border border-emerald-700/60 bg-emerald-900/25 px-3 py-1.5 text-[11px] font-semibold text-emerald-300 transition hover:bg-emerald-900/45"
          >
            ONTOLOGY
          </button>
          <a
            href={RDF_URL}
            className="rounded-[10px] border border-violet-500/40 bg-violet-900/25 px-3 py-1.5 text-[11px] font-semibold text-violet-300 transition hover:bg-violet-900/45"
          >
            RDF/OWL2 ⬇
          </a>
        </div>
      </header>
      <iframe
        ref={iframeRef}
        src="/mindmap-viewer.html"
        title="WEAID Ontology Viewer"
        className="min-h-0 flex-1 border-0 bg-transparent"
      />
    </main>
  );
}
