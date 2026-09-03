<h1>WEAID PipeAId</h1>

WEAID(에이드)는 Pipecat 기반의 한국어 실시간 음성·멀티모달 AI 어시스턴트입니다. Gemini Live와 WebRTC를 연결해 브라우저에서 음성으로 대화하고, 실시간 자막과 온톨로지 그래프 이벤트를 제공합니다.

## 빠른 시작

### 요구 사항

- Windows 또는 macOS/Linux
- Python 3.11 이상
- `uv`
- Gemini API 키

### 환경 설정

프로젝트 루트에 `.env` 파일을 만들고 필요한 API 키를 설정합니다. 키 이름과 서비스 설정은 `env.example`을 참고하세요.

### 실행

PowerShell:

```powershell
.\\run.ps1
```

Windows CMD:

```bat
run.bat
```

수동 실행:

```powershell
$env:PYTHONUTF8 = "1"
uv run python bot_weaid.py -t webrtc --port 7860
```

브라우저에서 [http://localhost:7860](http://localhost:7860)을 열고 마이크 권한을 허용한 뒤 연결합니다.

## 주요 기능

- Gemini Live 기반 양방향 음성 대화와 사용자 발화 중단 처리
- WebRTC 브라우저 transport 및 실시간 음성 스트리밍
- 한국어 존댓말 중심의 WEAID 페르소나
- 실시간 자막 SSE API: `/api/transcripts`, `/api/transcripts/history`
- WEAID 상태와 현재 시간 조회를 위한 function calling
- 회의 보조 기능과 온톨로지 그래프 연동

## 프로젝트 문서

- [제품 요구사항 문서](PRD.md)
- [상세 WEAID 안내](WEAID_README.md)

## 저장소

본 프로젝트의 공식 저장소는 [happytalkman/weaidPipe](https://github.com/happytalkman/weaidPipe)입니다.

---

<h1><div align="center">
 <img alt="pipecat" width="300px" height="auto" src="https://raw.githubusercontent.com/pipecat-ai/pipecat/main/pipecat.png">
</div></h1>

[![PyPI](https://img.shields.io/pypi/v/pipecat-ai)](https://pypi.org/project/pipecat-ai) ![Tests](https://github.com/pipecat-ai/pipecat/actions/workflows/tests.yaml/badge.svg) [![codecov](https://codecov.io/gh/pipecat-ai/pipecat/graph/badge.svg?token=LNVUIVO4Y9)](https://codecov.io/gh/pipecat-ai/pipecat) [![Docs](https://img.shields.io/badge/Documentation-blue)](https://docs.pipecat.ai) [![Discord](https://img.shields.io/discord/1239284677165056021)](https://discord.gg/pipecat) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/pipecat-ai/pipecat)

# 🎙️ Pipecat: Real-Time Voice & Multimodal AI Agents

**Pipecat** is an open-source Python framework for building real-time voice and multimodal conversational agents. Build a single voice agent or a full multi-agent system where specialists hand off, fan out in parallel, and coordinate over a shared bus, locally or distributed across processes and machines. Orchestrate audio and video, AI services, transports, and conversation pipelines effortlessly, so you can focus on what makes your agents unique.

> Want to dive right in? Run `pipecat init quickstart` or follow the [quickstart guide](https://docs.pipecat.ai/getting-started/quickstart).

## 🚀 What you can build

- **Voice Assistants** – natural, streaming conversations with AI
- **Multi-Agent Systems** – specialists that hand off, fan out in parallel, or run as sidecars over a shared bus
- **AI Companions** – coaches, meeting assistants, characters
- **Multimodal Interfaces** – voice, video, images, and more
- **Interactive Storytelling** – creative tools with generative media
- **Business Agents** – customer intake, support bots, guided flows
- **Complex Dialog Systems** – design logic with structured conversations

## 🧠 Why Pipecat?

- **Voice-first**: Integrates speech recognition, text-to-speech, and conversation handling
- **Pluggable**: Supports many AI services and tools
- **Composable Pipelines**: Build complex behavior from modular components
- **Multi-Agent Ready**: Each pipeline is an agent. Compose them with handoff, parallel fan-out, sidecar workers, or distributed deployments
- **Real-Time**: Ultra-low latency interaction with different transports (e.g. WebSockets or WebRTC)

## 🌐 Pipecat ecosystem

### 📱 Client SDKs

Building client applications? You can connect to Pipecat from any platform using our official SDKs:

<a href="https://docs.pipecat.ai/client/js/introduction">JavaScript</a> | <a href="https://docs.pipecat.ai/client/react/introduction">React</a> | <a href="https://docs.pipecat.ai/client/react-native/introduction">React Native</a> |
<a href="https://docs.pipecat.ai/client/ios/introduction">Swift</a> | <a href="https://docs.pipecat.ai/client/android/introduction">Kotlin</a> | <a href="https://docs.pipecat.ai/client/c++/introduction">C++</a> | <a href="https://github.com/pipecat-ai/pipecat-esp32">ESP32</a>

### 🧭 Structured conversations

Need predefined or dynamic conversation paths with state management? [Pipecat Flows](https://docs.pipecat.ai/guides/features/pipecat-flows) is built into Pipecat. Browse the [examples](https://github.com/pipecat-ai/pipecat/tree/main/examples/flows) to see it in action.

### 🪄 Beautiful UIs

Want to build beautiful and engaging experiences? Checkout the [Voice UI Kit](https://github.com/pipecat-ai/voice-ui-kit), a collection of components, hooks and templates for building voice AI applications quickly.

### 🛠️ Create and deploy projects

The [Pipecat CLI](https://docs.pipecat.ai/api-reference/cli/overview) ships with `pipecat-ai` — install it with `uv tool install "pipecat-ai[cli]"`. Run `pipecat init` to start a project: it sets you up so an AI coding assistant (Claude Code, Codex) builds it for you, and can scaffold a runnable bot in under a minute. Then use the CLI to monitor and deploy your agent to production.

### 🔍 Debugging

Looking for help debugging your pipeline and processors? Check out [Whisker](https://github.com/pipecat-ai/whisker), a real-time Pipecat debugger.

### 🖥️ Terminal

Love terminal applications? Check out [Tail](https://github.com/pipecat-ai/tail), a terminal dashboard for Pipecat.

### 🤖 Claude Code skills

Use [Pipecat Skills](https://github.com/pipecat-ai/skills) with [Claude Code](https://claude.ai/code) to scaffold projects, deploy to Pipecat Cloud, and more. Install the marketplace with:

```
claude plugin marketplace add pipecat-ai/skills
```

and install any of the available plugins.

### 🧩 Community integrations

Build and share your own Pipecat service integrations! Browse existing [community integrations](https://docs.pipecat.ai/api-reference/server/services/supported-services) or check out our [guide](COMMUNITY_INTEGRATIONS.md) to create your own.

### 📺️ Pipecat TV channel

Catch new features, interviews, and how-tos on our [Pipecat TV](https://www.youtube.com/playlist?list=PLzU2zoMTQIHjqC3v4q2XVSR3hGSzwKFwH) channel.

## 🎬 See it in action

<p float="left">
    <a href="https://github.com/pipecat-ai/pipecat-examples/tree/main/simple-chatbot"><img src="https://raw.githubusercontent.com/pipecat-ai/pipecat-examples/main/simple-chatbot/image.png" width="400" /></a>&nbsp;
    <a href="https://github.com/pipecat-ai/pipecat-examples/tree/main/storytelling-chatbot"><img src="https://raw.githubusercontent.com/pipecat-ai/pipecat-examples/main/storytelling-chatbot/image.png" width="400" /></a>
    <br/>
    <a href="https://github.com/pipecat-ai/pipecat-examples/tree/main/daily-multi-translation"><img src="https://raw.githubusercontent.com/pipecat-ai/pipecat-examples/main/daily-multi-translation/image.png" width="400" /></a>&nbsp;
    <a href="https://github.com/pipecat-ai/pipecat/blob/main/examples/vision/vision-moondream.py"><img src="https://github.com/pipecat-ai/pipecat/blob/main/examples/assets/moondream.png" width="400" /></a>
</p>

## 🧩 Available services

| Category            | Services                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Speech-to-Text      | [AssemblyAI](https://docs.pipecat.ai/api-reference/server/services/stt/assemblyai), [AWS](https://docs.pipecat.ai/api-reference/server/services/stt/aws), [Azure](https://docs.pipecat.ai/api-reference/server/services/stt/azure), [Cartesia](https://docs.pipecat.ai/api-reference/server/services/stt/cartesia), [Deepgram](https://docs.pipecat.ai/api-reference/server/services/stt/deepgram), [ElevenLabs](https://docs.pipecat.ai/api-reference/server/services/stt/elevenlabs), [Fal Wizper](https://docs.pipecat.ai/api-reference/server/services/stt/fal), [FunASR](https://docs.pipecat.ai/api-reference/server/services/stt/funasr), [Gladia](https://docs.pipecat.ai/api-reference/server/services/stt/gladia), [Google](https://docs.pipecat.ai/api-reference/server/services/stt/google), [Gradium](https://docs.pipecat.ai/api-reference/server/services/stt/gradium), [Groq (Whisper)](https://docs.pipecat.ai/api-reference/server/services/stt/groq), [Mistral](https://docs.pipecat.ai/api-reference/server/services/stt/mistral), [Moonshine](https://docs.pipecat.ai/api-reference/server/services/stt/moonshine), [NVIDIA](https://docs.pipecat.ai/api-reference/server/services/stt/nvidia), [OpenAI (Whisper)](https://docs.pipecat.ai/api-reference/server/services/stt/openai), [Sarvam](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam), [Soniox](https://docs.pipecat.ai/api-reference/server/services/stt/soniox), [Speechmatics](https://docs.pipecat.ai/api-reference/server/services/stt/speechmatics), [Together](https://docs.pipecat.ai/api-reference/server/services/stt/together), [Whisper](https://docs.pipecat.ai/api-reference/server/services/stt/whisper), [xAI](https://docs.pipecat.ai/api-reference/server/services/stt/xai)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| LLMs                | [Anthropic](https://docs.pipecat.ai/api-reference/server/services/llm/anthropic), [AWS](https://docs.pipecat.ai/api-reference/server/services/llm/aws), [Azure](https://docs.pipecat.ai/api-reference/server/services/llm/azure), [Baseten](https://docs.pipecat.ai/api-reference/server/services/llm/baseten), [Cerebras](https://docs.pipecat.ai/api-reference/server/services/llm/cerebras), [Crusoe](https://docs.pipecat.ai/api-reference/server/services/llm/crusoe), [DeepSeek](https://docs.pipecat.ai/api-reference/server/services/llm/deepseek), [Fireworks AI](https://docs.pipecat.ai/api-reference/server/services/llm/fireworks), [Gemini](https://docs.pipecat.ai/api-reference/server/services/llm/gemini), [Grok](https://docs.pipecat.ai/api-reference/server/services/llm/grok), [Groq](https://docs.pipecat.ai/api-reference/server/services/llm/groq), [Inception](https://docs.pipecat.ai/api-reference/server/services/llm/inception), [Mistral](https://docs.pipecat.ai/api-reference/server/services/llm/mistral), [Nebius](https://docs.pipecat.ai/api-reference/server/services/llm/nebius), [Novita](https://docs.pipecat.ai/api-reference/server/services/llm/novita), [NVIDIA NIM](https://docs.pipecat.ai/api-reference/server/services/llm/nvidia), [Ollama](https://docs.pipecat.ai/api-reference/server/services/llm/ollama), [OpenAI](https://docs.pipecat.ai/api-reference/server/services/llm/openai), [OpenAI Responses](https://docs.pipecat.ai/api-reference/server/services/llm/openai-responses), [OpenRouter](https://docs.pipecat.ai/api-reference/server/services/llm/openrouter), [Perplexity](https://docs.pipecat.ai/api-reference/server/services/llm/perplexity), [Qwen](https://docs.pipecat.ai/api-reference/server/services/llm/qwen), [SambaNova](https://docs.pipecat.ai/api-reference/server/services/llm/sambanova), [Sarvam](https://docs.pipecat.ai/api-reference/server/services/llm/sarvam), [Together AI](https://docs.pipecat.ai/api-reference/server/services/llm/together)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Text-to-Speech      | [Async](https://docs.pipecat.ai/api-reference/server/services/tts/asyncai), [AWS](https://docs.pipecat.ai/api-reference/server/services/tts/aws), [Azure](https://docs.pipecat.ai/api-reference/server/services/tts/azure), [Bland](https://docs.pipecat.ai/api-reference/server/services/tts/bland), [Camb AI](https://docs.pipecat.ai/api-reference/server/services/tts/camb), [Cartesia](https://docs.pipecat.ai/api-reference/server/services/tts/cartesia), [Deepgram](https://docs.pipecat.ai/api-reference/server/services/tts/deepgram), [ElevenLabs](https://docs.pipecat.ai/api-reference/server/services/tts/elevenlabs), [Fish](https://docs.pipecat.ai/api-reference/server/services/tts/fish), [Google](https://docs.pipecat.ai/api-reference/server/services/tts/google), [Gradium](https://docs.pipecat.ai/api-reference/server/services/tts/gradium), [Groq](https://docs.pipecat.ai/api-reference/server/services/tts/groq), [Hume](https://docs.pipecat.ai/api-reference/server/services/tts/hume), [Inworld](https://docs.pipecat.ai/api-reference/server/services/tts/inworld), [Kokoro](https://docs.pipecat.ai/api-reference/server/services/tts/kokoro), [LMNT](https://docs.pipecat.ai/api-reference/server/services/tts/lmnt), [MiniMax](https://docs.pipecat.ai/api-reference/server/services/tts/minimax), [Mistral](https://docs.pipecat.ai/api-reference/server/services/tts/mistral), [Neuphonic](https://docs.pipecat.ai/api-reference/server/services/tts/neuphonic), [NVIDIA](https://docs.pipecat.ai/api-reference/server/services/tts/nvidia), [OpenAI](https://docs.pipecat.ai/api-reference/server/services/tts/openai), [Piper](https://docs.pipecat.ai/api-reference/server/services/tts/piper), [Resemble](https://docs.pipecat.ai/api-reference/server/services/tts/resemble), [Rime](https://docs.pipecat.ai/api-reference/server/services/tts/rime), [Sarvam](https://docs.pipecat.ai/api-reference/server/services/tts/sarvam), [Smallest](https://docs.pipecat.ai/api-reference/server/services/tts/smallest), [Soniox](https://docs.pipecat.ai/api-reference/server/services/tts/soniox), [Speechify](https://docs.pipecat.ai/api-reference/server/services/tts/speechify), [Speechmatics](https://docs.pipecat.ai/api-reference/server/services/tts/speechmatics), [Together](https://docs.pipecat.ai/api-reference/server/services/tts/together), [xAI](https://docs.pipecat.ai/api-reference/server/services/tts/xai), [XTTS](https://docs.pipecat.ai/api-reference/server/services/tts/xtts) |
| Speech-to-Speech    | [AWS Nova Sonic](https://docs.pipecat.ai/api-reference/server/services/s2s/aws), [Gemini Multimodal Live](https://docs.pipecat.ai/api-reference/server/services/s2s/gemini), [Grok Voice Agent](https://docs.pipecat.ai/api-reference/server/services/s2s/grok), [OpenAI Realtime](https://docs.pipecat.ai/api-reference/server/services/s2s/openai), [Ultravox](https://docs.pipecat.ai/api-reference/server/services/s2s/ultravox),                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Transport           | [Daily (WebRTC)](https://docs.pipecat.ai/api-reference/server/services/transport/daily), [FastAPI Websocket](https://docs.pipecat.ai/api-reference/server/services/transport/fastapi-websocket), [LiveKit (WebRTC)](https://docs.pipecat.ai/api-reference/server/services/transport/livekit), [SmallWebRTCTransport](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc), [Vonage (WebRTC)](https://docs.pipecat.ai/api-reference/server/services/transport/vonage), [WebSocket Server](https://docs.pipecat.ai/api-reference/server/services/transport/websocket-server), [WhatsApp](https://docs.pipecat.ai/api-reference/server/services/transport/whatsapp), Local                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Serializers         | [Exotel](https://docs.pipecat.ai/api-reference/server/services/serializers/exotel), [Genesys](https://docs.pipecat.ai/api-reference/server/services/serializers/genesys), [Plivo](https://docs.pipecat.ai/api-reference/server/services/serializers/plivo), [Twilio](https://docs.pipecat.ai/api-reference/server/services/serializers/twilio), [Telnyx](https://docs.pipecat.ai/api-reference/server/services/serializers/telnyx), [Vonage](https://docs.pipecat.ai/api-reference/server/services/serializers/vonage)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Video               | [HeyGen](https://docs.pipecat.ai/api-reference/server/services/video/heygen), [LemonSlice](https://docs.pipecat.ai/api-reference/server/services/transport/lemonslice), [Tavus](https://docs.pipecat.ai/api-reference/server/services/video/tavus), [Simli](https://docs.pipecat.ai/api-reference/server/services/video/simli)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Memory              | [mem0](https://docs.pipecat.ai/api-reference/server/services/memory/mem0)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Vision & Image      | [fal](https://docs.pipecat.ai/api-reference/server/services/image-generation/fal), [Google Imagen](https://docs.pipecat.ai/api-reference/server/services/image-generation/google-imagen), [Moondream](https://docs.pipecat.ai/api-reference/server/services/vision/moondream)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Audio Processing    | [Silero VAD](https://docs.pipecat.ai/api-reference/server/utilities/audio/silero-vad-analyzer), [Krisp Viva](https://docs.pipecat.ai/guides/features/krisp-viva), [Koala](https://docs.pipecat.ai/api-reference/server/utilities/audio/koala-filter), [ai-coustics](https://docs.pipecat.ai/api-reference/server/utilities/audio/aic-filter), [RNNoise](https://docs.pipecat.ai/api-reference/server/utilities/audio/rnnoise-filter)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Analytics & Metrics | [OpenTelemetry](https://docs.pipecat.ai/api-reference/server/utilities/opentelemetry), [Sentry](https://docs.pipecat.ai/api-reference/server/services/analytics/sentry)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Community           | [Browse community integrations →](https://docs.pipecat.ai/api-reference/server/services/supported-services)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

📚 [View full services documentation →](https://docs.pipecat.ai/api-reference/server/services/supported-services)

## ⚡ Getting started

Run Pipecat on your local machine, then move your agent processes to the cloud when you're ready. Either way, you'll need [uv](https://docs.astral.sh/uv/getting-started/installation/) — install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Start a new project with the CLI

The quickest path: install the Pipecat CLI and scaffold a new phone or web/mobile bot interactively.

```bash
uv tool install "pipecat-ai[cli]"
pipecat init
```

Follow the [quickstart guide](https://docs.pipecat.ai/getting-started/quickstart) to get your very first bot running, or dive a little deeper into [bootstrapping a project](https://docs.pipecat.ai/pipecat/get-started/build-your-next-bot).

### Manual installation

Prefer to wire things up yourself?

1. Install the module

   ```bash
   # For new projects
   uv init my-pipecat-app
   cd my-pipecat-app
   uv add pipecat-ai

   # Or for existing projects
   uv add pipecat-ai
   ```

2. Set up your environment

   ```bash
   cp env.example .env
   ```

3. To keep things lightweight, only the core framework is included by default. If you need support for third-party AI services, you can add the necessary dependencies with:

   ```bash
   uv add "pipecat-ai[option,...]"
   ```

> **Using pip?** You can still use `pip install pipecat-ai` and `pip install "pipecat-ai[option,...]"` to get set up.

From here, the code examples below are the best way to learn — agents you can run, read, and adapt.

## 🧪 Code examples

- [Focused examples](https://github.com/pipecat-ai/pipecat/tree/main/examples) — small agents that each illustrate one or two specific services or concepts
- [Example apps](https://github.com/pipecat-ai/pipecat-examples) — complete applications you can use as starting points for development

## 🛠️ Developing Pipecat

### Prerequisites

**Minimum Python Version:** 3.11
**Recommended Python Version:** >= 3.12

### Setup steps

1. Clone the repository and navigate to it:

   ```bash
   git clone https://github.com/pipecat-ai/pipecat.git
   cd pipecat
   ```

2. Install development and testing dependencies:

   ```bash
   uv sync --group dev --all-extras \
     --no-extra gstreamer \
     --no-extra local \
   ```

3. Install the git pre-commit hooks:

   ```bash
   uv run pre-commit install
   ```

> **Note**: Some extras (local, gstreamer) require system dependencies. See documentation if you encounter build errors.

### Claude Code skills

Install development workflow skills for contributing to Pipecat with [Claude Code](https://claude.ai/code):

```
claude plugin marketplace add pipecat-ai/pipecat
claude plugin install pipecat-dev@pipecat-dev-skills
```

### Running tests

To run all tests, from the root directory:

```bash
uv run pytest
```

Run a specific test suite:

```bash
uv run pytest tests/test_name.py
```

## 🤝 Contributing

We welcome contributions from the community! Whether you're fixing bugs, improving documentation, or adding new features, here's how you can help:

- **Found a bug?** Open an [issue](https://github.com/pipecat-ai/pipecat/issues)
- **Have a feature idea?** Start a [discussion](https://discord.gg/pipecat)
- **Want to contribute code?** Check our [CONTRIBUTING.md](CONTRIBUTING.md) guide
- **Documentation improvements?** [Docs](https://github.com/pipecat-ai/docs) PRs are always welcome

Before submitting a pull request, please check existing issues and PRs to avoid duplicates.

We aim to review all contributions promptly and provide constructive feedback to help get your changes merged.

## 🛟 Getting help

➡️ [Join our Discord](https://discord.gg/pipecat)

➡️ [Read the docs](https://docs.pipecat.ai)

➡️ [Reach us on X](https://x.com/pipecat_ai)

➡️ [Enterprise support](mailto:help@daily.co) for consulting and support agreements
