"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Diagnosis = {
  ts: string;
  graph: { turns: number; topics: number; triples: number; entities: number; relations: number; summaries: number };
  constitution: { amendments: string[]; history_count: number; status: string };
  rlaif: { total: number; violation_count: number; avg_score: number; top_patterns: [string, number][] };
  engine: { report_count: number; last_cycle: Cycle | null; recent: Cycle[] };
};
type Cycle = {
  stamp: string;
  apply: boolean;
  issue_count: number;
  critical: number;
  fixed_count: number;
  evolution_applied: number;
  verify_ok: boolean;
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-cyan-400/10 bg-[#0d1526]/60 p-4 backdrop-blur-xl">
      <h2 className="mb-3 text-[11px] font-bold uppercase tracking-widest text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function Stat({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[.03] px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-xl font-bold ${accent || "text-cyan-200"}`}>{value}</div>
    </div>
  );
}

export default function SelfDiagnosisPage() {
  const [status, setStatus] = useState<Diagnosis | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetch(`${API_URL}/self-improve/status`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: Diagnosis) => setStatus(d))
      .catch((e) => setError("자가진단 서버에 연결할 수 없습니다: " + e.message));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <main className="min-h-screen bg-[#04060c] p-6 text-[#eaf0ff]">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-bold tracking-wide text-cyan-300">WEAID · 자가진단 대시보드</h1>
        <span className="text-[11px] text-slate-500">자가진화 엔진 상태 실시간 모니터링</span>
        <button
          onClick={load}
          className="ml-auto rounded-[10px] border border-cyan-400/20 bg-cyan-400/10 px-4 py-1.5 text-[11px] font-semibold text-cyan-200 transition hover:bg-cyan-400/20"
        >
          새로고침
        </button>
      </header>

      {error && <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">{error}</div>}

      {status && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="누적 온톨로지">
            <div className="grid grid-cols-3 gap-2">
              <Stat label="턴" value={status.graph.turns} />
              <Stat label="주제" value={status.graph.topics} />
              <Stat label="요약" value={status.graph.summaries} accent="text-violet-300" />
              <Stat label="트리플" value={status.graph.triples} />
              <Stat label="엔티티" value={status.graph.entities} />
              <Stat label="관계" value={status.graph.relations} accent="text-amber-300" />
            </div>
          </Card>

          <Card title="헌법 진화 루프">
            <div className="mb-2 grid grid-cols-2 gap-2">
              <Stat label="보완 조항" value={status.constitution.amendments.length} accent="text-emerald-300" />
              <Stat label="진화 이력" value={status.constitution.history_count} />
            </div>
            <ul className="space-y-1.5">
              {status.constitution.amendments.slice(-5).map((a, i) => (
                <li key={i} className="rounded-lg border border-emerald-500/10 bg-emerald-500/5 px-3 py-1.5 text-[11px] text-emerald-200">
                  보완 {i + 1}: {a}
                </li>
              ))}
            </ul>
          </Card>

          <Card title="RLAIF (AI 피드백)">
            <div className="grid grid-cols-3 gap-2">
              <Stat label="기록" value={status.rlaif.total} />
              <Stat label="위반" value={status.rlaif.violation_count} accent="text-red-300" />
              <Stat label="평균 점수" value={status.rlaif.avg_score} />
            </div>
            {status.rlaif.top_patterns.length > 0 && (
              <div className="mt-3 text-[11px] text-slate-400">
                위반 패턴: {status.rlaif.top_patterns.map(([k, c]) => `${k}×${c}`).join(" · ")}
              </div>
            )}
          </Card>

          <Card title="자가진화 엔진">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="사이클 리포트" value={status.engine.report_count} />
              <Stat
                label="최근 검증"
                value={status.engine.last_cycle?.verify_ok ? "정상" : "주의"}
                accent={status.engine.last_cycle?.verify_ok ? "text-emerald-300" : "text-red-300"}
              />
            </div>
            <ul className="mt-3 space-y-1.5">
              {status.engine.recent.slice(0, 6).map((c) => (
                <li key={c.stamp} className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[.03] px-3 py-1.5 text-[11px]">
                  <span className="text-slate-400">{c.stamp}</span>
                  <span className="text-slate-300">
                    문제 {c.issue_count}
                    {c.critical > 0 && <span className="text-red-300"> (치명 {c.critical})</span>} · 수정 {c.fixed_count}
                    {c.evolution_applied > 0 && <span className="text-emerald-300"> · 진화 {c.evolution_applied}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </main>
  );
}
