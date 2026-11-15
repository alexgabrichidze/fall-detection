"""Optional offline response recognition. Audio and transcripts stay in memory."""

from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import re
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic

DEFAULT_VOICE_MODEL = Path("models/vosk-model-small-en-us-0.15")
# Keep negative responses and unknown speech in the grammar so the recognizer
# can reject them instead of forcing every utterance into a positive phrase.
RESPONSE_GRAMMAR = (
    "i'm okay", "i am okay", "i'm not okay", "i am not okay",
    "help", "i need help", "are you okay", "ten seconds left", "[unk]",
)
_RESPONSE = r"(?:i'm|i am) (?:okay|ok)"



def is_cancellation(result: str) -> bool:
    """Accept complete, confidently recognized responses; never partial matches."""
    try:
        decoded = json.loads(result)
        text = decoded["text"].lower().replace("’", "'")
        words = decoded["result"]
        if not re.fullmatch(rf"{_RESPONSE}(?: {_RESPONSE})*", text):
            return False
        if " ".join(word["word"] for word in words) != text:
            return False
        return bool(words) and all(
            isfinite(word["conf"]) and 0.8 <= word["conf"] <= 1 for word in words
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


@dataclass(frozen=True, slots=True)
class VoiceResult:
    incident_id: str


class VoiceListener:
    """Bounded worker; microphone opens only for an active response window.

    Each request has its own cancellation token. Stale workers cannot update
    the status of a newer request. No audio or recognized text is persisted.
    """

    def __init__(self, model_path: Path, device: int | None = None):
        self._path = model_path
        self._device = device
        self._lock = Lock()
        self._stop = Event()
        self._request = None
        self._working = False
        self._status = "ready"
        self._listening = False
        self._results: Queue[VoiceResult] = Queue(maxsize=1)
        self._thread = Thread(target=self._run, name="voice-response", daemon=True)
        self._thread.start()

    @property
    def status(self) -> tuple[str, bool]:
        with self._lock:
            return self._status, self._listening

    @property
    def idle(self) -> bool:
        """True only after the worker has finished its current request."""
        with self._lock:
            return not self._working and self._request is None

    def start(self, incident_id: str, deadline: float):
        self.cancel()
        with self._lock:
            self._request = (incident_id, deadline, Event())
            self._status = "waiting for prompt echo"

    def cancel(self):
        with self._lock:
            if self._request:
                self._request[2].set()
            self._request = None
            # Keep the active indication until the worker closes its stream.
            self._status = "stopping" if self._listening else "ready"

    def poll(self) -> VoiceResult | None:
        try:
            return self._results.get_nowait()
        except Empty:
            return None

    def close(self, timeout: float = 0.2) -> bool:
        self.cancel()
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _update(self, request, status, listening=False):
        with self._lock:
            # Only this worker opens streams, so this flag always reflects
            # whether its current stream has closed, even after cancellation.
            self._listening = listening
            if self._request is request:
                self._status = status
            elif self._request is None:
                self._status = "ready"

    def _listen(self, request, model):
        import sounddevice as sd
        from vosk import KaldiRecognizer

        _, deadline, cancelled = request
        chunks: Queue[bytes] = Queue(maxsize=20)
        failed = Event()

        def callback(data, frames, timing, status):
            if cancelled.is_set() or self._stop.is_set() or monotonic() >= deadline:
                return
            if status:
                failed.set()
                return
            try:
                chunks.put_nowait(bytes(data))
            except Full:
                failed.set()

        device_info = sd.query_devices(self._device, "input")
        rate = int(device_info["default_samplerate"])
        recognizer = KaldiRecognizer(model, rate, json.dumps(RESPONSE_GRAMMAR))
        recognizer.SetWords(True)
        if cancelled.is_set() or self._stop.is_set() or monotonic() >= deadline:
            return False
        # Only complete results pass the strict phrase and confidence filter.
        with sd.RawInputStream(device=self._device, samplerate=rate, channels=1,
                               dtype="int16", blocksize=max(1, rate // 10), callback=callback):
            input_name = device_info.get("name", "system default")
            self._update(request, f"listening ({input_name}): say I'm okay", True)
            while not cancelled.is_set() and not self._stop.is_set() and monotonic() < deadline:
                if failed.is_set():
                    raise RuntimeError("microphone input interrupted")
                try:
                    chunk = chunks.get(timeout=0.05)
                except Empty:
                    continue
                if recognizer.AcceptWaveform(chunk) and is_cancellation(recognizer.Result()):
                    return True
        return False

    def _run(self):
        model = None
        while not self._stop.wait(0.02):
            with self._lock:
                request = self._request
                if request is not None:
                    self._working = True
            if request is None:
                continue
            incident_id, deadline, cancelled = request
            accepted = False
            status = "ready"
            try:
                # Never capture the playback or its immediate speaker echo.
                if cancelled.wait(0.4) or monotonic() >= deadline:
                    continue
                if model is None:
                    self._update(request, "loading local speech model")
                    from vosk import Model, SetLogLevel
                    SetLogLevel(-1)
                    if not self._path.is_dir():
                        raise ValueError("voice model missing")
                    model = Model(str(self._path))
                if not cancelled.is_set() and monotonic() < deadline:
                    accepted = self._listen(request, model)
                status = "response recognized" if accepted else "ready"
            except Exception:
                status = "unavailable: check voice model and microphone"
            finally:
                self._update(request, status)
                with self._lock:
                    self._working = False
                    if self._request is request:
                        self._request = None
                if accepted and not cancelled.is_set() and monotonic() < deadline:
                    try:
                        self._results.put_nowait(VoiceResult(incident_id))
                    except Full:
                        pass
