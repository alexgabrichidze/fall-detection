import contextlib
import io
import subprocess
import sys
import unittest

from fall_detection.device import ConsoleRenderer
from fall_detection.fake_detector import FakeDetector
from fall_detection.main import consume_pending, main


class ApplicationTests(unittest.TestCase):
    def test_consumer_accepts_detector_protocol_without_model_packages(self):
        detector = FakeDetector()
        detector.start()
        detector.emit_possible_fall()
        output = io.StringIO()
        count = consume_pending(detector, ConsoleRenderer(output))
        self.assertEqual(count, 1)
        self.assertIn("[SIMULATED]", output.getvalue())
        self.assertIn("kind=possible_fall", output.getvalue())
        self.assertEqual(consume_pending(detector, ConsoleRenderer(output)), 0)

    def test_cli_simulation_runs_without_optional_runtime_packages(self):
        result = subprocess.run(
            [sys.executable, "-m", "fall_detection", "--detector", "fake",
             "--duration", "0.15", "--event-after", "0"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIMULATION ONLY", result.stdout)
        self.assertEqual(result.stdout.count("kind=possible_fall"), 1)
        self.assertIn("camera=stopped", result.stdout)

    def test_busy_event_producer_cannot_starve_the_application_loop(self):
        detector = FakeDetector(event_capacity=100)
        detector.start()
        for _ in range(40):
            detector.emit_possible_fall()
        renderer = ConsoleRenderer(io.StringIO())
        self.assertEqual(consume_pending(detector, renderer), 32)
        self.assertEqual(consume_pending(detector, renderer), 8)

    def test_invalid_timing_is_rejected_before_the_application_starts(self):
        for arguments in [
            ["--duration", "nan"],
            ["--duration", "-1"],
            ["--event-after", "-1"],
            ["--duration", "0.1", "--event-after", "1"],
        ]:
            with self.subTest(arguments=arguments):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        main(["--detector", "fake", *arguments])
                self.assertEqual(result.exception.code, 2)

