"""Person A owns live detection. Implement the Detector protocol here."""

from .contracts import Detector


def create_live_detector() -> Detector:
    raise NotImplementedError(
        "Live detection is not implemented. Verify model preprocessing before enabling it."
    )

