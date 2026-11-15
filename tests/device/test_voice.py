import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fall_detection.voice import VoiceListener, is_cancellation


def response(text, confidence=0.95):
    return json.dumps({"text": text, "result": [
        {"word": word, "conf": confidence} for word in text.split()
    ]})


class VoiceTests(unittest.TestCase):
    def test_only_complete_confident_positive_responses_cancel(self):
        for text in ("i'm okay", "i am okay", "i'm ok", "i am ok"):
            self.assertTrue(is_cancellation(response(text)))
        for text in ("okay", "not okay", "i am not okay", "are you okay", "i need help",
                     "i'm okay but help", "", "i am"):
            self.assertFalse(is_cancellation(response(text)))
        for value in (response("i'm okay", 0.79), response("i'm okay", float('nan')),
                      '{"partial":"i am okay"}', '{"text":"i am okay"}',
                      '{"text":"i am okay","result":null}', 'not JSON', '[]'):
            self.assertFalse(is_cancellation(value))

    def run_worker(self, *, input_error=False, missing=False, cancel_during_input=False, silence=False):
        entered, exited = Event(), Event()
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = not silence
        recognizer.Result.return_value = response("i am okay")
        sd = Mock()
        sd.query_devices.return_value = {"default_samplerate": 48000}
        voice = None

        def stream(**kwargs):
            class Stream:
                def __enter__(self):
                    entered.set()
                    kwargs['callback'](b'\0' * 9600, 4800, None, input_error)
                    if cancel_during_input:
                        voice.cancel()
                    return self

                def __exit__(self, *args):
                    exited.set()
            return Stream()

        sd.RawInputStream.side_effect = stream
        vosk = SimpleNamespace(Model=Mock(), SetLogLevel=Mock(),
                               KaldiRecognizer=Mock(return_value=recognizer))
        with patch.dict('sys.modules', {'sounddevice': sd, 'vosk': vosk}), \
                patch.object(Path, 'is_dir', return_value=not missing):
            voice = VoiceListener(Path('test-model'))
            try:
                self.assertEqual(voice.status, ('ready', False))
                sd.RawInputStream.assert_not_called()
                voice.start('incident-1', monotonic() + (0.8 if silence else 5))
                # The speaker echo guard must finish before opening input.
                self.assertFalse(entered.wait(0.1))
                limit = monotonic() + 3
                result = None
                while monotonic() < limit:
                    result = voice.poll()
                    status, listening = voice.status
                    if result or status.startswith('unavailable') or (exited.is_set() and not listening):
                        break
                    sleep(0.01)
                self.assertFalse(voice.status[1])
                if missing or input_error or cancel_during_input or silence:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result.incident_id, 'incident-1')
                    self.assertTrue(exited.is_set())
                    recognizer.SetWords.assert_called_once_with(True)
                    vosk.KaldiRecognizer.assert_called_once_with(vosk.Model.return_value, 48000)
                if missing:
                    sd.RawInputStream.assert_not_called()
                if input_error:
                    self.assertTrue(voice.status[0].startswith('unavailable'))
            finally:
                self.assertTrue(voice.close(timeout=1))

    def test_input_opens_after_guard_and_closes_before_response(self):
        self.run_worker()

    def test_input_failure_never_confirms_response(self):
        self.run_worker(input_error=True)

    def test_missing_model_never_opens_microphone(self):
        self.run_worker(missing=True)

    def test_silence_closes_input_at_deadline_without_response(self):
        self.run_worker(silence=True)

    def test_cancelled_capture_cannot_publish_response(self):
        self.run_worker(cancel_during_input=True)
