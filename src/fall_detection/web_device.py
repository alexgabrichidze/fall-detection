"""Loopback browser interface for the existing detector and safety loop."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Callable
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from time import monotonic

from .device import Command, View, draw_pose
from .activity import ActivityTracker
from .camera_inputs import list_camera_inputs
from .notifier import DeliveryStatus
from .telegram_link import RelayTelegramLink, TelegramLink

_STATIC = Path(__file__).with_name("static")
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/monitor.css": ("monitor.css", "text/css; charset=utf-8"),
    "/monitor.js": ("monitor.js", "text/javascript; charset=utf-8"),
    "/geist.woff2": ("geist.woff2", "font/woff2"),
}


class WebDevice:
    def __init__(self, port: int, *, live: bool, countdown: float, telegram: TelegramLink | RelayTelegramLink | None,
                 camera_index: Callable[[], int] | None = None,
                 select_camera: Callable[[int], None] | None = None):
        self.live = live
        self.countdown = countdown
        self.telegram = telegram
        self.permission_pending = False
        self._camera_index = camera_index or (lambda: 0)
        self._select_camera = select_camera
        self._view: View | None = None
        self._state_lock = Lock()
        self._activity_tracker = ActivityTracker()
        self._activity = self._activity_tracker.clear(0, "off", "Start the camera to see live activity.")
        self._commands: Queue[Command] = Queue(maxsize=16)
        self._frame_lock = Lock()
        self._encoded_timestamp: float | None = None
        self._encoded_frame: bytes = b""
        self._server = ThreadingHTTPServer(("127.0.0.1", port), MonitorHandler)
        self._server.device = self
        self.host = f"127.0.0.1:{self._server.server_port}"
        self.url = f"http://{self.host}"
        self._thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()

    def render(self, view: View) -> None:
        report = self._activity_tracker.update(view.preview, running=view.running,
                                               paused=view.paused, fault=view.detector_error)
        with self._state_lock:
            self._view = view
            self._activity = report

    def enqueue(self, command: Command) -> None:
        try:
            self._commands.put_nowait(command)
        except Full:
            raise ValueError("The monitor is busy. Try again in a moment.") from None

    def poll_commands(self) -> tuple[Command, ...]:
        commands = []
        for _ in range(16):
            try:
                commands.append(self._commands.get_nowait())
            except Empty:
                break
        return tuple(commands)

    def status(self) -> dict:
        with self._state_lock:
            view = self._view
            activity = self._activity

        return {
            "live": self.live,
            "running": view.running if view else False,
            "permissionPending": self.permission_pending,
            "cameraInput": self._camera_index() if self.live else None,
            "countdown": self.countdown,
            "camera": view.preview.camera_status.value if view else "stopped",
            "pose": view.preview.pose_status.value if view else "unknown",
            "modelReady": view.preview.model_ready if view else False,
            "activity": activity.payload(monotonic()),
            "hasFrame": bool(view and view.preview.frame and not view.detector_error),
            "paused": view.paused if view else False,
            "fault": view.detector_error if view else None,
            "incident": view.state.value if view else "monitoring",
            "remaining": view.remaining if view else None,
            "delivery": view.delivery if view else "none",
            "audio": view.audio if view else "not requested",
            "voice": view.voice if view else "disabled",
            "listening": view.listening if view else False,
            "telegram": self.telegram.status() if self.telegram else None,
        }

    def camera_inputs(self) -> dict:
        if not self.live:
            return {"cameras": [], "selected": None}
        try:
            cameras = list_camera_inputs()
        except (ImportError, OSError, RuntimeError, NotImplementedError):
            raise ValueError("Could not list cameras. Install the detection dependencies and try again.") from None
        selected = self._camera_index()
        if not any(camera["index"] == selected for camera in cameras):
            cameras.append({"index": selected, "name": f"Camera {selected} (not detected)"})
        return {"cameras": cameras, "selected": selected}

    def select_camera(self, index: int) -> None:
        if not self.live or self._select_camera is None:
            raise ValueError("Camera selection is available in live mode.")
        if type(index) is not int or not 0 <= index <= 255:
            raise ValueError("Choose a valid camera input.")
        if self.permission_pending or (self._view and self._view.running):
            raise ValueError("Stop the camera before changing the input.")
        if not any(camera["index"] == index for camera in self.camera_inputs()["cameras"]):
            raise ValueError("Camera input is no longer available. Refresh the camera list.")
        # The detector also checks its session under a lock, closing a race
        # between this request and the main loop starting capture.
        self._select_camera(index)

    def jpeg(self) -> bytes:
        view = self._view
        if view is None or view.detector_error or view.preview.frame is None:
            return b""
        preview = view.preview
        frame = preview.frame
        with self._frame_lock:
            if preview.timestamp != self._encoded_timestamp:
                import cv2
                import numpy as np

                raw = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.width, 3)
                scale = min(1, 960 / max(frame.width, frame.height))
                size = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
                image = cv2.resize(raw, size)
                draw_pose(image, preview, cv2)
                ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
                self._encoded_frame = encoded.tobytes() if ok else b""
                self._encoded_timestamp = preview.timestamp
            return self._encoded_frame

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


class MonitorHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass

    @property
    def device(self) -> WebDevice:
        return self.server.device

    def do_GET(self):
        if self.headers.get("Host") != self.device.host:
            self._json({"error": "Open the monitor using its local address."}, 403)
            return
        path = self.path.partition("?")[0]
        if path in _ASSETS:
            asset, media = _ASSETS[path]
            self._respond((_STATIC / asset).read_bytes(), media)
        elif path == "/api/status":
            self._json(self.device.status())
        elif path == "/api/cameras":
            try:
                self._json(self.device.camera_inputs())
            except ValueError as error:
                self._json({"error": str(error)}, 400)
        elif path == "/api/frame":
            try:
                frame = self.device.jpeg()
            except (ValueError, RuntimeError):
                frame = b""
            self._respond(frame, "image/jpeg", 200 if frame else 204)
        elif path == "/favicon.ico":
            self._respond(b"", "image/x-icon", 204)
        else:
            self._json({"error": "Page not found."}, 404)

    def do_POST(self):
        if self.headers.get("Host") != self.device.host or self.headers.get("Origin") != self.device.url:
            self._json({"error": "Open the monitor using its local address."}, 403)
            return
        try:
            count = int(self.headers.get("Content-Length", "0"))
            if not 0 < count <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(count))
            if not isinstance(body, dict):
                raise ValueError
        except (OSError, ValueError):
            self._json({"error": "Invalid monitor request."}, 400)
            return
        try:
            response = self._action(body)
        except ValueError as error:
            self._json({"error": str(error)}, 400)
        except OSError:
            self._json({"error": "Could not save your Telegram settings."}, 500)
        else:
            self._json(response)

    def _action(self, body: dict) -> dict:
        if self.path == "/api/camera":
            self.device.select_camera(body.get("index"))
            return {"selected": self.device.status()["cameraInput"]}
        if self.path == "/api/command":
            try:
                command = Command(body.get("command"))
            except ValueError:
                raise ValueError("Unknown monitor command.") from None
            self.device.enqueue(command)
            return {}
        telegram = self.device.telegram
        if telegram is None:
            raise ValueError("Open live mode to connect Telegram.")
        if self.path == "/api/telegram/link":
            token = body.get("token", "")
            if not isinstance(token, str):
                raise ValueError("Enter a Telegram bot token.")
            return telegram.begin(token)
        if self.path == "/api/telegram/finish":
            telegram.finish()
        elif self.path == "/api/telegram/disconnect":
            telegram.disconnect()
        elif self.path == "/api/telegram/test":
            result = telegram.send_test()
            if result == DeliveryStatus.ACCEPTED:
                return {"message": "Telegram accepted the test message. Check your chat."}
            if result == DeliveryStatus.UNCERTAIN:
                raise ValueError("Could not confirm delivery. Check your chat before retrying.")
            raise ValueError("Telegram rejected the test message. Check the linked chat.")
        else:
            raise ValueError("Unknown monitor action.")
        return {}

    def _json(self, payload: dict, status: int = 200) -> None:
        self._respond(json.dumps(payload).encode(), "application/json", status)

    def _respond(self, data: bytes, media: str, status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            if data:
                self.wfile.write(data)
        except OSError:
            pass
