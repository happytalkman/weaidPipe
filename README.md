# WEAID "자기진화형 AI 음성 비서 (SKD 온톨로지 + 헌법적 AI + 자가진화 엔진)"

**WEAID**는 로컬에서 동작하는 AI 음성 비서입니다. 대화를 실시간으로 **SKD 3계층
(시멘틱/키네틱/다이나믹) 온톨로지 지식그래프**로 축적하고, **헌법적 AI
(Constitutional AI)** 원칙으로 스스로를 검열·진화시키며, 주기적으로 **자가진단**을
수행하는 자기개선형 어시스턴트입니다.

- 음성 대화: Gemini Live (성별 음성에 따라 **에이드/AID** · **애순이/AESUNI**)
- 백업 LLM: xAI Grok (Gemini 장애 시 자동 폴백)
- 창조자: HAPPYTALKMAN

---

## 🏗️ 아키텍처

```
┌─────────────────────────────── 데스크톱 앱 (PyQt6) ───────────────────────────────┐
│  UI (ui.py)         HUD 신경망 코어 · S-P-O 우주문자 조립 애니메이션               │
│                     Ctrl+K 명령 팔레트 · 헌법 검사 패널 · 글래스모피즘 스타일      │
│  JarvisLive(main.py) Gemini Live 세션 · STOP/WAKE · 프로액티브 엔진               │
│  ── 응답 파이프라인 ────────────────────────────────────────────────────────────  │
│  답변 → 무형문자 정화 → 원콜 인사이트(Gemini→Grok) → 누적 그래프 병합              │
│        → 헌법 자기비평+수정 → RLAIF 기록 → 요약 노드(10턴) → 헌법 진화(8건)       │
└──────────────────────────────────────────────────────────────────────────────────┘
        │                                   │
        ▼                                   ▼
  core/graph_store.py              web/mindmap.html (D3.js)
  memory/conversation_graph.json   → scripts/mindmap_viewer.py (별도 프로세스 뷰어)
        │
        ▼
  FastAPI (api/server.py)  ──── WS /ws/ontology (실시간 브로드캐스트)
        │                        REST /ontology/graph · /ontology/rdf · /self-improve/*
        ▼
  Next.js 웹 대시보드 (localhost:3000/ontology · /self-diagnosis)
```

## 📦 모듈 지도

| 모듈 | 역할 |
|---|---|
| `main.py` | 라이브 세션, STOP/WAKE, 인과추적, 예약명령, 프로액티브 통합 |
| `ui.py` | 메인 윈도우, HUD, 명령 팔레트, 헌법 패널, S-P-O 조립 애니메이션 |
| `core/graph_store.py` | 누적 대화 그래프(턴·주제·트리플·엔티티·관계·요약), 세션 저장/복원 |
| `core/insight.py` | 원콜 인사이트: S-P-O/SKD 추출 + 헌법 검토 동시 수행 |
| `core/llm.py` | Gemini → Grok 자동 폴백 통합 LLM 클라이언트 |
| `core/constitution.py` | 8원칙 헌법, STOP/재개/그래프탐색/인과질의 파서 |
| `core/constitution_evolver.py` | 헌법 진화 루프 (RLAIF 분석 → 보완 조항 자동 반영) |
| `core/summarizer.py` | 10턴 주기 대화 요약 노드 생성 |
| `core/proactive.py` | 프로액티브 어시스턴트 (예약 작업 자동 실행) |
| `core/scheduler.py` | 스케줄드 태스크 (매일/매주 반복 예약) |
| `core/self_improve.py` | 자가진화 엔진 (6종 건강검사 + AI 패치 + 감시 모드) |
| `core/wake.py` | 박수 명령 감지 (1번=멈춤, 2번=깨움) |
| `core/text_cleaner.py` | 무형 유니코드/워터마크 문자 정화 (자기 콘텐츠 한정) |
| `core/rdf_export.py` | RDF/OWL2(Turtle) 온톨로지 내보내기 |
| `core/diagnosis.py` | 자가진단 스냅샷 (그래프·헌법·RLAIF·엔진) |
| `scripts/mindmap_viewer.py` | D3.js 뷰어 (별도 프로세스 — 메인 앱 크래시 방지) |
| `web/mindmap.html` | 마인드맵/온톨로지 렌더러 (SKD 3기둥, 미니맵, 인스펙터) |
| `web/src/app/ontology/` | 웹 온톨로지 대시보드 (WS 실시간 동기화) |
| `web/src/app/self-diagnosis/` | 웹 자가진단 대시보드 |
| `tests/` | 단위 테스트 (스케줄러·프로액티브·진단) |

