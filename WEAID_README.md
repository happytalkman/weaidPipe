# 🎙️ PipeAId - WEAID Real-Time Voice Engine

이 프로젝트는 **Pipecat** 프레임워크를 기반으로 구축된 **WEAID (에이드)** 전용 실시간 음성 및 멀티모달 대화 파이프라인 엔진입니다.

## 🌟 주요 특징 및 구성
1. **창조자 및 페르소나**:
   - 창조자: **이길환 (HAPPYTALKMAN)** 님
   - 어시스턴트: **WEAID (에이드)**
   - 헌법적 AI 원칙 및 한국어 실시간 구어체 최적화
2. **Gemini Live 양방향 음성 스트리밍**:
   - 음성 인식(STT), 두뇌(LLM), 음성 합성(TTS)이 한 세션에서 초저지연(Ultra-low latency)으로 동작
   - 동시 발화(Barge-in/Interruption) 지원: 사용자가 말하면 즉시 멈추고 경청
3. **WEAID 온톨로지 지식그래프 연동**:
   - 대화 턴이 발생할 때마다 `core/graph_store.py`를 통해 `memory/conversation_graph.json`에 누적 기록
4. **내장 도구 (Function Calling)**:
   - `get_current_time`: 현재 한국 표준시 조회
   - `get_weaid_status`: WEAID 어시스턴트 및 온톨로지 지식 통계 조회
   - `google_search`: 최신 정보 실시간 웹 검색

## 🚀 실행 방법
### 1. 웹 브라우저 (WebRTC 대화)
- 스크립트 실행:
  - PowerShell: `.\run.ps1`
  - CMD: `run.bat`
- 브라우저 접속: `http://localhost:7860`
- **Connect** 클릭 후 마이크 권한을 허용하면 바로 음성 대화가 시작됩니다.

### 2. 수동 실행 명령
```powershell
$env:PYTHONUTF8 = "1"
uv run python bot_weaid.py -t webrtc --port 7860
```
