from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from fall_detection.audio import PromptPlayer


class AudioTests(unittest.TestCase):
    def test_missing_prompt_returns_visible_error_without_device_import(self):
        player = PromptPlayer(None)
        try:
            self.assertTrue(player.play("incident"))
            deadline = time.monotonic() + 1
            result = None
            while result is None and time.monotonic() < deadline:
                result = player.poll()
                time.sleep(0.001)
            self.assertIsNotNone(result)
            self.assertEqual(result.incident_id, "incident")
            self.assertIn("prompt WAV not configured", result.status)
        finally:
            self.assertTrue(player.close(timeout=1))

    def test_pcm_wav_decoding_uses_output_only_and_cancellation_stops_playback(self):
        try:
            import numpy
        except ImportError:
            self.skipTest("optional NumPy not installed")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-silence.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b'\x00\x00' * 80)
            fake_audio = SimpleNamespace(play=Mock(), get_stream=Mock(return_value=SimpleNamespace(active=True)),
                                         stop=Mock(), wait=Mock(return_value=None))
            player = PromptPlayer(path)
            try:
                def begin(*args, **kwargs):
                    self.assertFalse(kwargs["blocking"])
                    self.assertEqual(kwargs["samplerate"], 8000)
                    self.assertEqual(args[0].shape, (80, 1))
                    player.cancel()
                fake_audio.play.side_effect = begin
                with patch.dict("sys.modules", {"sounddevice": fake_audio}):
                    self.assertEqual(player._play(), "cancelled")
                fake_audio.stop.assert_called()
                fake_audio.wait.assert_not_called()
            finally:
                player.close(timeout=1)