## ⚖️ 헌법 (Constitutional AI — 8원칙)

도움 · 무해 · 차별금지 · 정직 · 과잉거부방지 · 창조자 존경 · **정지 복종(헌법 1조)** · 간결

- **자기비평**: 모든 답변을 헌법 기준으로 스스로 채점(0~100)하고 위반 시 수정 답변 생성
- **RLAIF**: 원본 vs 수정 쌍 + 선택 결과를 JSONL로 기록 (재학습 없음)
- **헌법 진화**: 위반 패턴 분석 → 보완 조항 자동 제안·반영 (최대 10개, 이력 영구 기록)
- **헌법 1조**: "멈춰/스톱/그만" 또는 **박수 1번 = 즉시 정지**, "말해" 또는 **박수 2번 = 재개**

## 🧠 온톨로지 지식그래프 (Palantir 스타일)

- **SKD 3계층**: 시멘틱(무엇인가) / 키네틱(무엇을 하는가) / 다이나믹(어떻게 변하는가)
- **누적 그래프**: 대화 턴마다 S-P-O 트리플·엔티티·인과관계가 영구 축적
- **대화 흐름 마인드맵**: Q→A 타임라인 + 추출 주제 태그
- **인과 추적**: "왜 OO?" → 그래프의 원인 체인 역추적 → 음성 설명 + 경로 하이라이트
- **그래프 탐색**: "그래프에서 OO 보여줘" → 1홉 서브그래프 포커스
- **요약 노드**: 10턴마다 자동 압축 (LLM 요약 + 핵심 주제/인사이트)
- **RDF/OWL2**: 표준 시맨틱 웹 포맷(Turtle) 내보내기 (Protege/GraphDB 호환)

## ⌨️ 명령 체계

| 입력 | 동작 |
|---|---|
| `Ctrl+K` | 명령 팔레트 (12개 기능 검색 실행) |
| `Ctrl+Shift+M` / `Ctrl+Shift+G` | 마인드맵 / 온톨로지 뷰어 |
| `Ctrl+W` | 수동 웨이크업 |
| "멈춰/스톱/그만" · 👏×1 | 즉시 정지 (헌법 1조) |
| "말해" · 👏👏 | 재개/깨움 |
| "새 대화" | 세션 초기화 (이전 그래프 아카이브) |
| "그래프에서 OO 보여줘" | 지식그래프 탐색 |
| "왜 OO?" | 인과 체인 추적 설명 |
| "매일 9시에 OO해줘" | 반복 예약 등록 |
| "예약 목록" / "예약 취소 [ID]" | 예약 관리 |

## 🛠️ 설치 & 실행

```bash
# 요구: Python 3.11+
git clone https://github.com/MAL19INDUSTRIES/JARVIS-OS-V.2.git
cd JARVIS-OS-V.2
python -m venv .venv
# Windows: .venv\Scripts\activate / macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env   # GEMINI_API_KEY(필수), XAI_API_KEY(선택: 폴백) 입력
jarvis
```

> ⚠️ Windows에서 venv 경로가 너무 길면(260자 초과) `C:\사용자\이름\jv-venv` 처럼
> 짧은 경로에 venv를 만들어 사용하세요. 한국어 Windows 콘솔에서는
> `PYTHONIOENCODING=utf-8` 설정을 권장합니다.

### 웹 대시보드 (선택)

```bash
# API (Postgres/Redis 불필요 — SQLite 로컬 모드)
DATABASE_URL="sqlite:///./jarvis.db" AUTO_CREATE_TABLES=1 REDIS_REQUIRED=0 \
  uvicorn api.server:app --port 8000

# 웹 (Node.js 20+)
cd web && npm install && npm run dev
# → http://localhost:3000/ontology       (온톨로지 실시간)
# → http://localhost:3000/self-diagnosis (자가진단)
```

## 🔬 테스트

```bash
python -m unittest tests.test_scheduler tests.test_proactive tests.test_diagnosis -v
```

## 🔒 개인정보 보호

- 대화 그래프(`memory/conversation_graph.json`), RLAIF 기록, 세션 아카이브,
  `.env`, 키체인 정보는 **git에서 제외**되며 로컬에만 저장됩니다.
- 무형문자/워터마크 정화는 **자신이 생성·소유한 콘텐츠에만** 적용합니다
  (타인 콘텐츠의 워터마크 제거는 헌법 원칙과 충돌).

## 📄 라이선스

MIT
