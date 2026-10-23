"""Fix #1326 follow-on: release must actually publish newly registered project choices.

Source-only and pre-write contract checks. Never touches a VPS, DB or workers.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import zcloud_transactional_promote as promote

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-catalog-activation-20261016.yml"
PRODUCTION = ROOT / ".github" / "workflows" / "zcloud-vps-deploy.yml"


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

    def test_manual_workflow_serially_promotes_catalog_after_owner_preflight(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        for binding in (
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "test \"$CONFIRMED_SHA\" = \"$GITHUB_SHA\"",
            "git ls-remote --exit-code https://github.com/Zennay/zCloud.git",
            "scripts/zcloud_vps_runner_guard.py",
        ):
            with self.subTest(binding=binding):
                self.assertIn(binding, text)
        first = text.index("- name: Promote the audited project catalog and matching autonomy contracts")
        role = text.index("- name: Publish the zSentinel worker-role metadata")
        assert_status = text.index("- name: Verify real runtime project and worker role inventory")
        self.assertLess(first, role)
        self.assertLess(role, assert_status)
        catalog_section = text[first:role]
        self.assertIn("--path projects.json", catalog_section)
        self.assertIn("--path project-contracts.json", catalog_section)
        self.assertIn("--promote-reviewed-project-catalog", catalog_section)
        self.assertNotIn("--path project-layout.json", catalog_section)
        self.assertIn("--preserve-schema-validated-runtime-config", catalog_section)
        roles_section = text[role:assert_status]
        self.assertIn("--path worker-modes.json", roles_section)
        self.assertIn("--preserve-schema-validated-runtime-config", roles_section)
        self.assertIn("group: zcloud-production-deploy", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("zcloud_deploy_safe_idle.py enter", text)
        self.assertIn("zcloud_deploy_safe_idle.py restore", text)
        self.assertIn("if: always()", text)
        self.assertNotIn("schedule:", text)

    def test_automatic_production_deploy_remains_unchanged_and_excludes_catalog(self):
        text = PRODUCTION.read_text(encoding="utf-8")
        self.assertNotIn("--path projects.json", text)
        self.assertNotIn("--path project-layout.json", text)
        self.assertNotIn("--path worker-modes.json", text)
        self.assertIn("group: zcloud-production-deploy", text)

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
