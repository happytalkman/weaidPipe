"""박수 2번 웨이크업 감지기 (Clap Wake Detector).

AID가 유휴 상태일 때도 백그라운드로 마이크를 가볍게 듣고 있다가,
짧은 간격의 박수 2번(transient + 에너지)을 감지하면 on_wake 콜백을 호출한다.

  * 상시 경량 리스너 (16kHz, non-blocking 스레드 + sounddevice 콜백)
  * 노이즈 플로어 적응형 임계값 (팬 소음/키보드 타이핑 오탐 방지)
  * 2회 박수 ≤ 2.5초 간격, 박수 간 쿨다운 0.25초
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class ClapWakeDetector:
    def __init__(
        self,
        on_wake: Callable[[], None],
        on_single: Callable[[], None] | None = None,
        double_window: float = 0.9,
        cooldown: float = 0.25,
        sample_rate: int = 16000,
        blocksize: int = 1024,
    ):
        """박수 명령 감지기 (헌법 1조).

        박수 1번 → on_single (즉시 멈춤/STOP)
        박수 2번 → on_wake  (깨어남/재개)

        1번 클랩 후 double_window(0.9초) 동안 두 번째 클랩이 오지 않으면
        멈춤으로 확정된다 (두 박자 클랩과의 구분).
        """
        self.on_wake = on_wake
        self.on_single = on_single
        self.double_window = float(double_window)
        self.cooldown = float(cooldown)
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # 클랩 검출 상태
        self._pending: float | None = None   # 첫 클랩 시각 (두 번째 대기 중)
        self._last_clap_at = 0.0
        self._noise_floor = 0.008

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clap-wake")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _fire_single(self, ts: float) -> None:
        """double_window 안에 두 번째 클랩이 없으면 1번 클랩 = 멈춤으로 확정."""
        with self._lock:
            if self._pending == ts:
                self._pending = None
                cb = self.on_single
            else:
                return
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return

        def callback(indata, frames, time_info, status):
            if self._stop.is_set():
                return
            try:
                samples = np.asarray(indata, dtype=np.float32).reshape(-1)
            except Exception:
                return
            if samples.size == 0:
                return
            magnitude = np.abs(samples)
            rms = float(np.sqrt(np.mean(samples * samples)))
            peak = float(np.max(magnitude))
            now = time.monotonic()
            wake_cb = None

            with self._lock:
                # 저에너지 프레임만 노이즈 플로어 학습
                if rms < max(0.08, self._noise_floor * 6.0):
                    self._noise_floor = (self._noise_floor * 0.96) + (rms * 0.04)
                threshold = max(0.100, self._noise_floor * 6.5)
                peak_threshold = max(0.35, self._noise_floor * 15.0)
                crest = peak / max(rms, 1e-6)
                is_transient = crest >= 2.20 and rms >= threshold
                is_loud = rms >= max(0.28, self._noise_floor * 15.0)
                if (
                    peak < peak_threshold
                    or not (is_transient or is_loud)
                    or now - self._last_clap_at < self.cooldown
                ):
                    return
                self._last_clap_at = now

                if self._pending is None:
                    # 첫 클랩: 두 번째를 double_window 동안 기다린다.
                    self._pending = now
                    timer = threading.Timer(self.double_window, self._fire_single, args=(now,))
                    timer.daemon = True
                    timer.start()
                else:
                    if now - self._pending <= self.double_window:
                        # 박수 2번 → 웨이크 확정
                        self._pending = None
                        wake_cb = self.on_wake
                    else:
                        # 이전 클랩은 멈춤으로 이미 확정됨 → 새 첫 클랩으로 시작
                        self._pending = now
                        timer = threading.Timer(self.double_window, self._fire_single, args=(now,))
                        timer.daemon = True
                        timer.start()
                        return

            if wake_cb is not None:
                try:
                    wake_cb()
                except Exception:
                    pass

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                callback=callback,
                latency="low",
            ):
                while not self._stop.is_set():
                    time.sleep(0.2)
        except Exception:
            # 마이크를 사용할 수 없으면 웨이크업 리스너는 조용히 비활성화된다.
            return
