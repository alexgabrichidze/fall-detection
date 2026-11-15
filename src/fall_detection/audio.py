"""Optional prerecorded PCM WAV playback. No microphone capture."""

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
import wave


PROMPTS = Path(__file__).with_name("prompts")


@dataclass(frozen=True, slots=True)
class AudioResult:
    incident_id: str
    status: str


class PromptPlayer:
    """Load and play on a worker; local controls never wait for audio I/O."""

    def __init__(self, path: Path | None):
        self._path = path
        self._jobs: Queue[tuple[str, Path | None]] = Queue(maxsize=1)
        self._results: Queue[AudioResult] = Queue(maxsize=2)
        self._stop = Event()
        self._cancel = Event()
        self._busy = False
        self._thread = Thread(target=self._run, name="prompt-audio", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        return self._busy

    def play(self, incident_id: str, path: Path | None = None) -> bool:
        if self._busy or self._stop.is_set() or not self._thread.is_alive():
            return False
        self._cancel.clear()
        try:
            self._jobs.put_nowait((incident_id, path))
        except Full:
            return False
        self._busy = True
        return True

    def cancel(self):
        self._cancel.set()

    def poll(self) -> AudioResult | None:
        try:
            result = self._results.get_nowait()
        except Empty:
            return None
        self._busy = False
        return result

    def close(self, timeout: float = 0.2) -> bool:
        self._stop.set()
        self._cancel.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _play(self, path: Path | None = None) -> str:
        path = path or self._path
        if path is None:
            return "error: prompt WAV not configured"
        # Imported only on the audio worker, never needed by headless tests.
        import numpy as np
        import sounddevice as sd

        with wave.open(str(path), "rb") as prompt:
            channels, width, rate, frames = (
                prompt.getnchannels(), prompt.getsampwidth(), prompt.getframerate(), prompt.getnframes()
            )
            if width != 2 or channels not in (1, 2) or frames <= 0 or frames / rate > 10:
                return "error: prompt must be 16-bit PCM mono/stereo WAV, at most 10 seconds"
            samples = np.frombuffer(prompt.readframes(frames), dtype="<i2").reshape(-1, channels)
        if self._cancel.is_set():
            return "cancelled"
        try:
            sd.play(samples, samplerate=rate, blocking=False)
            stream = sd.get_stream()
            while stream.active:
                if self._cancel.wait(0.02):
                    sd.stop()
                    return "cancelled"
            underflows = sd.wait()
            return "error: audio output underflow" if underflows else "played"
        finally:
            sd.stop()

    def _run(self):
        while not self._stop.is_set():
            try:
                incident_id, path = self._jobs.get(timeout=0.05)
            except Empty:
                continue
            try:
                status = self._play(path)
            except Exception:
                status = "error: prompt unavailable or audio playback failed"
            self._results.put_nowait(AudioResult(incident_id, status))
