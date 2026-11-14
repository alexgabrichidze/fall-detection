"""Person B owns rendering and local controls. Console consumer for now."""

from typing import TextIO

from .contracts import DetectionEvent, Preview


class ConsoleRenderer:
    def __init__(self, output: TextIO) -> None:
        self._output = output

    def preview(self, preview: Preview) -> None:
        print(
            f"[{preview.source.value.upper()}] "
            f"camera={preview.camera_status.value} "
            f"pose={preview.pose_status.value} "
            f"model_ready={preview.model_ready} "
            f"fault={preview.fault_code.value if preview.fault_code else 'none'}",
            file=self._output, flush=True,
        )

    def event(self, event: DetectionEvent) -> None:
        print(
            f"[{event.source.value.upper()}] consumed event={event.event_id} "
            f"kind={event.kind.value} score={event.model_score} "
            f"fault={event.fault_code.value if event.fault_code else 'none'}",
            file=self._output, flush=True,
        )

