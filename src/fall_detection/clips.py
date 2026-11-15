"""A bounded, in-memory camera buffer and a silent MP4 encoder for alerts."""

from collections import deque
from io import BytesIO

from .contracts import BgrFrame


BEFORE_SECONDS = 10
AFTER_SECONDS = 3
CLIP_FPS = 10
MAX_CLIP_BYTES = 5 * 1024 * 1024
ClipFrames = tuple[tuple[float, bytes], ...]


class FallClipBuffer:
    def __init__(self):
        self._recent: deque[tuple[float, bytes]] = deque(maxlen=BEFORE_SECONDS * CLIP_FPS + 1)
        self._incident: list[tuple[float, bytes]] = []
        self._until: float | None = None
        self._next_sample = float("-inf")

    def observe(self, frame: BgrFrame, timestamp: float) -> None:
        if timestamp < self._next_sample:
            return
        self._next_sample = timestamp + 1 / CLIP_FPS
        import cv2
        import numpy as np

        try:
            image = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.width, 3)
            scale = min(1, 640 / max(frame.width, frame.height))
            size = (max(2, int(frame.width * scale) // 2 * 2),
                    max(2, int(frame.height * scale) // 2 * 2))
            if size != (frame.width, frame.height):
                image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        except (ValueError, cv2.error):
            return
        if not ok:
            return
        sample = timestamp, jpeg.tobytes()
        self._recent.append(sample)
        while self._recent[0][0] < timestamp - BEFORE_SECONDS:
            self._recent.popleft()
        if self._until is not None and timestamp <= self._until:
            self._incident.append(sample)

    def mark(self, timestamp: float) -> None:
        self._until = timestamp + AFTER_SECONDS
        self._incident = [sample for sample in self._recent
                          if timestamp - BEFORE_SECONDS <= sample[0] <= self._until]

    def clear_recent(self) -> None:
        """Start a new rolling window without altering an active incident's frames."""
        self._recent.clear()
        self._next_sample = float("-inf")
        self._until = None

    def take(self) -> ClipFrames:
        frames = tuple(self._incident)
        self.discard()
        return frames

    def discard(self) -> None:
        self._incident.clear()
        self._until = None


def encode_clip(frames: ClipFrames) -> bytes | None:
    """Encode on the notification worker. The caller can fall back to text."""
    if len(frames) < 2:
        return None
    import av
    import cv2
    import numpy as np

    def decode(data: bytes):
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

    image = decode(frames[0][1])
    height, width = image.shape[:2]
    buffer = BytesIO()
    with av.open(buffer, "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=CLIP_FPS)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "veryfast", "crf": "25"}
        start = frames[0][0]
        index = 0
        for step in range(round((frames[-1][0] - start) * CLIP_FPS) + 1):
            timestamp = start + (step + 0.5) / CLIP_FPS
            while index + 1 < len(frames) and frames[index + 1][0] <= timestamp:
                index += 1
                image = decode(frames[index][1])
                if image.shape[:2] != (height, width):
                    image = cv2.resize(image, (width, height))
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    data = buffer.getvalue()
    return data if len(data) <= MAX_CLIP_BYTES else None
