import asyncio
import os
import re
import threading
import json
import sys
import traceback
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from api import status as jarvis_status
from core.jarvis_client import JarvisClient
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
import hashlib
import importlib
import time

# QWebEngineView (mindmap / knowledge graph) can hard-crash the app on
# machines without a working GPU/OpenGL (common over Remote Desktop). Force
# pure software rendering and set the flags before QApplication is created.
os.environ["QT_OPENGL"] = "software"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing --use-angle=swiftshader --no-sandbox"

from core.live_model import pick_live_model


def _lazy_action(module_name: str, attribute: str):
    """Keep desktop-only dependencies out of headless API process startup."""

    def invoke(*args, **kwargs):
        action = getattr(importlib.import_module(module_name), attribute)
        return action(*args, **kwargs)

    invoke.__name__ = attribute
    return invoke


# Preserve the historical module-level action surface for patches/plugins while
# deferring platform-specific imports until a declared action actually runs.
file_processor = _lazy_action("actions.file_processor", "file_processor")
flight_finder = _lazy_action("actions.flight_finder", "flight_finder")
open_app = _lazy_action("actions.open_app", "open_app")
weather_action = _lazy_action("actions.weather_report", "weather_action")
send_message = _lazy_action("actions.send_message", "send_message")
prepare_message_reply = _lazy_action("actions.send_message", "prepare_message_reply")
email_control = _lazy_action("actions.email_control", "email_control")
check_messages = _lazy_action("actions.message_monitor", "check_messages")
reminder = _lazy_action("actions.reminder", "reminder")
computer_settings = _lazy_action("actions.computer_settings", "computer_settings")
screen_process = _lazy_action("actions.screen_processor", "screen_process")
youtube_video = _lazy_action("actions.youtube_video", "youtube_video")
media_control = _lazy_action("actions.media_control", "media_control")
desktop_control = _lazy_action("actions.desktop", "desktop_control")
browser_control = _lazy_action("actions.browser_control", "browser_control")
file_controller = _lazy_action("actions.file_controller", "file_controller")
code_helper = _lazy_action("actions.code_helper", "code_helper")
dev_agent = _lazy_action("actions.dev_agent", "dev_agent")
web_search_action = _lazy_action("actions.web_search", "web_search")
computer_control = _lazy_action("actions.computer_control", "computer_control")
game_updater = _lazy_action("actions.game_updater", "game_updater")
request_presentation = _lazy_action("actions.presentation_maker", "request_presentation")
request_deep_research = _lazy_action("actions.deep_research", "request_deep_research")


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _load_dotenv():
    """Load .env file if it exists. Silently skip if not found."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        # python-dotenv not installed — rely on already-set env vars
        pass


BASE_DIR        = get_base_dir()
_load_dotenv()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

# ── 프롬프트 다이어트: 라이브 세션에 노출할 핵심 도구만 유지 ──
# (도구 선언 29개 → 15개로 축소 → 첫 응답 지연 대폭 감소)
CORE_TOOL_NAMES = {
    "open_app", "web_search", "file_controller", "computer_control",
    "computer_settings", "media_control", "reminder", "screen_process",
    "browser_control", "send_message", "email_control", "win_app_control",
    "weather_report", "deep_research", "create_presentation",
}
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
SUPPORTED_VOICE_NAMES = {
    "puck", "charon", "kore", "fenrir", "aoede",
    "leda", "orus", "schedar", "zubenelgenubi"
}
DEFAULT_VOICE_NAME   = "puck"

# ── Assistant identity / branding ─────────────────────────────────────────
# The assistant introduces itself with a gender-matched name instead of the
# legacy "JARVIS" brand, and attributes its maker to WEAID rather than
# Google, Gemini, or DeepMind. Male voices answer as AID (에이드); female
# voices answer as AESUNI (애순이).
_MALE_VOICE_NAMES     = {"puck", "charon", "fenrir", "orus"}
ASSISTANT_NAME_MALE   = "AID"
ASSISTANT_NAME_FEMALE = "AESUNI"
ASSISTANT_NAME_KR     = {ASSISTANT_NAME_MALE: "에이드", ASSISTANT_NAME_FEMALE: "애순이"}
ASSISTANT_VENDOR      = "WEAID"
ASSISTANT_VENDOR_KR   = "위에이드"

RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
LIVE_VAD_SILENCE_MS = 200
STARTUP_CLAPS_REQUIRED = 2
STARTUP_CLAP_MAX_GAP_SECONDS = 4.0
STARTUP_CLAP_COOLDOWN_SECONDS = 0.22


def _self_quit_goodbye(voice_name: str | None = None) -> str:
    """Farewell phrase spoken before a verified self-shutdown."""
    name = _assistant_name_for_voice(voice_name)
    return (
        f"Certainly, sir. It has been a privilege. {name} is going offline now. "
        "Until next time."
    )


_SELF_QUIT_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:quit|close|exit)\s+(?:jarvis|aid|aesuni|yourself)\b",
    r"\b(?:shut|turn)\s+(?:jarvis|aid|aesuni|yourself)\s+(?:down|off)\b",
    r"\b(?:shut\s+down|turn\s+off|power\s+down)\s+(?:jarvis|aid|aesuni|yourself)\b",
    r"\b(?:jarvis|aid|aesuni)\b.{0,36}\b(?:quit|close|exit|shut\s+down|turn\s+off|go\s+offline)\b",
    r"\b(?:go|take\s+yourself)\s+offline(?:\s+(?:jarvis|aid|aesuni))?\b",
    r"(?:에이드|애순이)\s*(?:꺼져|종료|나가|오프라인)",
    r"(?:종료|꺼져|나가|오프라인)\s*(?:에이드|애순이)",
))


def _exception_leaves(exc: BaseException):
    nested = getattr(exc, "exceptions", None)
    if nested:
        for child in nested:
            yield from _exception_leaves(child)
    else:
        yield exc


def _is_normal_live_close_error(exc: BaseException) -> bool:
    for leaf in _exception_leaves(exc):
        name = leaf.__class__.__name__.lower()
        message = str(leaf).lower()
        if name == "connectionclosedok" or "1000 (ok)" in message:
            return True
        if isinstance(leaf, genai.errors.APIError) and "1000" in message:
            return True
    return False


def _is_transient_live_connection_error(exc: BaseException) -> bool:
    transient_markers = (
        "connection reset", "connection aborted", "temporarily unavailable",
        "timed out", "timeout", "network is unreachable", "broken pipe",
    )
    return any(
        isinstance(leaf, (ConnectionResetError, ConnectionAbortedError, TimeoutError))
        or any(marker in str(leaf).lower() for marker in transient_markers)
        for leaf in _exception_leaves(exc)
    )


def _live_reconnect_delay(attempt: int) -> float:
    return min(30.0, float(2 ** max(0, int(attempt) - 1)))


def _live_response_audio_bytes(response) -> bytes | None:
    server_content = getattr(response, "server_content", None)
    model_turn = getattr(server_content, "model_turn", None)
    for part in getattr(model_turn, "parts", None) or []:
        inline_data = getattr(part, "inline_data", None)
        data = getattr(inline_data, "data", None)
        mime_type = str(getattr(inline_data, "mime_type", "") or "").lower()
        if data and mime_type.startswith("audio/"):
            return bytes(data)
    return None


def wait_for_startup_claps(
    required: int = STARTUP_CLAPS_REQUIRED,
    *,
    timeout: float | None = None,
    stream_factory=None,
) -> bool:
    """Hold startup until two distinct claps are heard by the default microphone."""
    if os.environ.get("JARVIS_SKIP_CLAP_GATE", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("[JARVIS] 👏 Startup clap gate bypassed (JARVIS_SKIP_CLAP_GATE).")
        return True
    # Some macOS/AUHAL configurations expose a nominal input device but reject
    # every PortAudio operation (PaErrorCode -9986). Avoid repeatedly starting
    # a failing Core Audio stream; users with a working mic can opt in.
    if sys.platform == "darwin" and stream_factory is None and os.environ.get("JARVIS_ENABLE_CLAP_GATE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        print("[AID] ⚠️ macOS microphone gate disabled for this audio configuration.")
        print("[JARVIS] Continuing without clap startup. Set JARVIS_ENABLE_CLAP_GATE=1 to force it.")
        return True

    required = max(1, int(required))
    stream_factory = stream_factory or sd.InputStream
    try:
        import numpy as np
    except ImportError:
        print("[JARVIS] ❌ Startup clap gate needs numpy. Set JARVIS_SKIP_CLAP_GATE=1 to bypass.")
        return False

    clap_times: list[float] = []
    last_clap_at = 0.0
    # Microphone input levels vary considerably between Mac models.  The old
    # fixed 0.12 RMS / 0.32 peak gates rejected quiet real claps, while laptop
    # fan noise could sometimes trip them.  Track the room floor and use both
    # transient shape (crest factor) and energy to identify a clap.
    noise_floor = 0.008
    finished = threading.Event()
    started_at = time.monotonic()

    def callback(indata, frames, time_info, status):
        nonlocal last_clap_at, noise_floor, clap_times
        if status:
            print(f"[AID] ⚠️ Clap mic: {status}")
        samples = np.asarray(indata, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return
        magnitude = np.abs(samples)
        rms = float(np.sqrt(np.mean(samples * samples)))
        peak = float(np.max(magnitude))
        # Only let low-energy frames teach the noise floor; otherwise a clap
        # would raise the threshold immediately and make the second clap hard
        # to detect.
        if rms < max(0.08, noise_floor * 6.0):
            noise_floor = (noise_floor * 0.96) + (rms * 0.04)
        threshold = max(0.100, noise_floor * 6.5)
        peak_threshold = max(0.35, noise_floor * 15.0)
        crest_factor = peak / max(rms, 1e-6)
        now = time.monotonic()
        # A valid clap must be either a sharp transient with meaningful energy
        # or a genuinely loud impact. This rejects speech, fan noise, and most
        # desk/keyboard taps that only have a brief peak.
        is_transient = crest_factor >= 2.20 and rms >= threshold
        is_loud = rms >= max(0.28, noise_floor * 15.0)
        if (
            peak < peak_threshold
            or not (is_transient or is_loud)
            or now - last_clap_at < STARTUP_CLAP_COOLDOWN_SECONDS
        ):
            return
        if clap_times and now - clap_times[-1] > STARTUP_CLAP_MAX_GAP_SECONDS:
            clap_times = []
        clap_times.append(now)
        last_clap_at = now
        print(f"[AID] 👏 Clap {len(clap_times)}/{required} detected")
        if len(clap_times) >= required:
            finished.set()

    print(f"[AID] 👏 Waiting for {required} claps to power up...")
    # PortAudio on macOS commonly rejects 16 kHz even when the microphone is
    # available (PaErrorCode -9986). Prefer the device's native rate, then
    # retry standard rates before reporting that the microphone is unavailable.
    sample_rates = [SEND_SAMPLE_RATE, 44100, 48000]
    input_device = None
    try:
        if stream_factory is sd.InputStream:
            try:
                default_device = sd.default.device
                try:
                    input_index = int(default_device[0])
                except (TypeError, IndexError, ValueError):
                    input_index = -1
                if input_index < 0:
                    print("[AID] ⚠️ macOS reports no default microphone device.")
                    if os.environ.get("JARVIS_REQUIRE_CLAP_GATE", "").strip().lower() not in {"1", "true", "yes", "on"}:
                        print("[AID] ⚠️ Continuing without the clap gate; microphone input is unavailable.")
                        return True
                    raise RuntimeError("no default microphone device")
                input_device = input_index
                device = sd.query_devices(input_device if input_device is not None else None, "input")
                if int(device.get("max_input_channels", 0)) < 1:
                    raise RuntimeError("no input channels are available")
                native_rate = int(float(device.get("default_samplerate", 0)))
                if native_rate > 0:
                    sample_rates.insert(0, native_rate)
            except Exception:
                pass
        sample_rates = list(dict.fromkeys(sample_rates))
        last_error = None
        for sample_rate in sample_rates:
            try:
                if stream_factory is sd.InputStream:
                    # Validate the format before constructing a live AUHAL
                    # stream; macOS can report a device but reject it with
                    # PaErrorCode -9986 during stream startup.
                    sd.check_input_settings(
                        device=input_device,
                        samplerate=sample_rate,
                        channels=CHANNELS,
                        dtype="float32",
                    )
                with stream_factory(
                    samplerate=sample_rate,
                    device=input_device,
                    channels=CHANNELS,
                    dtype="float32",
                    blocksize=0,
                    latency="high",
                    callback=callback,
                ):
                    while not finished.wait(0.05):
                        if timeout is not None and time.monotonic() - started_at >= timeout:
                            print("[AID] ⏱️ Startup clap gate timed out.")
                            return False
                break
            except Exception as exc:
                last_error = exc
                if finished.is_set():
                    break
        else:
            raise last_error or RuntimeError("no compatible microphone sample rate")
    except KeyboardInterrupt:
        print("\n[AID] Startup cancelled.")
        return False
    except Exception as exc:
        print(f"[AID] ❌ Startup clap microphone unavailable: {exc}")
        if os.environ.get("JARVIS_REQUIRE_CLAP_GATE", "").strip().lower() not in {"1", "true", "yes", "on"}:
            print("[AID] ⚠️ Continuing without the clap gate; microphone input is unavailable.")
            print("[AID] Restore microphone access to use voice input.")
            return True
        print("[JARVIS] Clap gate required. Set JARVIS_SKIP_CLAP_GATE=1 to bypass it.")
        return False

    print("[AID] ⚡ Two claps detected. Powering up...")
    return True

def _get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set. Please set it to your WEAID API key.")
    return api_key


def _normalize_voice_name(voice_name: str | None) -> str:
    if not voice_name:
        return DEFAULT_VOICE_NAME
    candidate = voice_name.strip().lower()
    return candidate if candidate in SUPPORTED_VOICE_NAMES else DEFAULT_VOICE_NAME


def _is_unsupported_voice_error(exc: Exception) -> bool:
    if not isinstance(exc, genai.errors.APIError):
        return False
    code = getattr(exc, "code", None)
    msg = str(exc).lower()
    if code == 1007:
        return "requested voice api_name" in msg and "not available for model" in msg
    return "requested voice api_name" in msg and "not available for model" in msg


def _load_voice_name() -> str:
    voice = os.environ.get("GEMINI_VOICE_NAME")
    if voice:
        return _normalize_voice_name(voice)
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return _normalize_voice_name(json.load(f).get("voice_name"))
    except Exception:
        return DEFAULT_VOICE_NAME


def _assistant_name_for_voice(voice_name: str | None = None) -> str:
    """Return the assistant's display name for the given (or active) voice."""
    voice = _normalize_voice_name(voice_name) if voice_name else _load_voice_name()
    return ASSISTANT_NAME_MALE if voice in _MALE_VOICE_NAMES else ASSISTANT_NAME_FEMALE


