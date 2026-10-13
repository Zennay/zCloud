"""Offline denial matrix for one-shot Force v2 addon idle activation."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "zcloud_force_v2_idle_activation_20261013.py"
spec = importlib.util.spec_from_file_location("idle_v2_activation", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def observation(**patch):
    worker = {
        "active": True, "worker_id": "supa::w1",
        "generating": False, "sending": False,
        "desired_state": "running", "command": {"status": "completed"},
        "last_event": {"event": "heartbeat"}, "age_seconds": 6,
    }
    worker.update(patch)
    return {"chatgpt_runners": {"supa": {"workers": [worker]}}}


class IdleAddonActivationTests(unittest.TestCase):
    def test_safe_idle(self):
        self.assertEqual(m.safe_snapshot(observation()), frozenset({"supa::w1"}))

    def test_deny_generating(self):
        with self.assertRaisesRegex(ValueError, "generating"):
            m.safe_snapshot(observation(generating=True))

    def test_deny_sending(self):
        with self.assertRaisesRegex(ValueError, "sending"):
            m.safe_snapshot(observation(sending=True))

    def test_deny_unknown_busy_flag(self):
        with self.assertRaisesRegex(ValueError, "unverified"):
            m.safe_snapshot(observation(generating=None))

    def test_deny_pending(self):
        with self.assertRaisesRegex(ValueError, "pending"):
            m.safe_snapshot(observation(command={"status": "pending"}))

    def test_deny_draining(self):
        with self.assertRaisesRegex(ValueError, "draining"):
            m.safe_snapshot(observation(desired_state="draining"))

    def test_deny_unverified_recent_prompt(self):
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            m.safe_snapshot(observation(last_event={"event": "prompt-sent"}, age_seconds=25))

    def test_deny_duplicate(self):
        payload = observation()
        payload["chatgpt_runners"]["supa"]["workers"].append(dict(
            payload["chatgpt_runners"]["supa"]["workers"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.safe_snapshot(payload)

    def test_deny_no_active(self):
        with self.assertRaisesRegex(ValueError, "no-active"):
            m.safe_snapshot(observation(active=False))

    def test_deny_wrong_schema(self):
        with self.assertRaisesRegex(ValueError, "missing-runners"):
            m.safe_snapshot({})

    def test_no_unsafe_mutations_or_firefox_process_restart(self):
        content = SOURCE.read_text()
        for forbidden in ("runner-control", "runner-command-result",
                          "systemctl restart firefox", "kill -9", "browser.tabs.remove",
                          "sqlite3.connect", "DROP TABLE", "pkill"):
            self.assertNotIn(forbidden, content)
        self.assertIn("fcntl.flock", content)
        self.assertIn("if one != two", content)
        self.assertIn("if two != three", content)
        self.assertIn("FORCE_V2_ADDON_ACTIVATED_WITHOUT_FIREFOX_RESTART=1", content)


if __name__ == "__main__":
    unittest.main()
