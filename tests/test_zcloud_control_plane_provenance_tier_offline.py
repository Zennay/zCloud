import unittest
from scripts.zcloud_control_plane_provenance_tier_offline import classify_provenance


class ProvenanceTierTests(unittest.TestCase):
    def test_all_known_sources_remain_non_authorizing(self):
        for source in ("notion_claim", "github_check", "github_main_status",
                       "vps_process", "vps_sqlite", "runtime_generation"):
            for success in (True, False):
                with self.subTest(source=source, success=success):
                    result = classify_provenance({"source": source, "success": success})
                    self.assertFalse(result["authenticated"])
                    self.assertFalse(result["authorizes_restart"])
                    self.assertFalse(result["authorizes_deploy"])
                    self.assertFalse(result["authorizes_queue_write"])

    def test_ci_cannot_impersonate_deployment(self):
        result = classify_provenance({"source": "github_check", "success": True,
                                      "deployed": True, "authenticated": True})
        self.assertEqual(result["class"], "ci_only")
        self.assertFalse(result["authorizes_deploy"])

    def test_notion_claim_not_generation(self):
        result = classify_provenance({"source": "notion_claim", "success": True,
                                      "generation_started": True})
        self.assertEqual(result["class"], "claim_only")

    def test_runtime_source_is_not_authenticated_by_self_claim(self):
        result = classify_provenance({"source": "runtime_generation", "success": True,
                                      "identity": "trusted-vps"})
        self.assertEqual(result["class"], "generation_claim_only")
        self.assertFalse(result["authenticated"])

    def test_invalid_shapes_fail_closed(self):
        for value in (None, [], "ok", {}, {"source": "github_check"},
                      {"source": "github_check", "success": 1},
                      {"source": "github_check", "success": "true"},
                      {"source": "production", "success": True}):
            with self.subTest(value=value):
                self.assertEqual(classify_provenance(value)["class"], "unknown")

    def test_negative_observations_do_not_get_positive_class(self):
        self.assertEqual(classify_provenance(
            {"source": "github_main_status", "success": False})["class"],
            "negative_observation")


if __name__ == "__main__":
    unittest.main()
