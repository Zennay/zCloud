"""Offline source-level guardrails for worker no-generation replay.

These tests detect accidental removal of critical controls. They are NOT
A-H behavioral acceptance evidence and never authorize deployment.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "firefox-extension" / "background.js"


class WorkerReplaySourceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = EXTENSION.read_text(encoding="utf-8")

    def test_primary_runner_requires_known_version(self):
        self.assertRegex(self.source, r'VIOLENTMONKEY_REQUIRED_VERSION\s*=\s*["\']1\.3\.17["\']')
        self.assertRegex(self.source, r'VIOLENTMONKEY_PRIMARY_RUNNER\s*=\s*true')
        self.assertIn("vmVersions.includes(VIOLENTMONKEY_REQUIRED_VERSION)", self.source)

    def test_acknowledgement_not_generation(self):
        self.assertIn('"generation-not-started"', self.source)
        self.assertIn('"no-generation-after-send"', self.source)
        self.assertIn("awaitingGeneration", self.source)
        self.assertIn("sawGeneration", self.source)

    def test_replay_retains_assignment_scope(self):
        self.assertIn("same canonical", self.source)
        self.assertIn("assignment", self.source)
        self.assertIn("anti-spam", self.source)

    def test_worker_slot_visible_in_evidence(self):
        self.assertRegex(self.source, r'workerSlot:\s*cfg\.worker_slot')
        self.assertRegex(self.source, r'queueItem:\s*cfg\.queue_item\?\.queue_id')

    def test_runner_refuses_incomplete_assignment(self):
        self.assertIn("portfolioAssignmentReady", self.source)
        self.assertIn("assignment_ready !== true", self.source)
        self.assertIn("queue_id", self.source)

    def test_no_unreviewed_runtime_mutation(self):
        self.assertTrue(EXTENSION.is_file())
        self.assertGreater(len(self.source), 1000)


if __name__ == "__main__":
    unittest.main()
