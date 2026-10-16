"""Fix #1326 follow-on: release must actually publish newly registered project choices.

Source-only and pre-write contract checks. Never touches a VPS, DB or workers.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import zcloud_transactional_promote as promote

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-vps-deploy.yml"


class _ReachedCandidateValidation(Exception):
    pass


class LiveCatalogPromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.kwargs = dict(
            candidate=root / "candidate",
            root=root / "live",
            state=root / "lkg",
            paths=["projects.json", "project-contracts.json"],
            preserve_schema_validated_runtime_config=True,
        )

    def test_no_implicit_catalog_mutation_during_code_only_promotion(self):
        with self.assertRaisesRegex(promote.PromotionError, "cannot select files"):
            promote.promote(**self.kwargs)

    def test_catalog_is_not_allowed_without_layout_preservation(self):
        kwargs = dict(self.kwargs, promote_reviewed_project_catalog=True)
        kwargs["preserve_schema_validated_runtime_config"] = False
        with self.assertRaisesRegex(promote.PromotionError, "requires both"):
            promote.promote(**kwargs)

    def test_catalog_does_not_allow_promoting_operator_layout(self):
        kwargs = dict(self.kwargs, promote_reviewed_project_catalog=True)
        kwargs["paths"] = [*kwargs["paths"], "project-layout.json"]
        with self.assertRaisesRegex(promote.PromotionError, "project-layout.json"):
            promote.promote(**kwargs)

    def test_catalog_requires_matching_runtime_contract(self):
        kwargs = dict(self.kwargs, promote_reviewed_project_catalog=True)
        kwargs["paths"] = ["projects.json"]
        with self.assertRaisesRegex(promote.PromotionError, "matching project-contracts"):
            promote.promote(**kwargs)

    def test_audited_catalog_and_contract_pair_reaches_candidate_validation(self):
        kwargs = dict(self.kwargs, promote_reviewed_project_catalog=True)
        with patch.object(promote, "validate_candidate", side_effect=_ReachedCandidateValidation):
            with self.assertRaises(_ReachedCandidateValidation):
                promote.promote(**kwargs)

    def test_workflow_serially_promotes_real_catalog_and_worker_modes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        first = text.index("- name: Promote audited project catalog with matching runtime contracts")
        backend = text.index("- name: Promote backend runtime core")
        role = text.index("- name: Promote zSentinel worker-mode registry")
        seed = text.index("- name: Promote autonomy policies and queue seed")
        self.assertLess(first, backend)
        self.assertLess(backend, role)
        self.assertLess(role, seed)
        catalog_section = text[first:backend]
        self.assertIn("--path projects.json", catalog_section)
        self.assertIn("--path project-contracts.json", catalog_section)
        self.assertIn("--promote-reviewed-project-catalog", catalog_section)
        self.assertIn("--preserve-schema-validated-runtime-config", catalog_section)
        self.assertNotIn("--path project-layout.json", catalog_section)
        roles_section = text[role:seed]
        self.assertIn("--path worker-modes.json", roles_section)
        self.assertIn("--preserve-schema-validated-runtime-config", roles_section)
        self.assertNotIn("--path projects.json", roles_section)
        self.assertIn("concurrency:", text)
        self.assertIn("group: zcloud-production-deploy", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)

    def test_sentinel_and_new_projects_have_noncolliding_catalog_entries(self):
        import json
        projects = json.loads((ROOT / "projects.json").read_text())
        role_metadata = json.loads((ROOT / "worker-modes.json").read_text())
        project_ids = [p["id"] for p in projects]
        role_ids = [p["id"] for p in role_metadata]
        self.assertTrue({"tradengine", "mixlab", "fuckball", "tuddy"} <= set(project_ids))
        self.assertEqual(role_ids.count("zsentinel"), 1)
        self.assertNotIn("zsentinel", project_ids)
        self.assertEqual(len(project_ids), len(set(project_ids)))


if __name__ == "__main__":
    unittest.main()
