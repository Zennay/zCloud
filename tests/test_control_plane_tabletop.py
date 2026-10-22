"""Offline, deterministic safety contract for the control-plane tabletop classifier."""
import unittest

from scripts.zcloud_control_plane_tabletop import classify


class TabletopTests(unittest.TestCase):
    def test_queue_pressure_is_not_outage(self):
        result = classify(dict(provenance="trusted-main", service="healthy",
                               api="healthy", generation="healthy", queued_jobs=150))
        self.assertEqual(result["classification"], "healthy-with-backlog")
        self.assertFalse(result["production_mutation_authorized"])

    def test_transient_http_only_rechecks(self):
        result = classify(dict(provenance="trusted-main", service="healthy", api="unhealthy"))
        self.assertEqual(result["next_step"], "bounded-read-only-recheck")
        self.assertFalse(result["production_mutation_authorized"])

    def test_stalled_generation_never_claims_success(self):
        result = classify(dict(provenance="trusted-main", generation="unhealthy"))
        self.assertEqual(result["classification"], "generation-unconfirmed")

    def test_pr_checks_do_not_claim_production(self):
        result = classify(dict(provenance="pr-only", service="healthy", api="healthy",
                               generation="healthy"))
        self.assertEqual(result["classification"], "insufficient-production-evidence")

    def test_writer_window_wins_over_healthy_telemetry(self):
        result = classify(dict(provenance="trusted-main", service="healthy", api="healthy",
                               generation="healthy", serialized_writer_active=True))
        self.assertEqual(result["next_step"], "respect-owner-gate")

    def test_invalid_values_fail_closed(self):
        for payload in ({"queued_jobs": -1}, {"queued_jobs": True},
                        {"api": "green"}, {"provenance": "untrusted"},
                        {"serialized_writer_active": "false"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                classify(payload)

    def test_all_outcomes_are_non_mutating(self):
        for service in ("healthy", "unhealthy", "unknown"):
            for api in ("healthy", "unhealthy", "unknown"):
                for generation in ("healthy", "unhealthy", "unknown"):
                    output = classify({"provenance": "trusted-main", "service": service,
                                       "api": api, "generation": generation})
                    self.assertIs(output["production_mutation_authorized"], False)


if __name__ == "__main__":
    unittest.main()
