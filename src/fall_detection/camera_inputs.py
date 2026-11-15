"""Discover capture inputs without starting a video stream."""

import sys


def camera_backend() -> int:
    """Use the same backend for enumeration and capture so indices agree."""
    import cv2

    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    if sys.platform == "win32":
        return cv2.CAP_MSMF
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def list_camera_inputs() -> list[dict]:
    from cv2_enumerate_cameras import enumerate_cameras

    return [
        {"index": camera.index, "name": camera.name or f"Camera {camera.index}"}
        for camera in enumerate_cameras(camera_backend())
    ]
