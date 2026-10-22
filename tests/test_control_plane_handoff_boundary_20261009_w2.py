"""Static regression for the non-authorizing zCloud control-plane handoff.

This test never connects to production, imports a worker, or reads SQLite.
"""
from pathlib import Path
import unittest


DOCUMENT = (
    Path(__file__).resolve().parents[1]
    / "docs/control-plane-ownership-evidence-handoff-20261009-0350-w2.md"
)


class ControlPlaneHandoffBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = DOCUMENT.read_text(encoding="utf-8")

    def test_requires_immutable_identity(self):
        for field in (
            "repo/main_sha:",
            "pr_number/head_sha/base_sha:",
            "workflow_run_id/attempt/job_id:",
            "runner_identity/environment:",
            "source_of_each_fact:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.document)

    def test_requires_separate_observation_timestamps(self):
        for field in (
            "service_status_observed_at:",
            "sqlite_queue_observed_at:",
            "worker_generation_id/heartbeat_observed_at:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.document)

    def test_no_mutation_or_granted_authority(self):
        self.assertIn("production_mutation_performed: false", self.document)
        self.assertIn("authority_granted: false", self.document)
        self.assertIn("No claim-release, global worker refresh", self.document)

    def test_serialized_ownership_links_present(self):
        for pr in ("pull/580", "pull/1089", "pull/1034", "pull/1143"):
            with self.subTest(pr=pr):
                self.assertIn(pr, self.document)

    def test_explicit_non_authorizing_boundary(self):
        self.assertIn("non-authorizing", self.document.lower())
        self.assertIn("not execute checks or change the control plane", self.document)


if __name__ == "__main__":
    unittest.main()
