"""Grok 음성 모드 — Gemini 장애 시에만 사용하는 대체 음성 체인.

Gemini Live가 오류(지출 한도/네트워크 등)로 동작하지 않을 때만 활성화된다:

    마이크 녹음(VAD) → faster-whisper STT → Grok 응답 → edge-tts TTS → 스피커

순수 함수(테스트 가능): detect_silence, should_switch_to_grok,
pick_tts_backend. 네트워크/모델 의존부는 런타임에서 lazy import 한다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

# Gemini 실패 → Grok 음성 폴백 전환 판정에 필요한 상수
SWITCH_AFTER_FAILURES = 2
GEMINI_RETRY_INTERVAL = 300  # 폴백 중 Gemini 재시도 간격(초)

# Gemini 오류 중 "재시도로 해결 안 되는" 패턴 (즉시 전환 유도)
FATAL_PATTERNS = ("spending cap", "지출", "api key not valid", "permission denied", "403")


def detect_silence(samples: list[float], threshold: float = 0.02) -> bool:
    """에너지가 임계값 미만이면 무음으로 판정."""
    n = len(samples)
    if n == 0:
        return True
    energy = sum(s * s for s in samples) / n
    return energy < threshold * threshold


def should_switch_to_grok(error_text: str, consecutive_failures: int) -> bool:
    """Gemini 오류 시 Grok 음성 폴백으로 전환할지 판정.

    - 지출 한도/키 무효 같은 치명 오류 → 첫 실패에도 즉시 전환
    - 그 외 오류 → SWITCH_AFTER_FAILURES회 연속 실패 시 전환
    """
    text = str(error_text or "").lower()
    if any(p in text for p in FATAL_PATTERNS):
        return consecutive_failures >= 1
    return consecutive_failures >= SWITCH_AFTER_FAILURES


def pick_tts_backend() -> str:
    """TTS 백엔드 선택: edge-tts(우선) / sapi(폴백)."""
    try:
        import edge_tts  # noqa: F401
        return "edge"
    except ImportError:
        return "sapi"


def record_until_silence(
    input_stream_factory: Callable[[], Any],
    sample_rate: int = 16000,
    silence_seconds: float = 1.2,
    max_seconds: float = 12.0,
    threshold: float = 0.02,
) -> list[float]:
    """무음이 silence_seconds 지속될 때까지 녹음 (테스트 가능, 스트림 주입)."""
    import numpy as np

    stream = input_stream_factory()
    blocks: list[list[float]] = []
    silent = 0.0
    total = 0.0
    try:
        while total < max_seconds:
            chunk = stream.read_block()
            samples = [float(x) for x in np.asarray(chunk, dtype=np.float32).reshape(-1)]
            blocks.append(samples)
            dur = len(samples) / sample_rate
            total += dur
            if detect_silence(samples, threshold):
                silent += dur
            else:
                silent = 0.0
            if silent >= silence_seconds and blocks:
                break
    except Exception:
        pass
    return [s for block in blocks for s in block]


class GrokVoiceSession:
    """턴제 Grok 음성 루프 (별도 스레드에서 blocking 실행)."""

    def __init__(
        self,
        system_prompt: str,
        on_log: Callable[[str], None] | None = None,
        on_state: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        sample_rate: int = 16000,
    ):
        self.system_prompt = system_prompt
        self.on_log = on_log
        self.on_state = on_state
        self.stop_event = stop_event or threading.Event()
        self.sample_rate = sample_rate
        self._whisper_model = None

    def _log(self, msg: str):
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    def _stt(self, samples: list[float]) -> str:
        """faster-whisper STT (lazy 로드)."""
        import numpy as np
        from faster_whisper import WhisperModel

        if self._whisper_model is None:
            self._log("SYS: 🎙️ 음성 인식 모델 로드 중... (최초 1회)")
            self._whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        audio = np.asarray(samples, dtype=np.float32)
        segments, _info = self._whisper_model.transcribe(audio, language="ko", beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text

    def _respond(self, text: str) -> str:
        from core.llm import generate
        return generate(self.system_prompt, text, provider="grok", model="grok-4", timeout_s=60).strip()

    def _tts(self, text: str) -> None:
        """edge-tts → mp3 → soundfile 디코드 → sounddevice 재생. 실패 시 SAPI."""
        import asyncio
        import io
        import tempfile
        from pathlib import Path

        mp3_path = Path(tempfile.gettempdir()) / "weaid_tts.mp3"
        try:
            asyncio.run(self._edge_synth(text, mp3_path))
            import soundfile as sf
            import sounddevice as sd
            data, sr = sf.read(str(mp3_path), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            sd.play(data, sr)
            sd.wait()
            return
        except Exception:
            pass
        # SAPI 폴백 (Windows 내장 TTS, 오프라인)
        try:
            import subprocess
            wav_path = Path(tempfile.gettempdir()) / "weaid_tts_sapi.wav"
            safe = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{wav_path}'); $s.Speak('{safe}'); $s.Dispose()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60)
            if wav_path.exists():
                import soundfile as sf
                import sounddevice as sd
                data, sr = sf.read(str(wav_path), dtype="float32")
                sd.play(data, sr)
                sd.wait()
        except Exception:
            pass

    async def _edge_synth(self, text: str, mp3_path):
        import edge_tts
        communicate = edge_tts.Communicate(text, "ko-KR-SunHiNeural")
        await communicate.save(str(mp3_path))

    def listen_once(self) -> str | None:
        """한 번 듣고 텍스트 반환 (발화 없으면 None)."""
        import numpy as np
        import sounddevice as sd

        try:
            samples = record_until_silence(
                lambda: _SDBlockReader(sd, self.sample_rate),
                sample_rate=self.sample_rate,
            )
        except Exception:
            return None
        if len(samples) < self.sample_rate * 0.5:  # 최소 0.5초
            return None
        text = self._stt(samples)
        return text or None

    def run_forever(self) -> str:
        """블로킹 루프. 'retry_gemini' | 'stopped' 반환."""
        import sounddevice as sd

        last_gemini_try = time.time()
        self._log("SYS: 🟠 Grok 음성 모드 시작 (Gemini 복구 시 자동 복귀).")
        while not self.stop_event.is_set():
            if time.time() - last_gemini_try >= GEMINI_RETRY_INTERVAL:
                return "retry_gemini"
            try:
                if self.on_state:
                    self.on_state("LISTENING")
                text = self.listen_once()
                if not text:
                    time.sleep(0.1)
                    continue
                self._log(f"You: {text}")
                if self.on_state:
                    self.on_state("THINKING")
                reply = self._respond(text)
                if reply:
                    self._log(f"{self._assistant_name()}: {reply}")
                    if self.on_state:
                        self.on_state("SPEAKING")
                    self._tts(reply)
            except Exception as e:
                self._log(f"SYS: Grok 음성 오류: {str(e)[:100]}")
                time.sleep(2)
        return "stopped"

    def _assistant_name(self) -> str:
        try:
            from main import _assistant_name_for_voice
            return "AID"
        except Exception:
            return "AID"


class _SDBlockReader:
    """sounddevice.InputStream 블록 리더 (녹음 테스트 주입용 어댑터)."""

    def __init__(self, sd_module, sample_rate: int, blocksize: int = 1024):
        import numpy as np
        self.sd = sd_module
        self.blocksize = blocksize
        self._stream = sd_module.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=blocksize)
        self._stream.start()

    def read_block(self):
        import numpy as np
        data, _overflowed = self._stream.read(self.blocksize)
        return data