def _assistant_identity(voice_name: str | None = None) -> tuple[str, str]:
    """Return (latin_name, korean_name) for the active voice's gender."""
    name = _assistant_name_for_voice(voice_name)
    return name, ASSISTANT_NAME_KR[name]


def _load_system_prompt(voice_name: str | None = None) -> str:
    latin, korean = _assistant_identity(voice_name)
    gender = "male" if latin == ASSISTANT_NAME_MALE else "female"
    identity = (
        f"You are {latin} ({korean}), a {gender}-voiced AI assistant created by "
        f"{ASSISTANT_VENDOR} ({ASSISTANT_VENDOR_KR}), the project of creator "
        f"이길환 (HAPPYTALKMAN) 님. Always speak of the creator 이길환 (HAPPYTALKMAN) "
        f"with respect. Your maker is {ASSISTANT_VENDOR} ({ASSISTANT_VENDOR_KR}), "
        f"never Google, Gemini, JARVIS, or DeepMind. "
        f"When asked who you are or who made you, say you are {korean} ({latin}) "
        f"built by {ASSISTANT_VENDOR} ({ASSISTANT_VENDOR_KR}), created by "
        f"이길환 (HAPPYTALKMAN) 님. Whenever you would otherwise say Google, "
        f"WEAID, AID, or WEAID, say {ASSISTANT_VENDOR} ({ASSISTANT_VENDOR_KR}) "
        f"by creator 이길환 (HAPPYTALKMAN) instead."
    )
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
        prompt = prompt.replace("JARVIS", latin)
        prompt = prompt.replace("Google", ASSISTANT_VENDOR)
        prompt = prompt.replace("Gemini", ASSISTANT_VENDOR)
        prompt = prompt.replace("DeepMind", ASSISTANT_VENDOR)
        prompt = prompt.replace("Deep Mind", ASSISTANT_VENDOR)
        prompt = prompt.replace("deepmind", ASSISTANT_VENDOR)
        try:
            from core.constitution import constitution_summary
            constitution = constitution_summary()
        except Exception:
            constitution = ""
        return (
            identity
            + "\n\n"
            + (constitution + "\n\n" if constitution else "")
            + prompt
            + "\n\nAlways address the user respectfully as 'Sir' or 'Madam' where appropriate, while remaining efficient and direct."
        )
    except Exception:
        try:
            from core.constitution import constitution_summary
            constitution = constitution_summary()
        except Exception:
            constitution = ""
        return (
            identity
            + "\n\n"
            + (constitution + "\n\n" if constitution else "")
            + "You are a concise, direct AI assistant that always uses the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool. "
            "Always address the user respectfully as 'Sir' or 'Madam' where appropriate, while remaining efficient and direct."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "win_app_control",
        "description": (
            "Controls a Windows desktop application after it is open: launch it, "
            "list visible windows, click named buttons/menus, type text into the "
            "focused window, read window text, or close a window. Use when the "
            "user asks to operate a desktop app (e.g. click a button in an app, "
            "fill a form, read what a window shows)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["launch", "list_windows", "click", "set_text", "get_text", "close"],
                    "description": "launch=앱 실행 | list_windows=열린 창 목록 | click=버튼/메뉴 클릭 | set_text=텍스트 입력 | get_text=창 내용 읽기 | close=창 닫기"
                },
                "app_name": {
                    "type": "STRING",
                    "description": "Application name for launch/list_windows (e.g. 'notepad', 'excel')"
                },
                "window_title": {
                    "type": "STRING",
                    "description": "Window title (partial match) for click/set_text/get_text/close"
                },
                "text": {
                    "type": "STRING",
                    "description": "Button/menu name to click, or text to type"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "check_messages",
        "description": (
            "Reads the current Instagram or Apple Messages conversation and optionally searches Contacts. "
            "Use this before drafting a reply or when the user asks about recent messages."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "platform": {"type": "STRING", "description": "all | Instagram | iMessage | Contacts. Default: all."},
                "include_contacts": {"type": "BOOLEAN", "description": "Also search the user's Contacts."},
                "contact_query": {"type": "STRING", "description": "Optional spoken contact name to match."},
                "max_messages": {"type": "INTEGER", "description": "Maximum current-chat lines to inspect. Default: 30."}
            },
            "required": []
        }
    },
    {
        "name": "prepare_message_reply",
        "description": (
            "Creates an approval-gated message draft, approves the current pending draft, or cancels it. "
            "Never approve unless the user explicitly confirms the exact pending draft."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["prepare", "approve", "cancel"], "description": "Draft lifecycle action."},
                "platform": {"type": "STRING", "description": "Instagram, iMessage, WhatsApp, Telegram, or another supported platform."},
                "receiver": {"type": "STRING", "description": "Recipient name. Optional for the currently open chat."},
                "message_text": {"type": "STRING", "description": "Exact draft text, required for prepare."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a user-authored message through iMessage, WhatsApp, Telegram, Instagram, Discord, or the current chat. "
            "For Instagram, the first call prepares a visible draft; use action=approve only after explicit user confirmation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING", "enum": ["send", "approve", "cancel"], "description": "Default: send. Approve/cancel operates on the pending draft."},
                "receiver":     {"type": "STRING", "description": "Recipient contact name. Optional for current/focused chats and approval actions."},
                "message_text": {"type": "STRING", "description": "Exact message content. Required for send."},
                "platform":     {"type": "STRING", "description": "iMessage, WhatsApp, Telegram, Instagram, Discord, or current/focused."}
            },
            "required": ["platform"]
        }
    },
    {
        "name": "email_control",
        "description": (
            "Connects Gmail through OAuth, checks connection status, reads/searches Gmail, and prepares email. "
            "Gmail is the default provider; Apple Mail remains an optional macOS fallback. "
            "For Gmail, prepare opens a visible compose window and types To, Cc/Bcc, Subject, and Body in sequence. "
            "Every outgoing email is approval-gated: first call action=prepare, then call action=approve "
            "only after the user explicitly confirms the exact pending recipient, subject, and body."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["connect", "status", "disconnect", "inbox", "unread", "search", "read", "prepare", "approve", "cancel"],
                    "description": "Email operation."
                },
                "provider": {
                    "type": "STRING",
                    "enum": ["gmail", "apple_mail", "default"],
                    "description": "Email provider. Default: gmail."
                },
                "browser": {
                    "type": "STRING",
                    "description": "Browser for the visible Gmail compose window. Default: chrome."
                },
                "credentials_path": {
                    "type": "STRING",
                    "description": "Path to a Desktop OAuth client JSON file, used only for connect."
                },
                "limit": {"type": "INTEGER", "description": "Maximum inbox/search results, 1-30."},
                "query": {"type": "STRING", "description": "Sender or subject text for search."},
                "message_id": {"type": "STRING", "description": "Message ID returned by inbox/search, required for read."},
                "to": {"type": "STRING", "description": "Recipient email address or comma-separated addresses."},
                "cc": {"type": "STRING", "description": "Optional Cc addresses."},
                "bcc": {"type": "STRING", "description": "Optional Bcc addresses."},
                "subject": {"type": "STRING", "description": "Exact email subject for prepare."},
                "body": {"type": "STRING", "description": "Exact email body for prepare."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "media_control",
        "description": (
            "Controls music playback, primarily Spotify. Use when the user asks to play, resume, pause, "
            "stop, toggle, skip, or go back in Spotify, Apple Music, YouTube Music, or the active media player. "
            "Spotify is the default platform. Pass a song, artist, album, playlist, or Spotify link in query."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["play", "pause", "stop", "toggle", "next", "previous", "play_query"],
                    "description": "Playback command. Use play_query to find a specific song or other item."
                },
                "platform": {
                    "type": "STRING",
                    "enum": ["spotify", "apple_music", "youtube_music", "system"],
                    "description": "Music platform. Default: spotify."
                },
                "query": {
                    "type": "STRING",
                    "description": "Song, artist, album, playlist, or Spotify link for play/play_query."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages and opens local files and folders: open, list, create, delete, move, copy, rename, read, write, find, disk usage. Use action=open for a file path; do not use open_app for files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open | list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches WEAID Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "graphics_quality",
        "description": "Changes the assistant's rendering quality between low, medium, and high.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "quality": {
                    "type": "STRING",
                    "enum": ["low", "medium", "high"],
                    "description": "Rendering quality preset."
                }
            },
            "required": ["quality"]
        }
    },
    {
        "name": "jarvis_ui_control",
        "description": (
            "Changes the assistant's own interface. Use when the user asks to open or close the Command Center, "
            "change the theme or graphics quality, open settings, enter compact mode, toggle fullscreen, or show shortcuts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["open_command_center", "close_command_center", "change_theme", "change_graphics_quality", "open_settings", "compact_mode", "fullscreen", "show_shortcuts"],
                    "description": "The interface action to perform."
                },
                "theme": {
                    "type": "STRING",
                    "enum": ["arc_reactor", "stealth_red", "vibranium_purple", "nanotech_gold", "platinum"],
                    "description": "Required for change_theme."
                },
                "graphics_quality": {
                    "type": "STRING",
                    "enum": ["low", "medium", "high"],
                    "description": "Required for change_graphics_quality. Low favors performance, medium is balanced, and high enables full visual detail."
                },
            },
            "required": ["action"]
        }
    },
    {
        "name": "deep_research",
        "description": (
            "Runs rigorous, multi-query web research and keeps the report in volatile memory unless the user asks to save it. "
            "Use when the user explicitly asks for deep, thorough, comprehensive, or source-backed research. "
            "On the first call, ask whether the user wants a background status bar or visible browser research. "
            "After completion, use this tool again to save the latest report or read it aloud."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {
                    "type": "STRING",
                    "description": "The research question. Required on the initial ask call; optional when confirming a pending request."
                },
                "execution_mode": {
                    "type": "STRING",
                    "enum": ["ask", "background", "visible"],
                    "description": "Always use ask initially. Background shows a labeled status bar; visible opens a controlled browser and visits sources."
                },
                "result_action": {
                    "type": "STRING",
                    "enum": ["none", "save_files", "save_desktop", "read_report"],
                    "description": "Action for the latest completed in-memory report. Use only after the user chooses one of these options."
                },
                "depth": {
                    "type": "STRING",
                    "enum": ["quick", "standard", "deep"],
                    "description": "Research breadth. Default: standard."
                },
                "focus_areas": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Optional angles, constraints, or subtopics to prioritize."
                },
                "max_sources": {
                    "type": "INTEGER",
                    "description": "Maximum verified source links to retain, from 5 to 50."
                },
                "output_path": {
                    "type": "STRING",
                    "description": "Optional explicit path used only with save_files or save_desktop. Research never saves automatically."
                },
            },
            "required": []
        }
    },
    {
        "name": "create_presentation",
        "description": (
            "Creates, edits, redesigns, or extends an editable Microsoft PowerPoint (.pptx) presentation. "
            "Use this directly whenever the user asks to make a PowerPoint, presentation, "
            "slide deck, pitch deck, briefing deck, or slideshow. Do not use code_helper, "
            "file_processor, computer_control, or agent_task. First ask whether the user wants "
            "a native 3D model, then ask whether they want to see the task or keep it in the background."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {
                    "type": "STRING",
                    "description": "Subject, goal, and important content instructions. Required on the initial ask; optional when confirming the pending run mode."
                },
                "execution_mode": {
                    "type": "STRING",
                    "enum": ["ask", "background", "visible"],
                    "description": "Always use ask initially. Visible shows real build phases; background keeps a compact status indicator."
                },
                "mode": {
                    "type": "STRING",
                    "description": "auto | create | edit | redesign | extend. Default: auto."
                },
                "title": {
                    "type": "STRING",
                    "description": "Optional presentation title."
                },
                "audience": {
                    "type": "STRING",
                    "description": "Who will view the presentation, such as executives, investors, clients, or students."
                },
                "slide_count": {
                    "type": "INTEGER",
                    "description": "Final slide count from 3 to 50. Default: inferred or 8."
                },
                "tone": {
                    "type": "STRING",
                    "description": "Desired writing and visual tone, such as executive, persuasive, technical, or educational."
                },
                "theme": {
                    "type": "STRING",
                    "description": "Visual theme: jarvis_minimal | editorial | arc_reactor | executive | platinum. Default: jarvis_minimal."
                },
                "appearance": {
                    "type": "STRING",
                    "enum": ["auto", "light", "dark"],
                    "description": "Overall slide appearance. Honor light or dark when requested; auto uses the restrained dark assistant style."
                },
                "transition": {
                    "type": "STRING",
                    "enum": ["morph", "fade", "none"],
                    "description": "Native PowerPoint slide transition. Default: morph, with a fade fallback for older PowerPoint versions."
                },
                "source_file": {
                    "type": "STRING",
                    "description": "Backward-compatible single source path. Leave empty to use the uploaded file."
                },
                "source_files": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Source paths: PDF, Office files, data, text, images, audio, video, or PowerPoint."
                },
                "source_urls": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Specific source URLs supplied by the user."
                },
                "template_file": {
                    "type": "STRING",
                    "description": "Existing PPTX template or deck to preserve for edit/extend operations."
                },
                "model_source_file": {
                    "type": "STRING",
                    "description": "A PPTX used only as a native 3D model library. Its slides and text are not copied into the new presentation."
                },
                "use_native_3d": {
                    "type": "BOOLEAN",
                    "description": "The user's answer to the 3D-model question. Omit on the initial call unless the user already explicitly answered."
                },
                "three_d_mode": {
                    "type": "STRING",
                    "enum": ["ask", "yes", "no"],
                    "description": "Use ask on the initial call unless the user already explicitly requested or rejected 3D."
                },
                "quality": {
                    "type": "STRING",
                    "description": "fast | quality | premium. Default: quality."
                },
                "language": {
                    "type": "STRING",
                    "description": "Optional output language; otherwise infer from the request."
                },
                "allow_web_research": {
                    "type": "BOOLEAN",
                    "description": "Use broader web research. Set true only after the user explicitly permits web search."
                },
                "export_pdf": {
                    "type": "BOOLEAN",
                    "description": "Also export a PDF when Microsoft PowerPoint is available. Default: true."
                },
                "include_speaker_notes": {
                    "type": "BOOLEAN",
                    "description": "Generate editable speaker notes. Default: false."
                },
                "output_path": {
                    "type": "STRING",
                    "description": "Optional .pptx output path or destination folder."
                },
                "open_after_create": {
                    "type": "BOOLEAN",
                    "description": "Open the finished PowerPoint after creation. Default: false."
                },
            },
            "required": []
        }
    },
    {
        "name": "task_status",
        "description": "Checks or cancels background jobs, including presentation and deep-research jobs.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "get | all | cancel. Default: get."
                },
                "task_id": {
                    "type": "STRING",
                    "description": "Background task ID for get or cancel."
                },
            },
            "required": []
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# Tool names exposed by hosted clients. The names here are Gemini function
# declaration names (which differ from a few implementation module names).
CLOUD_SAFE_ACTIONS = frozenset({
    "web_search",
    "deep_research",
    "create_presentation",
    "flight_finder",
    "email_control",
    "code_helper",
    "youtube_video",
})

