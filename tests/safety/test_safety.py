import unittest

from fall_detection.contracts import DetectionEvent, DetectionSource, EventKind, FaultCode
from fall_detection.safety import EffectKind, SafetyController, SafetyState


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.safety = SafetyController(5, clock=self.clock)

    def fall(self, event_id="fall", timestamp=None):
        return DetectionEvent(event_id, self.clock.now if timestamp is None else timestamp,
                              DetectionSource.SIMULATED, EventKind.POSSIBLE_FALL)

    def test_expiry_emits_one_prompt_and_one_alert_despite_repeated_evidence(self):
        self.assertTrue(self.safety.observe(self.fall()))
        self.assertEqual(self.safety.state, SafetyState.SUSPECTED_FALL)
        incident = self.safety.incident
        self.assertEqual([effect.kind for effect in self.safety.tick()], [EffectKind.PROMPT])
        self.clock.now = 12
        self.assertFalse(self.safety.observe(self.fall("another")))
        self.assertEqual(self.safety.remaining(), 3)
        self.clock.now = 15
        effects = self.safety.tick()
        self.assertEqual([effect.kind for effect in effects], [EffectKind.ALERT])
        self.assertEqual(effects[0].incident, incident)
        self.assertEqual(self.safety.delivery, "pending")
        for _ in range(10):
            self.safety.observe(self.fall())
            self.assertEqual(self.safety.tick(), ())
        self.assertFalse(self.safety.reset())

    def test_cancel_immediately_before_expiry_prevents_alert_and_requires_reset(self):
        self.safety.observe(self.fall())
        self.safety.tick()
        self.clock.now = 14.999999
        self.assertTrue(self.safety.cancel())
        self.clock.now = 100
        self.assertEqual(self.safety.tick(), ())
        self.assertFalse(self.safety.observe(self.fall()))
        self.assertTrue(self.safety.reset())
        self.assertFalse(self.safety.observe(self.fall(timestamp=99)))
        self.clock.now = 101
        self.assertTrue(self.safety.observe(self.fall("fresh")))

    def test_cancel_at_deadline_cannot_retract_alert(self):
        self.safety.observe(self.fall())
        self.clock.now = 15
        self.assertFalse(self.safety.cancel())
        self.assertEqual([effect.kind for effect in self.safety.tick()], [EffectKind.PROMPT, EffectKind.ALERT])
        self.assertFalse(self.safety.cancel())

    def test_fault_does_not_cancel_incident_or_change_deadline(self):
        self.safety.observe(self.fall())
        incident = self.safety.incident
        fault = DetectionEvent("fault", 11, DetectionSource.SIMULATED, EventKind.FAULT,
                               fault_code=FaultCode.CAMERA_FAILURE)
        self.clock.now = 11
        self.assertFalse(self.safety.observe(fault))
        self.assertEqual(self.safety.incident, incident)
        self.safety.tick()
        self.clock.now = 15
        self.assertEqual(self.safety.tick()[0].kind, EffectKind.ALERT)

    def test_only_matching_delivery_result_unlocks_rearm(self):
        self.safety.observe(self.fall())
        self.safety.tick()
        self.clock.now = 15
        self.safety.tick()
        self.safety.delivery_result("different incident", "failed")
        self.assertFalse(self.safety.reset())
        self.safety.delivery_result(self.safety.incident.incident_id, "uncertain")
        self.assertTrue(self.safety.reset())
        self.assertFalse(self.safety.observe(self.fall(timestamp=10)))
        self.clock.now = 16
        self.assertTrue(self.safety.observe(self.fall("new incident")))
        self.assertNotEqual(self.safety.incident.incident_id, "fall")

    def test_future_clock_domain_and_invalid_countdowns_are_rejected(self):
        self.assertFalse(self.safety.observe(self.fall(timestamp=999)))
        for duration in (0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                SafetyController(duration)
