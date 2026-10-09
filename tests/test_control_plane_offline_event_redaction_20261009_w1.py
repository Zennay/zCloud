"""Tests for the standalone (non-production) allowlist log reference."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "control_plane_offline_event_redaction_w1.py"
spec = importlib.util.spec_from_file_location("control_plane_offline_event_redaction_w1", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = {"event": "dispatch", "project": "zCloud", "worker": "w1", "status": "running", "timestamp": "2026-10-09T02:00:00Z"}


class RedactionBoundaryTests(unittest.TestCase):
    def test_clean_event_survives(self):
        self.assertEqual(mod.redact_control_event(BASE), BASE)

    def test_unknown_nested_fields_are_never_emitted(self):
        event = {**BASE, "headers": {"Authorization": "Bearer secret"},
                 "prompt": "secret", "cookies": "session", "error": "secret",
                 "environment": {"TOKEN": "secret"}, "url": "https://user:pass@example.com"}
        self.assertEqual(mod.redact_control_event(event), BASE)
        self.assertNotIn("secret", repr(mod.redact_control_event(event)))

    def test_reject_nonmapping(self):
        for value in (None, [], "hello", 42):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.redact_control_event(value)

    def test_missing_fields_fail_closed(self):
        for field in BASE:
            with self.subTest(field=field), self.assertRaises(ValueError):
                mod.redact_control_event({k: v for k, v in BASE.items() if k != field})

    def test_non_string_fields_fail_closed(self):
        for field in BASE:
            with self.subTest(field=field), self.assertRaises(ValueError):
                mod.redact_control_event({**BASE, field: ["leak"]})

    def test_injection_in_identifiers_fails_closed(self):
        for value in ("hello world", "line\nsecret", "x" * 97, "ø", "../secret", "a=b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.redact_control_event({**BASE, "worker": value})

    def test_status_is_enumerated(self):
        with self.assertRaises(ValueError):
            mod.redact_control_event({**BASE, "status": "Bearer-secret"})

    def test_timestamp_is_utc_second_precision(self):
        for value in ("2026-10-09T02:00:00+01:00", "2026-10-09", "secret", "2026-10-09T02:00:00Z\nTOKEN"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.redact_control_event({**BASE, "timestamp": value})


if __name__ == "__main__":
    unittest.main()