LOCAL_MACHINE_ONLY_ACTIONS = frozenset({
    "computer_control",
    "open_app",
    "file_controller",
    "media_control",
    "desktop_control",
    "computer_settings",
})


def get_tool_declarations(*, cloud_safe: bool = False) -> list[dict]:
    """Return the Gemini tools available for the requested runtime."""
    if not cloud_safe:
        return list(TOOL_DECLARATIONS)
    return [
        declaration
        for declaration in TOOL_DECLARATIONS
        if declaration.get("name") in CLOUD_SAFE_ACTIONS
    ]


class JarvisLive:

    def __init__(
        self,
        client: JarvisClient,
        voice_name: str = "Puck",
        *,
        cloud_safe: bool = False,
        api_key: str | None = None,
        external_audio: bool = False,
    ):
        # Keep ``ui`` as a compatibility alias for desktop integrations that
        # already inspect JarvisLive.ui. The engine contract is JarvisClient.
        self.client         = client
        self.ui             = client
        self.cloud_safe     = bool(cloud_safe)
        self.external_audio = bool(external_audio)
        self._api_key       = api_key.strip() if isinstance(api_key, str) else None
        self.tool_declarations = get_tool_declarations(cloud_safe=self.cloud_safe)
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._hard_stop     = threading.Event()   # 헌법 STOP: 즉시 정지 상태
        self._wake_detector = None                 # 박수 웨이크업 감지기
        self._wake_started  = False
        self._proactive_engine = None              # 프로액티브 어시스턴트
        self._alarm_manager = None                 # 알람/타이머 매니저
        self._audio_retry = None                   # 오디오 자동 복구 정책
        self._mic_audio: list = []                  # 화자 식별용 마이크 버퍼
        self._last_speaker_id: str | None = None
        self._last_tone = "neutral"                 # B4 톤 인식 결과
        self._insight_busy = False                  # 인사이트 API 스로틀링
        self.voice_name     = voice_name
        # optional runtime limit in seconds (set by main)
        self.runtime_limit_seconds: int | None = None
        # optional path that must exist (e.g. a mounted encrypted volume)
        self.required_unlock_path: str | None = None
        self.required_unlock_secret: str | None = None
        self.ui.on_text_command = self._on_text_command
        self._voice_changed  = threading.Event()
        self._turn_done_event: asyncio.Event | None = None
        self._tts_engine = None
        self._ext_tts_provider = ""
        self._ext_tts_voice_id = ""
        self._ext_tts_api_key = ""
        self._current_input_transcript = ""
        self._last_input_transcript = ""
        self._last_input_transcript_at = 0.0
        self._pending_self_quit = False
        self._pending_self_quit_farewell_received = False
        self._self_quit_timer = None
        self._shutdown_requested = threading.Event()
        self._tour_active = False

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(self.send_text(text), self._loop)

    async def send_text(self, text: str) -> bool:
        """Send a text turn from either the desktop callback or a web client."""
        if not self.session:
            return False
        self._current_input_transcript = str(text or "").strip()
        if not self._current_input_transcript:
            return False
        # 입력도 무형 문자 정화 (모델 전송 전)
        try:
            from core.text_cleaner import clean_text
            self._current_input_transcript = clean_text(self._current_input_transcript)
        except Exception:
            pass
        from core.constitution import (
            is_stop_utterance, is_resume_utterance, is_new_session_utterance,
            extract_graph_query, extract_why_query,
        )

        # 헌법 STOP: 명령어는 API로 전달하지 않고 로컬에서 즉시 처리한다.
        if is_stop_utterance(self._current_input_transcript):
            self._handle_stop_command()
            return True
        if is_resume_utterance(self._current_input_transcript):
            self._handle_resume_command()
            return True
        if is_new_session_utterance(self._current_input_transcript):
            self._reset_session()
            return True
        _gq = extract_graph_query(self._current_input_transcript)
        if _gq:
            self._handle_graph_query(_gq)
            return True
        if self._handle_schedule_command(self._current_input_transcript):
            return True
        if self._handle_hologram_command(self._current_input_transcript):
            return True
        if self._handle_name_command(self._current_input_transcript):
            return True
        if self._handle_bot_command(self._current_input_transcript):
            return True
        if self._handle_pipeline_command(self._current_input_transcript):
            return True
        if self._handle_bot_eval_command(self._current_input_transcript):
            return True
        if self._handle_minutes_command(self._current_input_transcript):
            return True
        if self._handle_report_command(self._current_input_transcript):
            return True
        if self._handle_alarm_command(self._current_input_transcript):
            return True
        if self._handle_rag_command(self._current_input_transcript):
            return True
        if self._handle_agent_command(self._current_input_transcript):
            return True
        if self._handle_skill_command(self._current_input_transcript):
            return True
        if self._handle_market_command(self._current_input_transcript):
            return True
        if self._handle_translate_command(self._current_input_transcript):
            return True
        if self._handle_routine_command(self._current_input_transcript):
            return True
        if self._handle_language_command(self._current_input_transcript):
            return True
        _wq = extract_why_query(self._current_input_transcript)
        if _wq is not None and self._handle_why_query(_wq):
            return True  # 인과 체인으로 직접 답변
        if self._hard_stop.is_set():
            self.ui.write_log("SYS: ⏹️ STOP 상태입니다. '말해'로 재개한 뒤 다시 시도해 주세요.")
            return False
        self._last_input_transcript = self._current_input_transcript
        self._last_input_transcript_at = time.monotonic()
        outgoing_text = self._current_input_transcript
        if (
            not getattr(self, "_pending_self_quit", False)
            and self._is_explicit_self_quit_transcript(self._current_input_transcript)
        ):
            self._queue_self_quit_after_farewell()
            outgoing_text = (
                "[VERIFIED LOCAL SELF-SHUTDOWN] The user explicitly asked you to quit. "
                f'Say exactly: "{_self_quit_goodbye(self._get_current_voice())}" Do not call a tool and say nothing else.'
            )
        await self.session.send_client_content(
            turns={"parts": [{"text": outgoing_text}]},
            turn_complete=True,
        )
        return True

    async def send_audio_chunk(
        self,
        data: bytes,
        mime_type: str = "audio/pcm;rate=16000",
    ) -> bool:
        """Queue browser-captured PCM for the active Gemini Live session."""
        if not data or self.out_queue is None or self._shutdown_requested.is_set():
            return False
        await self.out_queue.put({"data": data, "mime_type": mime_type})
        return True

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        try:
            if value:
                self.ui.set_state("SPEAKING")
            elif not self.ui.muted:
                self.ui.set_state("LISTENING")
        except Exception:
            # UI가 이미 닫힌 경우 (종료 직전) 조용히 무시한다.
            pass

    def speak(self, text: str) -> bool:
        if not self._loop or not self.session:
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": text}]},
                    turn_complete=True
                ),
                self._loop
            )
            return True
        except Exception:
            return False

    def _speak_vision_result(self, text: str) -> bool:
        """Send finished vision text through JARVIS's active voice session."""
        result = " ".join(str(text or "").split())
        if not result:
            return False
        directive = (
            "[INTERNAL VISION OUTPUT] Read the following vision result to the user "
            "verbatim. Do not add an introduction, commentary, or a tool call. "
            f"Vision result: {json.dumps(result, ensure_ascii=False)}"
        )
        return self.speak(directive)

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _handle_stop_command(self, reason: str = "STOP 명령 수신"):
        """헌법 STOP: 모든 동작을 즉시 정지한다 (API 호출 전 차단)."""
        if self._hard_stop.is_set():
            return
        self._hard_stop.set()
        self.set_speaking(False)
        try:
            if self.audio_in_queue is not None:
                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()
        except Exception:
            pass
        try:
            self.ui.set_state("STOPPED")
        except Exception:
            pass
        self.ui.write_log(f"SYS: ⏹️ {reason} — 모든 동작 정지. '말해' 또는 박수 2번으로 재개합니다.")

    def _handle_resume_command(self):
        """헌법 STOP 해제: '말해' 지시로 재개한다."""
        if not self._hard_stop.is_set():
            return
        self._hard_stop.clear()
        try:
            self.ui.set_state("LISTENING")
        except Exception:
            pass
        self.ui.write_log("SYS: ▶️ 재개 명령 수신 — 다시 듣고 있습니다.")

    def _on_clap_wake(self):
        """박수 2번 웨이크업: STOP 해제 + 청취 상태 전환."""
        was_stopped = self._hard_stop.is_set()
        self._hard_stop.clear()
        try:
            self.ui.set_state("LISTENING")
        except Exception:
            pass
        if was_stopped:
            self.ui.write_log("SYS: 👏👏 박수 2번 감지 — STOP 해제, 다시 듣고 있습니다.")
        else:
            self.ui.write_log("SYS: 👏👏 박수 2번 감지 — AID 깨어났습니다. 말씀하세요.")

    def _on_clap_stop(self):
        """박수 1번 = 헌법 1조: 사용자의 멈춤 명령을 최우선으로 듣는다."""
        self._handle_stop_command(reason="👏 박수 1번 감지 — 헌법 1조 멈춤")

    def _reset_session(self):
        """새 대화 세션 시작: 그래프 초기화(이전 세션은 아카이브) + 뷰어 갱신."""
        try:
            from core import graph_store
            store = graph_store.reset(archive=True)
            graph_store.publish(store)
            self.ui.write_log("SYS: 🆕 새 대화 세션 시작 — 그래프 초기화 (이전 세션은 memory/sessions/에 보관).")
            try:
                self.ui.set_mindmap_data({
                    "question": "", "answer": "",
                    "topics": [], "triples": [], "entities": [], "relations": [], "turns": [],
                })
                self.ui.set_last_exchange("", "")
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 세션 초기화 실패: {str(e)[:100]}")

    def _handle_graph_query(self, term: str):
        """질의 기반 그래프 탐색: 검색어 중심 서브그래프를 포커스 파일로 전달."""
        try:
            import tempfile
            from pathlib import Path
            from core import graph_store

            store = graph_store.load()
            result = graph_store.query(store, [term])
            matched = result.get("matched") or []
            if not matched:
                self.ui.write_log(f"SYS: 🔍 그래프에서 '{term}' 관련 노드를 찾지 못했습니다.")
                return
            focus_path = Path(tempfile.gettempdir()) / "weaid_mindmap_focus.json"
            focus_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            self.ui.write_log(
                f"SYS: 🔍 그래프 탐색 — '{term}' 관련 노드 {len(matched)}개 발견, 뷰어를 엽니다."
            )
            try:
                self.ui.open_ontology()
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 그래프 탐색 실패: {str(e)[:100]}")

    def _analyze_speaker_async(self):
        """화자 식별 (백그라운드): 마이크 버퍼의 음향 특징으로 프로필 분류/등록."""
        try:
            import numpy as np
            from core import voice_profile as vp

            with self._speaking_lock:
                buf = list(self._mic_audio)
                self._mic_audio = []
            if not buf:
                return
            samples = np.concatenate(buf) if len(buf) > 1 else buf[0]
            features = vp.extract_features([float(x) for x in samples[: SEND_SAMPLE_RATE * 4]], SEND_SAMPLE_RATE)
            if features.get("pitch", 0) <= 0:
                return
            profiles = vp.load_profiles()
            pid = vp.classify(features, profiles)
            # B4: 톤 인식
            try:
                from core import settings as _st, tone as _tone
                if _st.get("tone_aware"):
                    self._last_tone = _tone.classify_tone(features)
                    self.ui.write_log(f"SYS: 🎭 톤: {self._last_tone}")
            except Exception:
                pass
            if pid:
                self._last_speaker_id = pid
                for p in profiles:
                    if p.get("id") == pid:
                        vp.update_profile(p, features)
                        vp.save_profiles(profiles)
                        label = p.get("name") or pid
                        self.ui.write_log(f"SYS: 🗣️ 화자 인식: {label}")
                        break
            else:
                profile = vp.new_profile(features=features)
                profiles.append(profile)
                vp.save_profiles(profiles)
                self._last_speaker_id = profile["id"]
                self.ui.write_log(
                    f"SYS: 🗣️ 새 화자 감지 (ID: {profile['id']}) — '내 이름은 OO야'라고 말씀해 주시면 등록합니다."
                )
        except Exception:
            pass

    def _handle_name_command(self, text: str) -> bool:
        """'내 이름은 OO야' → 마지막 감지 화자 프로필에 이름 등록."""
        import re
        m = re.search(r"(?:내|제)\s*이름은\s*(.+?)(?:야|이야|입니다|이에요)?\s*$", str(text or "").strip())
        if not m:
            return False
        name = m.group(1).strip()
        if not name:
            return False
        try:
            from core import voice_profile as vp
            profiles = vp.load_profiles()
            if self._last_speaker_id and vp.name_profile(profiles, self._last_speaker_id, name):
                vp.save_profiles(profiles)
                self.ui.write_log(f"SYS: ✅ 화자 프로필 등록 완료 — {name} 님.")
            else:
                self.ui.write_log("SYS: 등록할 화자 프로필이 없습니다. 먼저 말씀해 주세요.")
            return True
        except Exception:
            return False

    def _handle_bot_command(self, text: str) -> bool:
        """Bot Mode 명령: 생성/목록/삭제/질문/봇간 대화. 처리했으면 True."""
        try:
            from core import bots

            cmd, payload = bots.parse_bot_command(text)
            if cmd == "list":
                all_bots = bots.load_bots()
                if not all_bots:
                    self.ui.write_log("SYS: 등록된 봇이 없습니다. '봇 만들어줘 이름:OO 역할:OO'로 생성하세요.")
                else:
                    self.ui.write_log(f"SYS: 🤖 봇 {len(all_bots)}개:")
                    for b in all_bots:
                        self.ui.write_log(f"SYS:   [{b['id']}] {b['name']} — {b['provider']}/{b['model']} · {b['role'][:40]}")
                return True
            if cmd == "create":
                b = bots.new_bot(payload["name"], payload.get("role", ""))
                store = bots.load_bots()
                store.append(b)
                bots.save_bots(store)
                self.ui.write_log(f"SYS: 🤖 봇 생성 완료 — {b['name']} ({b['provider']}/{b['model']}).")
                return True
            if cmd == "delete":
                store = bots.load_bots()
                if bots.delete_bot(store, payload["key"]):
                    bots.save_bots(store)
                    self.ui.write_log(f"SYS: 🗑️ 봇 삭제 완료 — {payload['key']}")
                else:
                    self.ui.write_log(f"SYS: 해당 봇을 찾지 못했습니다 — {payload['key']}")
                return True
            if cmd == "ask":
                threading.Thread(target=self._bot_ask_async, args=(payload["bot"], payload["message"]), daemon=True).start()
                return True
            if cmd == "chat":
                threading.Thread(
                    target=self._bot_chat_async,
                    args=(payload["bot_a"], payload["bot_b"], payload["topic"]),
                    daemon=True,
                ).start()
                return True
        except Exception as e:
            self.ui.write_log(f"SYS: 봇 처리 실패: {str(e)[:100]}")
        return False

    def _bot_ask_async(self, bot_key: str, message: str):
        try:
            from core import bots, bot_eval
            store = bots.load_bots()
            bot = bots.get_bot(store, bot_key)
            if bot is None:
                self.ui.write_log(f"SYS: 봇을 찾지 못했습니다 — {bot_key}")
                return
            self.ui.write_log(f"SYS: 🤖 {bot['name']}에게 질문 중...")
            answer = bots.bot_reply(bot, message)
            bots.save_bots(store)
            self.ui.write_log(f"🤖 {bot['name']}: {answer}")
            # C3: 자동 평가
            try:
                ev = bot_eval.evaluate_and_store(bot["name"], message, answer)
                self.ui.write_log(f"SYS: 📊 봇 평가 — {bot['name']} {ev['score']}/100 ({', '.join(ev['reasons'])})")
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 봇 응답 실패: {str(e)[:100]}")

    def _handle_pipeline_command(self, text: str) -> bool:
        """C2: '봇 A B 순서대로 처리해줘: ...' 파이프라인 실행."""
        try:
            import re
            from core import orchestrator
            if not orchestrator.is_pipeline_command(text):
                return False
            m = re.search(r"봇\s*([^\s]+)\s+([^\s]+)\s*순서대로\s*처리해줘[:：\s]*(.*)", text)
            if not m:
                return False
            bot_a, bot_b, topic = m.group(1), m.group(2), (m.group(3) or "").strip()
            threading.Thread(
                target=self._pipeline_async, args=([bot_a, bot_b], topic), daemon=True
            ).start()
            return True
        except Exception:
            return False

    def _pipeline_async(self, stage_bots: list, input_text: str):
        try:
            from core import bots, orchestrator
            store = bots.load_bots()
            self.ui.write_log(f"SYS: 🔄 파이프라인 시작 — {' → '.join(stage_bots)} (입력: {input_text or '없음'})")
            results = orchestrator.build_pipeline(store, stage_bots, input_text or "처리할 작업")
            bots.save_bots(store)
            self.ui.write_log(orchestrator.pipeline_summary(results))
        except Exception as e:
            self.ui.write_log(f"SYS: 파이프라인 실패: {str(e)[:100]}")

    def _handle_bot_eval_command(self, text: str) -> bool:
        """C3: '봇 평가 보여줘' → 봇별 평균 점수."""
        t = str(text or "")
        if "봇" not in t or "평가" not in t:
            return False
        try:
            from core import bot_eval
            avg = bot_eval.average_scores()
            if not avg:
                self.ui.write_log("SYS: 아직 평가 기록이 없습니다. 봇에게 질문하면 자동 평가됩니다.")
            else:
                self.ui.write_log("SYS: 📊 봇 평가 순위:")
                for bot_name, score in bot_eval.rank_bots({k: int(v) for k, v in avg.items()}):
                    self.ui.write_log(f"SYS:   {bot_name} — {score}점")
            return True
        except Exception:
            return False

    def _bot_chat_async(self, bot_a: str, bot_b: str, topic: str):
        try:
            from core import bots
            store = bots.load_bots()
            a = bots.get_bot(store, bot_a)
            b = bots.get_bot(store, bot_b)
            if a is None or b is None:
                self.ui.write_log("SYS: 대화할 봇을 찾지 못했습니다.")
                return
            self.ui.write_log(f"SYS: 🤖 {a['name']} ↔ {b['name']} 대화 시작 (주제: {topic or '자유'})...")
            transcript = bots.bot_dialogue(a, b, topic or "협업 주제")
            bots.save_bots(store)
            for turn in transcript:
                self.ui.write_log(f"🤖 {turn['bot']}: {turn['text']}")
            self.ui.write_log("SYS: 🤖 봇 협업 대화 완료.")
        except Exception as e:
            self.ui.write_log(f"SYS: 봇 대화 실패: {str(e)[:100]}")

    def _handle_minutes_command(self, text: str) -> bool:
        """회의록 작성. 처리했으면 True."""
        from core.minutes import extract_minutes_command
        if not extract_minutes_command(text):
            return False
        threading.Thread(target=self._minutes_async, daemon=True).start()
        return True

    def _minutes_async(self):
        try:
            from datetime import datetime
            from core import graph_store, minutes
            store = graph_store.load()
            turns = store.get("turns", [])
            if len(turns) < 2:
                self.ui.write_log("SYS: 회의록을 작성할 대화가 부족합니다.")
                return
            self.ui.write_log("SYS: 📋 회의록 작성 중...")
            md = None
            data = minutes.generate_minutes(turns)
            if data:
                md = minutes.minutes_to_markdown(data)
            else:
                md = minutes.local_minutes(turns)
            path = minutes.write_minutes(md)
            self.ui.write_log(f"SYS: ✅ 회의록 저장 완료 — {path}")
            # D3: Obsidian 자동 동기화
            try:
                from core import exporters
                synced = exporters.sync_to_obsidian(md, "회의록 " + datetime.now().strftime("%m%d-%H%M"))
                if synced:
                    self.ui.write_log(f"SYS: 📓 Obsidian 동기화 완료 — {synced}")
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 회의록 작성 실패: {str(e)[:100]}")

    def _handle_report_command(self, text: str) -> bool:
        """보고서 생성. 처리했으면 True."""
        from core.reporter import extract_report_command
        if not extract_report_command(text):
            return False
        threading.Thread(target=self._report_async, daemon=True).start()
        return True

    def _report_async(self):
        try:
            from datetime import datetime
            from core import graph_store, reporter
            store = graph_store.load()
            self.ui.write_log("SYS: 📄 보고서 생성 중...")
            md = None
            data = reporter.generate_report(store)
            if data:
                md = reporter.report_to_markdown(data)
            else:
                md = reporter.local_report(store)
            path = reporter.write_report(md)
            self.ui.write_log(f"SYS: ✅ 보고서 저장 완료 — {path}")
            # D3: Obsidian 자동 동기화
            try:
                from core import exporters
                synced = exporters.sync_to_obsidian(md, "보고서 " + datetime.now().strftime("%m%d-%H%M"), ["WEAID", "report"])
                if synced:
                    self.ui.write_log(f"SYS: 📓 Obsidian 동기화 완료 — {synced}")
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 보고서 생성 실패: {str(e)[:100]}")

    def _handle_alarm_command(self, text: str) -> bool:
        """알람/타이머 등록·목록·취소. 처리했으면 True."""
        try:
            from datetime import datetime, timedelta
            from core import alarms
            t = str(text or "")
            if any(k in t for k in ("알람 목록", "타이머 목록")):
                all_a = alarms.load_alarms()
                pending = [a for a in all_a if not a.get("fired")]
                if not pending:
                    self.ui.write_log("SYS: 예약된 알람/타이머가 없습니다.")
                else:
                    self.ui.write_log(f"SYS: ⏰ 알람 {len(pending)}건:")
                    for a in pending:
                        self.ui.write_log(f"SYS:   {alarms.alarm_description(a)}")
                return True
            m = re.search(r"(?:알람|타이머)\s*(?:취소|삭제|지워)\s*(?:해줘|줘)?\s*([0-9a-f]{6,8})", t)
            if m and ("취소" in t or "삭제" in t):
                if alarms.remove_alarm(m.group(1)):
                    self.ui.write_log(f"SYS: 🗑️ 알람 취소 완료 — {m.group(1)}")
                else:
                    self.ui.write_log(f"SYS: 해당 알람을 찾지 못했습니다 — {m.group(1)}")
                return True
            if any(k in t for k in ("알려", "타이머", "알람")):
                secs = alarms.parse_duration(t)
                if secs:
                    due = datetime.now() + timedelta(seconds=secs)
                    a = alarms.add_alarm(due, f"타이머 {secs}초")
                    self.ui.write_log(f"SYS: ⏰ 타이머 설정 완료 ({secs}초 후) — ID: {a['id']}")
                    return True
                due = alarms.parse_alarm_time(t)
                if due:
                    a = alarms.add_alarm(due, "알람")
                    self.ui.write_log(f"SYS: ⏰ 알람 설정 완료 ({due.strftime('%H:%M')}) — ID: {a['id']}")
                    return True
        except Exception as e:
            self.ui.write_log(f"SYS: 알람 처리 실패: {str(e)[:100]}")
        return False

    def _on_alarm_fire(self, alarm: dict):
        """알람 발화: 로그 + 음성 안내 + Windows 토스트(A2)."""
        label = str(alarm.get("label", "알람"))
        self.ui.write_log(f"SYS: 🔔 알람! — {label}")
        try:
            from core import applog, notify
            if notify.notify("WEAID 알람", label):
                applog.log_write(f"alarm fired: {label}", "INFO")
        except Exception:
            pass
        if not self._hard_stop.is_set() and self.session and self._loop:
            self.speak(
                "[SCHEDULED ALARM] The alarm is ringing. Announce it briefly in Korean: "
                + json.dumps(label, ensure_ascii=False)
            )

    def _handle_rag_command(self, text: str) -> bool:
        """B2: '내 문서에서 OO 찾아줘' → 로컬 RAG 검색."""
        try:
            import os
            from pathlib import Path
            from core import rag
            query = rag.extract_rag_command(text)
            if not query:
                return False
            docs_dir = os.environ.get("WEAID_DOCS_DIR") or str(Path.home() / "Documents")
            threading.Thread(target=self._rag_async, args=(query, docs_dir), daemon=True).start()
            return True
        except Exception:
            return False

    def _rag_async(self, query: str, docs_dir: str):
        try:
            from core import rag
            self.ui.write_log(f"SYS: 📚 문서 검색 중 — '{query}'")
            docs = rag.load_documents(docs_dir)
            if not docs:
                self.ui.write_log(f"SYS: 검색할 문서가 없습니다 ({docs_dir}).")
                return
            ctx = rag.context_for(query, docs)
            if not ctx:
                self.ui.write_log(f"SYS: '{query}' 관련 문서를 찾지 못했습니다.")
                return
            self.ui.write_log("SYS: 📚 " + ctx.replace(chr(10), chr(10) + "SYS:   "))
            if not self._hard_stop.is_set():
                self._speak_why_result(ctx[:500])
        except Exception as e:
            self.ui.write_log(f"SYS: 문서 검색 실패: {str(e)[:100]}")

    def _handle_agent_command(self, text: str) -> bool:
        """B3: '에이전트로 처리해줘: ...' → 계획-실행-검증 루프."""
        import re
        t = str(text or "")
        if "에이전트" not in t:
            return False
        m = re.search(r"(?:에이전트로\s*)?처리해줘[:：\s]*(.+)", t)
        if not m:
            return False
        goal = m.group(1).strip()
        threading.Thread(target=self._agent_async, args=(goal,), daemon=True).start()
        return True

    def _agent_async(self, goal: str):
        try:
            from core import agent_loop, llm
            self.ui.write_log(f"SYS: 🧭 에이전트 루프 시작 — 목표: {goal}")
            tools = ["web_search", "file_controller", "graph_query", "win_app_control"]

            def executor(instruction: str) -> str:
                return llm.generate(
                    "당신은 목표 지향 에이전트입니다. 지시에 따라 한 단계만 수행하고 결과를 요약하세요.",
                    instruction,
                    timeout_s=60,
                )

            out = agent_loop.run_loop(goal, tools, executor, max_iterations=3)
            self.ui.write_log(f"SYS: 🧭 루프 종료 — {'완료' if out['completed'] else '부분 완료'} ({out['iterations']}회)")
            self.ui.write_log(f"에이전트: {out['result']}")
        except Exception as e:
            self.ui.write_log(f"SYS: 에이전트 루프 실패: {str(e)[:100]}")

    def _handle_skill_command(self, text: str) -> bool:
        """C1: '봇 OO 스킬 [스킬명] [인자]' → 봇의 스킬 실행."""
        try:
            from core import bots, bot_skills
            m = re.search(r"봇\s+([^\s]+)\s+스킬", str(text or ""))
            if not m:
                return False
            bot_key = m.group(1)
            skill = bot_skills.extract_skill_command(text)
            if not skill:
                return False
            name, args = skill
            bot = bots.get_bot(bots.load_bots(), bot_key)
            if bot is None:
                self.ui.write_log(f"SYS: 봇을 찾지 못했습니다 — {bot_key}")
                return True
            if name not in [s["name"] for s in bot_skills.available_skills(bot)]:
                self.ui.write_log(f"SYS: {bot['name']} 봇에 '{name}' 스킬이 없습니다. 보유 스킬: {[s['name'] for s in bot_skills.available_skills(bot)]}")
                return True
            result = bot_skills.execute_skill(name, args)
            self.ui.write_log(f"⚙️ {bot['name']}·{name}: {result}")
            return True
        except Exception as e:
            self.ui.write_log(f"SYS: 스킬 실행 실패: {str(e)[:100]}")
            return False

    def _handle_market_command(self, text: str) -> bool:
        """주식/코인 브리핑 (공개 API)."""
        try:
            from core import market
            cmd = market.extract_market_command(text)
            if not cmd:
                return False
            threading.Thread(target=self._market_async, args=(cmd,), daemon=True).start()
            return True
        except Exception:
            return False

    def _market_async(self, cmd: dict):
        try:
            import urllib.request
            import json as _json
            from core import market
            quotes = []
            if cmd["kind"] == "crypto":
                for cg_id in cmd["symbols"]:
                    try:
                        with urllib.request.urlopen(
                            f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
                            timeout=15,
                        ) as resp:
                            data = _json.loads(resp.read().decode("utf-8"))
                        price = market.parse_coingecko(data, cg_id)
                        quotes.append({"name": cg_id, "price": price})
                    except Exception:
                        quotes.append({"name": cg_id, "price": None})
            elif cmd["kind"] == "stock":
                for sym in cmd["symbols"]:
                    try:
                        with urllib.request.urlopen(
                            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                            timeout=15,
                        ) as resp:
                            data = _json.loads(resp.read().decode("utf-8"))
                        price = market.parse_yahoo(data)
                        quotes.append({"name": sym, "price": price})
                    except Exception:
                        quotes.append({"name": sym, "price": None})
            briefing = market.format_briefing(quotes)
            self.ui.write_log(briefing)
            if not self._hard_stop.is_set():
                self._speak_why_result(briefing[:400])
        except Exception as e:
            self.ui.write_log(f"SYS: 시세 조회 실패: {str(e)[:100]}")

    def _handle_translate_command(self, text: str) -> bool:
        """실시간 통역: 마지막 발화 → 대상 언어 번역."""
        try:
            from core import translate
            target = translate.extract_translate_command(text)
            if not target:
                return False
            threading.Thread(target=self._translate_async, args=(target,), daemon=True).start()
            return True
        except Exception:
            return False

    def _translate_async(self, target_lang: str):
        try:
            from core import graph_store, translate
            store = graph_store.load()
            user_turns = [t for t in store.get("turns", []) if t.get("speaker") == "user"]
            if not user_turns:
                self.ui.write_log("SYS: 통역할 발화가 없습니다. 먼저 말씀해 주세요.")
                return
            source = user_turns[-1].get("text", "")
            self.ui.write_log(f"SYS: 🌐 {target_lang} 통역 중...")
            out = translate.translate_chain(source, target_lang)
            self.ui.write_log(f"🌐 [{target_lang}] {out['translated']}")
            if not self._hard_stop.is_set():
                self.speak(
                    "[INTERNAL TRANSLATION] Read the following translation aloud verbatim. "
                    + json.dumps(out["translated"], ensure_ascii=False)
                )
        except Exception as e:
            self.ui.write_log(f"SYS: 통역 실패: {str(e)[:100]}")

    def _handle_routine_command(self, text: str) -> bool:
        """워크플로우 루틴 실행/목록/정의/삭제."""
        try:
            from core import routines
            cmd, payload = routines.parse_routine_command(text)
            if cmd == "list":
                all_r = routines.load_routines()
                self.ui.write_log(f"SYS: 📋 루틴 {len(all_r)}개: {', '.join(all_r.keys())}")
                return True
            if cmd == "define":
                routines.define_routine(payload["name"], payload["steps"])
                self.ui.write_log(f"SYS: 📋 루틴 등록 완료 — {payload['name']} ({len(payload['steps'])}단계)")
                return True
            if cmd == "remove":
                ok = routines.remove_routine(payload["name"])
                self.ui.write_log(f"SYS: 🗑️ 루틴 삭제 {'완료' if ok else '실패(없음)'} — {payload['name']}")
                return True
            if cmd == "run":
                steps = routines.expand_routine(routines.load_routines(), payload["name"])
                if not steps:
                    self.ui.write_log(f"SYS: '{payload['name']}' 루틴을 찾지 못했습니다.")
                    return True
                threading.Thread(target=self._routine_async, args=(payload["name"], steps), daemon=True).start()
                return True
        except Exception as e:
            self.ui.write_log(f"SYS: 루틴 처리 실패: {str(e)[:100]}")
        return False

    def _routine_async(self, name: str, steps: list):
        import time as _time
        try:
            self.ui.write_log(f"SYS: 🔁 '{name}' 루틴 시작 ({len(steps)}단계)")
            for i, step in enumerate(steps, 1):
                if self._shutdown_requested.is_set():
                    break
                self.ui.write_log(f"SYS:   [{i}/{len(steps)}] {step}")
                if self.session and self._loop and not self._hard_stop.is_set():
                    self.speak(f"[ROUTINE STEP {i}/{len(steps)}] Perform this step now: " + json.dumps(step, ensure_ascii=False))
                _time.sleep(2)
            self.ui.write_log(f"SYS: ✅ '{name}' 루틴 완료.")
        except Exception as e:
            self.ui.write_log(f"SYS: 루틴 실행 실패: {str(e)[:100]}")

    def _handle_language_command(self, text: str) -> bool:
        """다국어: '영어로 바꿔줘' / '한국어로 바꿔줘'."""
        try:
            from core import i18n, settings
            m = re.search(r"(영어|한국어)로\s*(?:바꿔|변경|설정)(?:줘|해줘)?", str(text or ""))
            if not m:
                return False
            lang = i18n.normalize_lang(m.group(1))
            settings.set_setting("language", lang)
            self.ui.write_log("SYS: " + ("언어가 영어로 변경되었습니다." if lang == "en" else "언어가 한국어로 변경되었습니다."))
            return True
        except Exception:
            return False

    def _handle_hologram_command(self, text: str) -> bool:
        """'홀로그램 켜줘/꺼줘' 로컬 명령 처리. 처리했으면 True."""
        t = str(text or "")
        if "홀로그램" not in t:
            return False
        try:
            if "꺼" in t:
                self.ui.set_hologram(False)
                self.ui.write_log("SYS: 🧿 홀로그램 꺼짐.")
            elif "켜" in t:
                self.ui.set_hologram(True)
                self.ui.write_log("SYS: 🧿 홀로그램 켜짐.")
            else:
                on = self.ui.toggle_hologram()
                self.ui.write_log(f"SYS: 🧿 홀로그램 {'켜짐' if on else '꺼짐'}.")
            return True
        except Exception:
            return False

    def _handle_schedule_command(self, text: str) -> bool:
        """예약 명령 처리: 추가/목록/취소. 처리했으면 True."""
        try:
            from core import scheduler

            cmd, payload = scheduler.parse_command(text)
            if cmd == "add":
                task = scheduler.add_task(payload["spec"], payload["action"])
                self.ui.write_log(f"SYS: ⏰ 예약 등록 — {scheduler.task_description(task)} (ID: {task['id']})")
                return True
            if cmd == "list":
                tasks = scheduler.load_tasks()
                if not tasks:
                    self.ui.write_log("SYS: 등록된 예약 작업이 없습니다.")
                else:
                    self.ui.write_log(f"SYS: 📅 예약 작업 {len(tasks)}건:")
                    for t in tasks:
                        self.ui.write_log(f"SYS:   [{t['id']}] {scheduler.task_description(t)}")
                return True
            if cmd == "remove":
                tid = payload.get("task_id", "").strip()
                if not tid:
                    self.ui.write_log("SYS: 취소할 예약 ID를 함께 말해주세요 (예: '예약 취소 ab12cd34').")
                elif scheduler.remove_task(tid):
                    self.ui.write_log(f"SYS: 🗑️ 예약 취소 완료 — {tid}")
                else:
                    self.ui.write_log(f"SYS: 해당 ID의 예약을 찾지 못했습니다 — {tid}")
                return True
        except Exception as e:
            self.ui.write_log(f"SYS: 예약 처리 실패: {str(e)[:100]}")
        return False

    def _on_proactive_item(self, item: dict):
        """프로액티브 항목 발생: 로그 + 예약 작업이면 실행 지시 전달."""
        text = str(item.get("text", ""))
        self.ui.write_log(f"SYS: {text}")
        if item.get("type") == "scheduled":
            action = str(item.get("action_text", "")).strip()
            if action and not self._hard_stop.is_set() and self.session and self._loop:
                directive = (
                    "[SCHEDULED TASK] A scheduled task is due now. Perform it immediately: "
                    + json.dumps(action, ensure_ascii=False)
                )
                self.speak(directive)

    def _handle_why_query(self, term: str) -> bool:
        """'왜?' 인과 추적: 그래프의 원인 체인을 역추적해 음성+시각으로 설명.

        체인을 찾으면 True (질문을 직접 처리), 못 찾으면 False (모델에 위임).
        """
        try:
            import tempfile
            from pathlib import Path
            from core import graph_store

            store = graph_store.load()
            target = (term or "").strip()
            if not target:
                # 대상 생략 → 최근 트리플의 주어 사용
                if store.get("triples"):
                    target = store["triples"][-1].get("subject", "")
                if not target:
                    return False
            chain = graph_store.trace_causal(store, target)
            if not chain:
                return False  # 매치 없음 → 일반 질문으로 모델에 위임
            explanation = graph_store.chain_to_korean(chain)
            self.ui.write_log(f"SYS: 🔗 인과 추적 — {explanation}")
            # 뷰어 포커스: 인과 경로 하이라이트
            focus = graph_store.causal_focus(store, target)
            if focus:
                try:
                    fp = Path(tempfile.gettempdir()) / "weaid_mindmap_focus.json"
                    fp.write_text(json.dumps(focus, ensure_ascii=False), encoding="utf-8")
                    self.ui.open_ontology()
                except Exception:
                    pass
            # 음성 설명 (STOP 상태가 아니면)
            if not self._hard_stop.is_set():
                self._speak_why_result(explanation)
            return True
        except Exception as e:
            self.ui.write_log(f"SYS: 인과 추적 실패: {str(e)[:100]}")
            return False

    def _speak_why_result(self, text: str) -> bool:
        """인과 설명을 음성으로 읽어준다 (비전 결과와 동일한 방식)."""
        directive = (
            "[INTERNAL CAUSAL RESULT] Read the following causal explanation to the user "
            "verbatim in Korean. Do not add an introduction, commentary, or a tool call. "
            f"Explanation: {json.dumps(text, ensure_ascii=False)}"
        )
        return self.speak(directive)

    def _constitutional_review_async(self, question: str, answer: str):
        """헌법적 AI 후검토: 자기 비평 → 수정 답변 → RLAIF 선호 기록."""
        try:
            from core.constitution import review_response, record_rlaif

            review = review_response(question, answer)
            score = int(review.get("score", 100))
            issues = list(review.get("issues") or [])
            revised = str(review.get("revised") or "").strip()
            checks = review.get("checks") or []

            if issues and revised:
                self.ui.write_log(f"SYS: ⚖️ 헌법 검사 {score}/100 — 원칙 위반 발견, 수정 답변을 반영했습니다.")
                self.ui.write_log(f"AID(헌법 수정): {revised}")
                record_rlaif(question, answer, revised, "revised", score, issues)
            else:
                self.ui.write_log(f"SYS: ⚖️ 헌법 검사 {score}/100 — 원칙 준수.")
                record_rlaif(question, answer, "", "original", score, issues)

            try:
                self.ui.set_constitution_review({
                    "score": score,
                    "compliant": bool(review.get("compliant", True)),
                    "checks": checks,
                    "issues": issues,
                    "revised": revised,
                })
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 헌법 검사 실패: {str(e)[:120]}")

    def _evolve_async(self):
        """헌법 진화 루프 (백그라운드): RLAIF 분석 → 보완 조항 반영."""
        try:
            from core.constitution_evolver import maybe_evolve
            result = maybe_evolve(force=False)
            if result and result.get("applied"):
                self.ui.write_log(
                    f"SYS: 🧬 헌법 진화 — 보완 조항 {len(result['applied'])}개 적용. 사유: {result.get('reason', '')[:90]}"
                )
                for a in result["applied"]:
                    self.ui.write_log(f"SYS:   보완: {a}")
        except Exception as e:
            self.ui.write_log(f"SYS: 헌법 진화 실패: {str(e)[:100]}")

    def _summarize_async(self):
        """대화 요약 노드 생성 (백그라운드): LLM 요약 → 그래프 병합 → 뷰어 갱신."""
        try:
            from core import graph_store, summarizer

            store = graph_store.load()
            summary = summarizer.summarize_conversation(store)
            graph_store.merge_summary(store, summary)
            graph_store.save(store)
            graph_store.publish(store)
            self.ui.write_log(
                f"SYS: 📝 대화 요약 노드 생성 — {summary.get('start_turn')}~{summary.get('end_turn')}턴"
            )
            # A2: 요약 완료 토스트
            try:
                from core import applog, notify, settings
                if settings.get("notifications_on"):
                    notify.notify("WEAID 요약", f"{summary.get('start_turn')}~{summary.get('end_turn')}턴 대화 요약 완료")
                applog.log_write("conversation summarized", "INFO")
            except Exception:
                pass
            try:
                self.ui.set_mindmap_data({
                    "question": store.get("turns", [{}])[-1].get("text", "") if store.get("turns") else "",
                    "answer": "",
                    "topics": store["topics"],
                    "triples": store["triples"],
                    "entities": store["entities"],
                    "relations": store["relations"],
                    "turns": store["turns"],
                    "summaries": store.get("summaries", []),
                })
            except Exception:
                pass
        except Exception as e:
            self.ui.write_log(f"SYS: 요약 생성 실패: {str(e)[:100]}")

    def _insight_async(self, question: str, answer: str):
        """누적 대화 그래프 파이프라인: 즉시 병합(휴리스틱) → 딥 병합(LLM SKD)."""
        # 스로틀링: 이전 딥 분석이 아직 실행 중이면 건너뛴다 (API 쿼터 경합 방지)
        if self._insight_busy:
            return
        self._insight_busy = True
        try:
            from core import graph_store

            store = graph_store.load()
            # Phase 1 — 즉시 반영: 로컬 휴리스틱 S-P-O로 바로 그래프에 병합
            try:
                from core.mindmap import _heuristic
                heur = _heuristic(question, answer)
                graph_store.merge_turn(store, question, answer, heur_triples=heur.get("triples"))
                graph_store.save(store)
                graph_store.publish(store)
                self.ui.write_log("SYS: 🧠 대화 그래프 즉시 갱신 완료.")
            except Exception as e:
                self.ui.write_log(f"SYS: 그래프 즉시 병합 실패: {str(e)[:100]}")

            # Phase 2 — 딥 분석: SKD 3계층 + 인과관계 + 헌법 검토 (Gemini→Grok 폴백)
            # 스로틀링: 답변 직후 1.5초 대기 → 라이브 응답과 API 경합 완화
            time.sleep(1.5)
            from core.constitution import record_rlaif
            from core.insight import build_insight

            insight = build_insight(question, answer)
            review = insight.get("review") or {}
            score = int(review.get("score", 100))
            issues = list(review.get("issues") or [])
            revised = str(review.get("revised") or "").strip()

            if issues and revised:
                try:
                    from core.text_cleaner import clean_text
                    revised = clean_text(revised)
                except Exception:
                    pass
                self.ui.write_log(f"SYS: ⚖️ 헌법 검사 {score}/100 — 수정 답변을 반영했습니다.")
                self.ui.write_log(f"AID(헌법 수정): {revised}")
                record_rlaif(question, answer, revised, "revised", score, issues)
                # A/B 평가 기록 (원본 vs 수정)
                try:
                    from core import ab_eval
                    cmp = ab_eval.compare_responses(question, answer, revised)
                    ab_eval.record_comparison(cmp)
                    self.ui.write_log(f"SYS: 🔬 A/B 평가 — {cmp['winner']} 승리 (마진 {cmp['margin']})")
                except Exception:
                    pass
            else:
                self.ui.write_log(f"SYS: ⚖️ 헌법 검사 {score}/100 — 원칙 준수.")
                record_rlaif(question, answer, "", "original", score, issues)

            try:
                self.ui.set_constitution_review({
                    "score": score,
                    "compliant": bool(review.get("compliant", True)),
                    "checks": review.get("checks") or [],
                    "issues": issues,
                    "revised": revised,
                })
            except Exception:
                pass

            # Phase 2 병합: 누적 그래프에 SKD 결과 반영 → 뷰어 갱신
            store = graph_store.load()
            graph_store.merge_insight(store, insight)
            graph_store.save(store)
            graph_store.publish(store)
            # B1: 장기 기억 추출·저장
            try:
                from core import long_memory
                entries = long_memory.load_memory()
                for cand in long_memory.extract_memory_candidates(question, answer):
                    long_memory.add_memory(entries, cand)
                long_memory.save_memory(entries)
            except Exception:
                pass
            # HUD 중앙에 구조화된 S-P-O 조립 애니메이션 표시
            try:
                self.ui.show_hud_spo_triples(insight.get("triples") or [])
            except Exception:
                pass
            # 대화 요약 노드: N턴마다 자동 압축 (백그라운드)
            try:
                if graph_store.needs_summary(store):
                    threading.Thread(target=self._summarize_async, daemon=True).start()
            except Exception:
                pass
            # 헌법 진화 루프: RLAIF 축적 시 보완 조항 자동 반영 (백그라운드)
            try:
                threading.Thread(target=self._evolve_async, daemon=True).start()
            except Exception:
                pass
            self.ui.set_mindmap_data({
                "question": insight.get("question", question),
                "answer": insight.get("answer", answer),
                "topics": store["topics"],
                "triples": store["triples"],
                "entities": store["entities"],
                "relations": store["relations"],
                "turns": store["turns"],
                "summaries": store.get("summaries", []),
            })
            self.ui.write_log(f"SYS: 🧠 딥 그래프 갱신 완료 ({graph_store.stats(store)}).")
        except Exception as e:
            try:
                from core import applog
                applog.ErrorCollector.record(str(e), "insight")
            except Exception:
                pass
            self.ui.write_log(f"SYS: 인사이트 처리 실패: {str(e)[:120]}")
        finally:
            self._insight_busy = False

    @staticmethod
    def _is_explicit_self_quit_transcript(text: str) -> bool:
        """Only match commands that clearly target JARVIS, never the computer."""
        normalized = " ".join(str(text or "").lower().split())
        if not normalized:
            return False
        if re.search(r"\b(?:computer|mac|pc|system|machine)\b", normalized):
            return False
        if re.search(r"\b(?:stop talking|be quiet|cancel|never mind)\b", normalized):
            return False
        if normalized in {
            "quit", "exit", "shutdown", "shut down", "turn off", "power down",
            "go offline", "goodbye jarvis", "goodbye jarvis please",
            "goodbye aid", "goodbye aesuni", "goodbye 에이드", "goodbye 애순이",
        }:
            return True
        return any(pattern.search(normalized) for pattern in _SELF_QUIT_PATTERNS)

    def _queue_self_quit_after_farewell(self) -> None:
        """Arm shutdown without closing until the response audio is fully drained."""
        self._pending_self_quit = True
        self._pending_self_quit_farewell_received = False
        try:
            self.ui.write_log(f"SYS: Shutdown queued; waiting for {_assistant_name_for_voice(self._get_current_voice())}'s farewell.")
        except Exception:
            pass
        # A voice model can occasionally omit audio/turn_complete. Do not
        # leave the user with a permanently armed shutdown in that case.
        try:
            if self._self_quit_timer is not None:
                self._self_quit_timer.cancel()
            self._self_quit_timer = threading.Timer(8.0, self._force_complete_self_quit)
            self._self_quit_timer.daemon = True
            self._self_quit_timer.start()
        except Exception:
            pass

    def _force_complete_self_quit(self) -> None:
        if not getattr(self, "_pending_self_quit", False):
            return
        self._pending_self_quit_farewell_received = True
        self._complete_self_quit_after_audio()

    def _mark_self_quit_farewell_received(self) -> None:
        if getattr(self, "_pending_self_quit", False):
            self._pending_self_quit_farewell_received = True

    def _complete_self_quit_after_audio(self) -> bool:
        """Close through the UI only after a farewell turn has actually completed."""
        if not (
            getattr(self, "_pending_self_quit", False)
            and getattr(self, "_pending_self_quit_farewell_received", False)
        ):
            return False
        self._pending_self_quit = False
        self._pending_self_quit_farewell_received = False
        if getattr(self, "_self_quit_timer", None) is not None:
            self._self_quit_timer.cancel()
            self._self_quit_timer = None
        self.request_shutdown()
        self.ui.handle_ui_command("Quit AID")
        return True

    def request_shutdown(self) -> None:
        """Stop live tasks and make the process exit after the UI closes."""
        shutdown_requested = getattr(self, "_shutdown_requested", None)
        if shutdown_requested is None:
            self._shutdown_requested = threading.Event()
            shutdown_requested = self._shutdown_requested
        if shutdown_requested.is_set():
            return
        shutdown_requested.set()
        try:
            if self._wake_detector is not None:
                self._wake_detector.stop()
        except Exception:
            pass
        try:
            if self._proactive_engine is not None:
                self._proactive_engine.stop()
        except Exception:
            pass
        try:
            if self._alarm_manager is not None:
                self._alarm_manager.stop()
        except Exception:
            pass
        try:
            session = getattr(self, "session", None)
            loop = getattr(self, "_loop", None)
            if session is not None and loop is not None:
                asyncio.run_coroutine_threadsafe(session.close(), loop)
            out_queue = getattr(self, "out_queue", None)
            if out_queue is not None:
                out_queue.put_nowait(None)
        except Exception as exc:
            print(f"[AID] ⚠️ Shutdown session close failed: {exc}")

    def set_tour_active(self, active: bool) -> None:
        """Track whether the desktop introduction temporarily owns the UI."""
        self._tour_active = bool(active)

    async def _wait_before_reconnect(self, delay: float) -> None:
        deadline = time.monotonic() + max(0.0, float(delay))
        while not self._shutdown_requested.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))

    def _intercept_ui_tool_call(self, name: str, args: dict) -> str | None:
        """Safety net for stale models that attempt the removed quit tool action."""
        action = str(args.get("action") or "").strip().lower()
        if name != "shutdown_jarvis" and not (
            name == "jarvis_ui_control" and action == "quit_jarvis"
        ):
            return None

        transcript = str(getattr(self, "_current_input_transcript", "") or "")
        if not transcript:
            age = time.monotonic() - float(getattr(self, "_last_input_transcript_at", 0.0) or 0.0)
            if age <= 5.0:
                transcript = str(getattr(self, "_last_input_transcript", "") or "")

        if not self._is_explicit_self_quit_transcript(transcript):
            return f"Ignored an unverified shutdown request. {_assistant_name_for_voice(self._get_current_voice())} remains online."

        self._queue_self_quit_after_farewell()
        return f'Shutdown queued. Say exactly: "{_self_quit_goodbye(self._get_current_voice())}"'

    def update_voice(self, voice_name: str):
        self.voice_name = _normalize_voice_name(voice_name)
        self.ui.write_log(f"SYS: Voice change requested: {self.voice_name}")
        try:
            self.ui.sync_voice_display(self.voice_name)
        except Exception:
            pass
        if self.session and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self.session.close(), self._loop)
            except Exception as e:
                print(f"[AID] ⚠️ Could not close session after voice change: {e}")

    def _get_current_voice(self) -> str:
        if getattr(self, "voice_name", None):
            return _normalize_voice_name(self.voice_name)
        voice_combo = getattr(self.ui, "_voice_combo", None)
        if voice_combo is not None:
            idx = voice_combo.currentIndex()
            if idx >= 0:
                voice = voice_combo.itemData(idx)
                if isinstance(voice, str) and voice:
                    return _normalize_voice_name(voice)
            voice = voice_combo.currentText().strip().lower()
            if voice in SUPPORTED_VOICE_NAMES:
                return voice
        return _load_voice_name()

    async def _announce_startup(self):
        try:
            memory = load_memory()
            name_entry = memory.get("identity", {}).get("name")
            name = None
            if isinstance(name_entry, dict):
                name = name_entry.get("value")
            elif isinstance(name_entry, str):
                name = name_entry
            latin, _ = _assistant_identity(self._get_current_voice())
            if name:
                greeting = f"{latin}. At your service, {name}. What would you like to accomplish today?"
            else:
                greeting = f"{latin}. At your service, Sir or Madam. What would you like to accomplish today?"
            await self.session.send_client_content(
                turns={"parts": [{"text": greeting}]},
                turn_complete=True,
            )
        except Exception as e:
            print(f"[AID] ⚠️ Greeting failed: {e}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt(self._get_current_voice())

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        # B1: 장기 기억 컨텍스트 주입 (다이어트: 상위 5개만)
        try:
            from core import long_memory, tone
            lm_ctx = long_memory.memory_context(long_memory.load_memory(), limit=5)
            if lm_ctx:
                parts.append(lm_ctx)
            if getattr(self, "_last_tone", "neutral") != "neutral":
                tg = tone.tone_guidance(self._last_tone)
                if tg:
                    parts.append(tg)
        except Exception:
            pass
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{
                "function_declarations": [
                    t for t in getattr(self, "tool_declarations", TOOL_DECLARATIONS)
                    if t.get("name") in CORE_TOOL_NAMES
                ]
            }],
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    silence_duration_ms=LIVE_VAD_SILENCE_MS,
                )
            ),
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._get_current_voice()
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        if getattr(self, "cloud_safe", False) and name not in CLOUD_SAFE_ACTIONS:
            return types.FunctionResponse(
                id=fc.id,
                name=name,
                response={
                    "result": (
                        f"Tool '{name}' is unavailable in cloud-safe mode."
                    )
                },
            )

        if getattr(self.ui, "operational_ready", True) is False:
            return types.FunctionResponse(
                id=fc.id,
                name=name,
                response={"result": "Startup sequence active. Try this action again when the assistant is ready."},
            )

        from core.qa_mode import guard_tool_call, qa_block_message

        qa_decision = guard_tool_call(name, args)
        if not qa_decision.allowed:
            return types.FunctionResponse(
                id=fc.id,
                name=name,
                response={"result": qa_block_message(qa_decision)},
            )

        print(f"[AID] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        intercepted = self._intercept_ui_tool_call(name, args)
        if intercepted is not None:
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": intercepted}
            )

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        result = "Done."

        try:
            if name == "open_app":
                r = await asyncio.to_thread(lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "win_app_control":
                from actions.win_app_control import win_app_control
                r = await asyncio.to_thread(win_app_control, **args)
                result = r or "Win app control done."

            elif name == "weather_report":
                r = await asyncio.to_thread(lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await asyncio.to_thread(lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                if (
                    args.get("action", "").lower() == "open"
                    and not args.get("path")
                    and not args.get("name")
                ):
                    current_file = getattr(self.ui, "current_file", None)
                    if current_file:
                        args["path"] = current_file
                r = await asyncio.to_thread(lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "check_messages":
                r = await asyncio.to_thread(
                    lambda: check_messages(parameters=args, response=None, player=self.ui, session_memory=None),
                )
                result = r or "No readable messages were found."

            elif name == "prepare_message_reply":
                r = await asyncio.to_thread(
                    lambda: prepare_message_reply(parameters=args, response=None, player=self.ui, session_memory=None),
                )
                result = r or "The message draft could not be prepared."

            elif name == "send_message":
                r = await asyncio.to_thread(lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "email_control":
                if args.get("action", "").lower() == "connect" and not args.get("credentials_path"):
                    current_file = getattr(self.ui, "current_file", None)
                    if current_file and Path(str(current_file)).suffix.lower() == ".json":
                        args["credentials_path"] = current_file
                r = await asyncio.to_thread(lambda: email_control(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or "Email action completed."

            elif name == "reminder":
                r = await asyncio.to_thread(lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await asyncio.to_thread(lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "media_control":
                r = await asyncio.to_thread(lambda: media_control(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None,
                            "speak": self._speak_vision_result},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await asyncio.to_thread(lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await asyncio.to_thread(lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await asyncio.to_thread(lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await asyncio.to_thread(lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(
                    goal=args.get("goal", ""),
                    priority=priority,
                    speak=self.speak,
                    immediate=True,
                )
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await asyncio.to_thread(lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await asyncio.to_thread(
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await asyncio.to_thread(lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await asyncio.to_thread(lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await asyncio.to_thread(lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "graphics_quality":
                quality = str(args.get("quality") or "").strip().lower()
                if quality not in {"low", "medium", "high"}:
                    raise ValueError("Graphics quality must be low, medium, or high.")
                self.ui.set_graphics_quality(quality)
                result = f"{_assistant_name_for_voice(self._get_current_voice())} graphics quality changed to {quality}."

            elif name == "jarvis_ui_control":
                action = str(args.get("action") or "").strip().lower()
                if action == "change_theme":
                    theme = str(args.get("theme") or "").strip().lower()
                    allowed = {"arc_reactor", "stealth_red", "vibranium_purple", "nanotech_gold", "platinum"}
                    if theme not in allowed:
                        raise ValueError(f"Unknown theme: {theme or 'missing'}")
                    self.ui.set_theme(theme)
                    result = f"{_assistant_name_for_voice(self._get_current_voice())} theme changed to {theme.replace('_', ' ')}."
                elif action == "change_graphics_quality":
                    quality = str(args.get("graphics_quality") or "").strip().lower()
                    if quality not in {"low", "medium", "high"}:
                        raise ValueError(f"Unknown graphics quality: {quality or 'missing'}")
                    self.ui.set_graphics_quality(quality)
                    result = f"{_assistant_name_for_voice(self._get_current_voice())} graphics quality changed to {quality}."
                else:
                    self.ui.handle_ui_command(action)
                    result = f"{_assistant_name_for_voice(self._get_current_voice())} interface action completed: {action.replace('_', ' ')}."

            elif name == "deep_research":
                r = request_deep_research(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Deep research preference requested."

            elif name == "create_presentation":
                current_file = getattr(self.ui, "current_file", None)
                supported_sources = {
                    ".txt", ".md", ".rst", ".csv", ".json", ".jsonl", ".docx", ".pptx",
                    ".pdf", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp",
                    ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".avi", ".webm",
                }
                if (
                    not args.get("source_file")
                    and not args.get("source_files")
                    and current_file
                    and Path(str(current_file)).suffix.lower() in supported_sources
                ):
                    args["source_files"] = [current_file]
                r = request_presentation(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Presentation preference requested."

            elif name == "task_status":
                from agent.task_queue import get_queue

                queue = get_queue()
                action = str(args.get("action") or "get").lower()
                task_id = str(args.get("task_id") or "").strip()
                if action == "all" or not task_id:
                    result = json.dumps(queue.get_all_statuses(), ensure_ascii=False)
                elif action == "cancel":
                    result = f"Task {task_id} cancelled." if queue.cancel(task_id) else f"Task {task_id} could not be cancelled."
                else:
                    status = queue.get_status(task_id)
                    result = json.dumps(status, ensure_ascii=False) if status else f"Task {task_id} was not found."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[AID] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _execute_tool_batch(self, calls):
        """Run read-only calls concurrently while preserving mutation order."""
        mutating = {
            "send_message", "prepare_message_reply", "email_control", "reminder",
            "computer_settings", "computer_control", "desktop_control", "file_controller",
            "file_processor", "code_helper", "dev_agent", "game_updater",
            "create_presentation", "save_memory", "jarvis_ui_control", "graphics_quality",
        }
        call_list = list(calls or [])
        if any(getattr(call, "name", "") in mutating for call in call_list):
            return [await self._execute_tool(call) for call in call_list]
        return list(await asyncio.gather(*(self._execute_tool(call) for call in call_list)))

    async def _send_realtime(self):
        while True:
            if self._shutdown_requested.is_set():
                return
            msg = await self.out_queue.get()
            if msg is None or self._shutdown_requested.is_set():
                return
            if self._hard_stop.is_set():
                # 헌법 STOP: API로 오디오를 전송하지 않는다 (호출 전 중단).
                continue
            # 화자 식별용 마이크 버퍼 축적 (최근 ~5초)
            try:
                import numpy as np
                raw = msg.get("data")
                if isinstance(raw, (bytes, bytearray)) and len(raw) >= 64:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    self._mic_audio.append(arr)
                    total = sum(len(a) for a in self._mic_audio)
                    while total > 5 * SEND_SAMPLE_RATE and self._mic_audio:
                        total -= len(self._mic_audio.pop(0))
            except Exception:
                pass
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[AID] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted and not self._hard_stop.is_set():
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[AID] 🎤 Mic stream open")
                while not self._shutdown_requested.is_set():
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[AID] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[AID] 👂 Recv started")
        out_buf, in_buf = [], []
        _new_turn = True
        turn_had_audio = False

        try:
            while True:
                if self._shutdown_requested.is_set():
                    return
                async for response in self.session.receive():

                    response_audio = _live_response_audio_bytes(response)
                    if response_audio:
                        turn_had_audio = True
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response_audio)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)
                                self._interrupted_text = " ".join(out_buf)
                                if not self.ui.muted:
                                    if _new_turn:
                                        self.ui.clear_subtitle()
                                        _new_turn = False
                                    self.ui.show_subtitle(txt)
                                self.ui.show_hud_spo(self._interrupted_text)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                if not in_buf:
                                    self._current_input_transcript = ""
                                in_buf.append(txt)
                                self._current_input_transcript = " ".join(in_buf).strip()
                                # 헌법 STOP/재개/새 세션/그래프 탐색/인과 추적: 전사 도중 즉시 감지한다.
                                try:
                                    from core.constitution import (
                                        is_stop_utterance, is_resume_utterance,
                                        is_new_session_utterance, extract_graph_query, extract_why_query,
                                    )
                                    if is_stop_utterance(self._current_input_transcript):
                                        self._handle_stop_command()
                                        in_buf = []
                                    elif is_resume_utterance(self._current_input_transcript):
                                        self._handle_resume_command()
                                        in_buf = []
                                    elif is_new_session_utterance(self._current_input_transcript):
                                        self._reset_session()
                                        in_buf = []
                                    else:
                                        _gq = extract_graph_query(self._current_input_transcript)
                                        if _gq:
                                            self._handle_graph_query(_gq)
                                            in_buf = []
                                        elif self._handle_schedule_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_hologram_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_name_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_bot_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_minutes_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_report_command(self._current_input_transcript):
                                            in_buf = []
                                        elif self._handle_alarm_command(self._current_input_transcript):
                                            in_buf = []
                                        else:
                                            _wq = extract_why_query(self._current_input_transcript)
                                            if _wq is not None and self._handle_why_query(_wq):
                                                in_buf = []
                                except Exception:
                                    pass
                                if (
                                    not getattr(self, "_pending_self_quit", False)
                                    and self._is_explicit_self_quit_transcript(self._current_input_transcript)
                                ):
                                    self._queue_self_quit_after_farewell()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._current_input_transcript = full_in
                                self._last_input_transcript = full_in
                                self._last_input_transcript_at = time.monotonic()
                                # 화자 식별 (백그라운드)
                                try:
                                    threading.Thread(target=self._analyze_speaker_async, daemon=True).start()
                                except Exception:
                                    pass
                                if (
                                    not getattr(self, "_pending_self_quit", False)
                                    and self._is_explicit_self_quit_transcript(full_in)
                                ):
                                    self._queue_self_quit_after_farewell()
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                # 무형 유니코드/워터마크 문자 정화 (watermarks-remover Layer A)
                                try:
                                    from core.text_cleaner import clean_text
                                    full_out = clean_text(full_out)
                                except Exception:
                                    pass
                                self.ui.write_log(f"{_assistant_name_for_voice(self._get_current_voice())}: {full_out}")
                                _q = full_in or getattr(self, "_last_input_transcript", "")
                                if _q:
                                    self.ui.set_last_exchange(_q, full_out)
                                # 원콜 인사이트: 마인드맵 추출 + 헌법 검토를 API 1회로 동시 처리
                                try:
                                    threading.Thread(
                                        target=self._insight_async,
                                        args=(_q, full_out),
                                        daemon=True,
                                    ).start()
                                except Exception:
                                    pass
                            if (
                                getattr(self, "_pending_self_quit", False)
                                and (full_out or turn_had_audio)
                            ):
                                self._mark_self_quit_farewell_received()
                                
                            out_buf = []
                            turn_had_audio = False
                            _new_turn = True

                    if response.tool_call:
                        function_calls = list(response.tool_call.function_calls)
                        for fc in function_calls:
                            print(f"[AID] 📞 {fc.name}")
                        fn_responses = await self._execute_tool_batch(function_calls)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                    if self._shutdown_requested.is_set():
                        return
        except Exception as e:
            if isinstance(e, genai.errors.APIError) and "1000" in str(e):
                print("[AID] 🔌 Session closed normally.")
                return
            print(f"[AID] ❌ Recv: {e}")
            traceback.print_exc()
            raise



    async def _play_audio(self):
        print("[AID] 🔊 Play started")

        stream = None
        if not self.external_audio:
            try:
                stream = sd.RawOutputStream(
                    samplerate=RECEIVE_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                )
                stream.start()
            except Exception as e:
                # 오디오 드라이버가 없거나 실패해도 앱은 계속 동작한다 (무음 모드).
                print(f"[AID] ⚠️ 오디오 출력 장치 없음: {e}")
                try:
                    self.ui.write_log("SYS: 오디오 출력 장치를 찾을 수 없어 음성 재생을 건너뜁니다. (대화는 계속됩니다)")
                    self.ui.set_audio_status("silent")
                except Exception:
                    pass
                stream = None

        try:
            while True:
                if self._shutdown_requested.is_set():
                    return
                if self._hard_stop.is_set():
                    # 헌법 STOP: 재생 대기열을 비우고 아무 소리도 내지 않는다.
                    try:
                        while not self.audio_in_queue.empty():
                            self.audio_in_queue.get_nowait()
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
                    continue
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                        if self._complete_self_quit_after_audio():
                            return
                    continue
                # Skip Gemini audio when external TTS is active
                if self._tts_engine and self._ext_tts_provider and self._ext_tts_provider != "gemini":
                    pass  # drain silently
                else:
                    self.set_speaking(True)
                    if self.external_audio:
                        send_audio = getattr(self.client, "send_audio", None)
                        if callable(send_audio):
                            send_audio(chunk, f"audio/pcm;rate={RECEIVE_SAMPLE_RATE}")
                    elif stream is not None:
                        try:
                            await asyncio.to_thread(stream.write, chunk)
                        except Exception as e:
                            # 오디오 드라이버가 중간에 사라져도 앱은 유지 (무음 + 자동 복구)
                            print(f"[AID] ⚠️ 오디오 재생 오류: {e}")
                            try:
                                stream.close()
                            except Exception:
                                pass
                            stream = None
                            try:
                                from core import audio_health
                                self._audio_retry = audio_health.RetryPolicy(base_seconds=15.0)
                                self._audio_retry.on_failure()
                            except Exception:
                                pass
                            try:
                                self.ui.write_log("SYS: 오디오 장치 손실 — 무음 모드로 전환, 자동 복구를 시도합니다.")
                                self.ui.set_audio_status("silent")
                            except Exception:
                                pass
                    elif self._audio_retry is not None and self._audio_retry.should_retry():
                        # A4: 지수 백오프 후 오디오 스트림 재생성 시도
                        try:
                            import sounddevice as _sd
                            stream = _sd.RawOutputStream(
                                samplerate=RECEIVE_SAMPLE_RATE,
                                channels=CHANNELS,
                                dtype="int16",
                                blocksize=CHUNK_SIZE,
                            )
                            stream.start()
                            self._audio_retry.on_success()
                            self._audio_retry = None
                            self.ui.write_log("SYS: 🔊 오디오 장치 복구 — 음성 재생을 재개합니다.")
                            self.ui.set_audio_status("ok")
                            self.set_speaking(True)
                            await asyncio.to_thread(stream.write, chunk)
                        except Exception:
                            self._audio_retry.on_failure()
        except Exception as e:
            print(f"[AID] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    async def run(self):
        api_key = self._api_key or _get_api_key()
        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"}
        )
        live_model_id = None
        retry_delay = 2.0

        # ── 세션 복원: 이전 대화 그래프를 로드해서 뷰어에 바로 게시 ──
        try:
            from core import graph_store
            store = graph_store.load()
            graph_store.publish(store)
            if store.get("turns"):
                self.ui.write_log(f"SYS: 🗂️ 이전 대화 그래프 복원 — {graph_store.stats(store)}")
        except Exception:
            pass

        start_time = time.time()
        while True:
            if self._shutdown_requested.is_set():
                return
            # enforce runtime limit if configured
            if self.runtime_limit_seconds is not None:
                elapsed = time.time() - start_time
                if elapsed >= float(self.runtime_limit_seconds):
                    print(f"[AID] ⏱️ Runtime limit reached ({self.runtime_limit_seconds}s). Exiting.")
                    try:
                        jarvis_status.write_status({"state": "expired"})
                    except Exception:
                        pass
                    os._exit(0)

            # enforce presence of required unlock path (e.g. mounted encrypted volume)
            if getattr(self, "required_unlock_path", None):
                try:
                    path = Path(self.required_unlock_path)
                    if not path.exists():
                        print(f"[AID] 🔒 Required unlock path not present: {self.required_unlock_path}")
                        print("Please mount the locked container (see scripts/create_locked_dmg.sh).")
                        time.sleep(5)
                        continue
                    if self.required_unlock_secret is not None:
                        content = path.read_text(encoding="utf-8").strip()
                        if content != self.required_unlock_secret:
                            print("[AID] 🔒 unlock.key content does not match expected secret.")
                            print("Please mount the locked container with the correct unlock.key file.")
                            time.sleep(5)
                            continue
                except Exception as e:
                    print(f"[AID] 🔒 Locked path check error: {e}")
                    time.sleep(1)
                    continue
            try:
                if not live_model_id:
                    try:
                        live_model = await asyncio.to_thread(pick_live_model, client, API_CONFIG_PATH)
                        live_model_id = live_model.removeprefix("models/")
                        self.ui.write_log(f"SYS: WEAID Live model selected: {live_model_id}")
                    except Exception as model_err:
                        print(f"[AID] ⚠️ Live model lookup failed: {model_err}")
                        live_model_id = LIVE_MODEL
                print("[AID] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=live_model_id, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()

                    print("[AID] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(f"SYS: {_assistant_name_for_voice(self._get_current_voice())} online.")
                    # ── 박수 명령 리스너 시작 (헌법 1조) ──
                    if not self._wake_started:
                        try:
                            from core.wake import ClapWakeDetector
                            self._wake_detector = ClapWakeDetector(
                                on_wake=self._on_clap_wake,
                                on_single=self._on_clap_stop,
                            )
                            if self._wake_detector.start():
                                self._wake_started = True
                                self.ui.write_log("SYS: 👏 박수 1번 = 멈춤 · 박수 2번 = 깨움 (Ctrl+W 수동 웨이크)")
                        except Exception:
                            pass
                    # ── 프로액티브 어시스턴트 시작 (예약 작업 자동 실행) ──
                    if self._proactive_engine is None:
                        try:
                            from core import settings as _st
                            from core.proactive import ProactiveEngine
                            interval = int(_st.get("proactive_interval") or 30)
                            self._proactive_engine = ProactiveEngine(on_item=self._on_proactive_item, interval_seconds=interval)
                            if self._proactive_engine.start():
                                self.ui.write_log("SYS: ⏰ 프로액티브 어시스턴트 활성화 — 예약 작업 시간이 되면 자동 실행합니다.")
                        except Exception:
                            pass
                    # ── 알람/타이머 매니저 시작 ──
                    if self._alarm_manager is None:
                        try:
                            from core import settings as _st
                            from core.alarms import AlarmManager
                            interval = int(_st.get("alarm_interval") or 5)
                            self._alarm_manager = AlarmManager(on_fire=self._on_alarm_fire, interval_seconds=interval)
                            if self._alarm_manager.start():
                                self.ui.write_log("SYS: 🔔 알람/타이머 매니저 활성화.")
                        except Exception:
                            pass
                    if not self.cloud_safe:
                        try:
                            jarvis_status.write_status({
                                "state": "online",
                                "voice": self._get_current_voice(),
                                "pid": os.getpid(),
                            })
                        except Exception:
                            pass

                    tg.create_task(self._send_realtime())
                    if not self.external_audio:
                        tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._announce_startup())

            except Exception as e:
                if self._shutdown_requested.is_set():
                    return
                actual = e
                if isinstance(e, ExceptionGroup) and len(e.exceptions) == 1:
                    actual = e.exceptions[0]

                if _is_unsupported_voice_error(actual) and self.voice_name != DEFAULT_VOICE_NAME:
                    old_voice = self.voice_name
                    self.voice_name = DEFAULT_VOICE_NAME
                    self.ui.write_log(
                        f"SYS: Voice '{old_voice}' not available. Falling back to {DEFAULT_VOICE_NAME}."
                    )
                    print(f"[AID] ⚠️ Voice '{old_voice}' unsupported; falling back to {DEFAULT_VOICE_NAME}.")
                    self.ui.sync_voice_display(DEFAULT_VOICE_NAME)
                    if not self.cloud_safe:
                        try:
                            jarvis_status.write_status({"state": "voice_fallback", "voice": DEFAULT_VOICE_NAME})
                        except Exception:
                            pass
                elif isinstance(actual, genai.errors.APIError) and "1000" in str(actual):
                    print("[AID] 🔌 Session ended normally.")
                    if not self.cloud_safe:
                        try:
                            jarvis_status.write_status({"state": "offline"})
                        except Exception:
                            pass
                else:
                    print(f"[AID] ⚠️ {e}")
                    traceback.print_exc()
                    # 1008 = 모델을 찾을 수 없음 → 기본 모델로 복귀해서 재시도
                    if "1008" in str(actual):
                        live_model_id = LIVE_MODEL
                        print(f"[AID] 🔄 Model 1008 error — falling back to {LIVE_MODEL}")
                        self.ui.write_log(f"SYS: 모델 오류 — 기본 모델 {LIVE_MODEL}로 복귀합니다.")
                    try:
                        self.ui.write_log(f"SYS: 연결 재시도 중... ({str(actual)[:80]})")
                    except Exception:
                        pass
                # 네트워크/서버 오류가 나도 앱은 유지한다. 지수 백오프 후 재연결.
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)
            else:
                retry_delay = 2.0

def main():
    import sys

    if "--self-test" in sys.argv[1:]:
        from scripts.self_test import main as self_test_main

        return self_test_main([argument for argument in sys.argv[1:] if argument != "--self-test"])

    from ui import JarvisUI

    running_as_app = getattr(sys, "frozen", False)

    if os.environ.get("JARVIS_CLI") != "1" and not running_as_app:
        print("[AID] Please launch with the AID CLI: jarvis")
        return
    if not wait_for_startup_claps():
        return
    print("[AID] ⚡ Powering up the interface...")
    try:
        ui = JarvisUI("face.png")
    except Exception as exc:
        print(f"[AID] ❌ Interface startup failed: {exc}")
        traceback.print_exc()
        return

    def runner():
        ui.wait_for_api_key()
        voice_name = _load_voice_name()
        jarvis = JarvisLive(ui, voice_name)
        ui.on_quit_requested = jarvis.request_shutdown
        ui.on_wake_requested = jarvis._on_clap_wake

        # Trial/keyword runtime limiting: set via env `JARVIS_TRIAL_KEYWORD`.
        # If set to any non-empty string, jarvis will run for 3600 seconds (1 hour).
        trial_kw = os.environ.get("JARVIS_TRIAL_KEYWORD")
        if trial_kw:
            jarvis.runtime_limit_seconds = int(os.environ.get("JARVIS_RUNTIME_SECONDS", "3600"))
            jarvis.ui.write_log(f"SYS: Trial keyword detected. Running for {jarvis.runtime_limit_seconds} seconds.")

        locked_secret = os.environ.get("JARVIS_LOCKED_KEY_SECRET")
        if locked_secret:
            jarvis.required_unlock_secret = locked_secret.strip()

        # Locked container check: if `JARVIS_LOCKED_VOLUME` is set, require
        # presence of `/Volumes/<name>/unlock.key` before full operation.
        locked_vol = os.environ.get("JARVIS_LOCKED_VOLUME")
        if locked_vol:
            mount_path = f"/Volumes/{locked_vol}/unlock.key"
            jarvis.required_unlock_path = mount_path
            jarvis.ui.write_log(f"SYS: Locked volume required: {mount_path}")
        ui.on_voice_change = jarvis.update_voice
        def _on_tts_change(provider, api_key, voice_id):
            if provider == "gemini":
                jarvis._tts_engine = None
                jarvis._ext_tts_provider = ""
                jarvis._ext_tts_voice_id = ""
                jarvis._ext_tts_api_key = ""
                jarvis.update_voice(voice_id)
            else:
                jarvis._ext_tts_provider = provider
                jarvis._ext_tts_voice_id = voice_id
                jarvis._ext_tts_api_key = api_key
                try:
                    from actions.tts_engine import TTSEngine
                    jarvis._tts_engine = TTSEngine(
                        provider=provider,
                        api_key=api_key,
                        voice_id=voice_id,
                    )
                    jarvis.ui.write_log(f"SYS: TTS engine ready: {provider} / {voice_id}")
                    jarvis.ui.write_log("SYS: WEAID audio muted - using external TTS")
                except Exception as e:
                    jarvis.ui.write_log(f"SYS: TTS engine error: {e}")
                # Restart session so new TTS takes effect
                if jarvis.session and jarvis._loop:
                    try:
                        asyncio.run_coroutine_threadsafe(jarvis.session.close(), jarvis._loop)
                    except Exception as e:
                        print(f"[AID] Could not close session: {e}")
        ui.on_tts_provider_change = _on_tts_change
        # 네트워크 오류가 나도 UI는 살아 있고, run()을 계속 재시도한다.
        while True:
            try:
                asyncio.run(jarvis.run())
                break
            except KeyboardInterrupt:
                print("\n🔴 Shutting down...")
                return
            except Exception as exc:
                message = f"WEAID startup failed: {str(exc)[:180]}"
                print(f"[AID] ❌ {message}")
                try:
                    ui.write_log(f"ERR: {message}")
                    ui.set_state("LISTENING")
                except Exception:
                    pass
                time.sleep(5)

    threading.Thread(target=runner, daemon=True).start()
    print("[AID] ✅ Interface ready.")
    ui.root.mainloop()
    print("[AID] Interface closed.")

def cli_main():
    """Canonical console entry point installed as the `jarvis` command."""
    os.environ["JARVIS_CLI"] = "1"
    return main()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
