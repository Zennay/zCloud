import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import enhancements


class EvidenceProgressTests(unittest.TestCase):
    def test_comparison_schema_is_generic_and_sparse(self):
        latest = enhancements._comparison_point(
            "Nieuwste candidate", "Generation 18", note="development-only", source="trial.json"
        )
        view = enhancements._comparison_view(latest=latest)
        self.assertTrue(view["available"])
        self.assertEqual("Generation 18", view["latest"]["value"])
        self.assertFalse(view["latest"]["validated"])
        self.assertIsNone(view["current"])
        self.assertIsNone(view["best"])

    def test_ftmo_exposes_latest_and_validated_current_without_inventing_best(self):
        cand = (
            18,
            "gen18/trial.json",
            {"trial_hash": "candidate-18"},
            {"total_pnl": 0.0023, "cost_1_5x_pnl": 0.0014, "closed_trades": 80, "win_rate": 0.55},
            {},
        )
        release = (
            17,
            {"paper_release_hash": "release-17"},
            {"trial_hash": "validated-17"},
            {"result": {"total_pnl": 0.0018, "cost_1_5x_pnl": 0.0010, "win_rate": 0.52}},
        )
        with patch.object(enhancements, "_ftmo_candidate", return_value=cand), \
             patch.object(enhancements, "_ftmo_release", return_value=release), \
             patch.object(enhancements, "_ftmo_lifecycle", return_value={}), \
             patch.object(enhancements, "_ftmo_readiness", return_value={"available": True}):
            quality = enhancements.quality_for("ftmo")
        cmp = quality["comparison"]
        self.assertEqual("Generation 18", cmp["latest"]["value"])
        self.assertFalse(cmp["latest"]["validated"])
        self.assertEqual("Generation 17", cmp["current"]["value"])
        self.assertTrue(cmp["current"]["validated"])
        self.assertIsNone(cmp["best"], "Do not invent a best release without a comparable validated criterion")

    def test_ftmo_adapter_tolerates_missing_optional_candidate_metrics(self):
        cand = (
            19,
            "gen19/trial.json",
            {"trial_hash": "candidate-19"},
            {"total_pnl": 0.0011, "cost_1_5x_pnl": 0.0007},
            {},
        )
        with patch.object(enhancements, "_ftmo_candidate", return_value=cand), \
             patch.object(enhancements, "_ftmo_release", return_value=None), \
             patch.object(enhancements, "_ftmo_lifecycle", return_value={}), \
             patch.object(enhancements, "_ftmo_readiness", return_value={"available": False}):
            quality = enhancements.quality_for("ftmo")

        self.assertTrue(quality["available"])
        self.assertEqual("Generation 19", quality["comparison"]["latest"]["value"])
        self.assertEqual(
            ["1.5× cost PnL"],
            [item["label"] for item in quality["items"]],
        )

    def test_ftmo_lifecycle_uses_runtime_status_and_bounded_generation_summaries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            research_root = root / "research_outcomes"
            status_path = root / "status.json"
            for generation, reviews in (
                (28, [{"outcome": "rejected"}, {"outcome": "candidate", "selected_variant": "v2"}]),
                (29, [{"outcome": "candidate", "selected_variant": "v3"}]),
                (30, [{"outcome": "rejected"}]),
            ):
                generation_dir = research_root / f"generation-{generation}"
                generation_dir.mkdir(parents=True)
                (generation_dir / "development-review-summary.json").write_text(
                    json.dumps({
                        "generation_id": f"generation-{generation}",
                        "reviews": reviews,
                    }),
                    encoding="utf-8",
                )
            status_path.write_text(
                json.dumps({
                    "ok": True,
                    "research": {
                        "generation_number": 30,
                        "generation_id": "generation-30",
                        "next_stage": "provider_foundation",
                    },
                    "research_controller": {
                        "action": "continue",
                        "generation_loop": {
                            "stage": "provider_foundation",
                            "action": "provider_foundation_window_not_elapsed",
                        },
                    },
                }),
                encoding="utf-8",
            )

            with patch.object(enhancements, "FTMO_RESEARCH_ROOT", research_root), \
                 patch.object(enhancements, "FTMO_RUNTIME_STATUS_FILE", status_path):
                lifecycle = enhancements._ftmo_lifecycle()

        self.assertEqual(30, lifecycle["generation_number"])
        self.assertEqual("generation-30", lifecycle["generation_id"])
        self.assertEqual([30, 29, 28], lifecycle["recent_generations"])
        self.assertEqual("provider_foundation", lifecycle["next_stage"])
        self.assertEqual("provider_foundation_window_not_elapsed", lifecycle["controller_action"])
        self.assertEqual(4, lifecycle["reviewed_experiments"])
        self.assertEqual(2, lifecycle["selected_candidates"])
        self.assertTrue(lifecycle["runtime_ok"])
        self.assertEqual(str(status_path), lifecycle["source"])

    def test_ftmo_quality_surfaces_lifecycle_without_inventing_best_release(self):
        lifecycle = {
            "generation_number": 30,
            "generation_id": "generation-30",
            "recent_generations": [30, 29, 28],
            "next_stage": "provider_foundation",
            "controller_action": "provider_foundation_window_not_elapsed",
            "reviewed_experiments": 14,
            "selected_candidates": 3,
            "runtime_ok": True,
            "source": "/tmp/ftmo-status.json",
        }
        with patch.object(enhancements, "_ftmo_candidate", return_value=None), \
             patch.object(enhancements, "_ftmo_release", return_value=None), \
             patch.object(enhancements, "_ftmo_lifecycle", return_value=lifecycle), \
             patch.object(enhancements, "_ftmo_readiness", return_value={"available": False}):
            quality = enhancements.quality_for("ftmo")

        metrics = {item["label"]: item for item in quality["items"]}
        self.assertEqual(30, metrics["Runtime generation"]["value"])
        self.assertEqual("30 · 29 · 28", metrics["Recent generations"]["value"])
        self.assertEqual("provider_foundation", metrics["Lifecycle stage"]["value"])
        self.assertEqual(14, metrics["Reviewed experiments"]["value"])
        self.assertEqual(3, metrics["Selected candidates"]["value"])
        self.assertEqual(30, quality["meta"]["runtime_generation"])
        self.assertEqual("provider_foundation", quality["meta"]["runtime_stage"])
        self.assertEqual(
            "not_available_without_comparable_validated_rank",
            quality["meta"]["best_validated_policy"],
        )
        self.assertIsNone(quality["comparison"]["best"])

    def test_registered_project_adapters_are_explicit(self):
        self.assertEqual(("ftmo", "haxlab", "ulab"), enhancements.telemetry_adapter_projects())
        self.assertIsInstance(
            enhancements._TELEMETRY_ADAPTERS["ftmo"],
            enhancements.ProjectTelemetryAdapter,
        )

    def test_ulab_adapter_uses_scorecard_backed_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            projects_path = Path(td) / "projects.json"
            projects_path.write_text(json.dumps([{
                "id": "ulab",
                "phase": "V2 proof gate",
                "milestone_revision": "ulab-score-v2",
                "progress_basis": "Weighted V0–V3 score",
                "progress_override": 38,
                "scorecard_url": "https://example.invalid/ulab-scorecard",
                "milestones": [
                    {"title": "V0 · Core", "progress": 100, "done": True},
                    {"title": "V1 · CLI", "progress": 75, "done": False},
                ],
            }]), encoding="utf-8")
            with patch.object(enhancements, "PROJECTS_FILE", projects_path):
                quality = enhancements.quality_for("ulab")

        self.assertTrue(quality["available"])
        self.assertEqual(38, quality["headline"]["value"])
        self.assertEqual("ulab-score-v2", quality["comparison"]["latest"]["value"])
        self.assertEqual(38, quality["comparison"]["current"]["value"])
        self.assertIsNone(quality["comparison"]["best"])
        metrics = [
            quality["headline"],
            *quality["items"],
            quality["comparison"]["latest"],
            quality["comparison"]["current"],
        ]
        for metric in metrics:
            self.assertEqual(str(projects_path), metric["source"])
            self.assertIsNotNone(datetime.fromisoformat(metric["observed_at"]).tzinfo)
        self.assertEqual(
            "https://example.invalid/ulab-scorecard",
            quality["meta"]["scorecard_url"],
        )

    def test_all_registered_adapter_metrics_have_source_and_timestamp(self):
        snapshot = {
            "available": True,
            "headline": {"label": "Headline", "value": 1},
            "items": [{"label": "Metric", "value": 2}],
            "comparison": enhancements._comparison_view(
                latest=enhancements._comparison_point("Latest", "v1")
            ),
        }
        stamped = enhancements._stamp_snapshot_provenance(snapshot, "synthetic-source")
        metrics = [stamped["headline"], *stamped["items"], stamped["comparison"]["latest"]]
        for metric in metrics:
            self.assertTrue(metric.get("source"))
            observed_at = metric.get("observed_at")
            self.assertTrue(observed_at)
            self.assertIsNotNone(datetime.fromisoformat(observed_at).tzinfo)

    def test_ftmo_adapter_exposes_timestamped_source_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trial_path = root / "trial.json"
            release_path = root / "paper-release.json"
            holdout_path = root / "holdout-run.json"
            for path in (trial_path, release_path, holdout_path):
                path.write_text("{}", encoding="utf-8")
            cand = (
                18,
                trial_path,
                {"trial_hash": "candidate-18"},
                {"total_pnl": 0.0023, "cost_1_5x_pnl": 0.0014, "closed_trades": 80, "win_rate": 0.55},
                {},
            )
            release = (
                17,
                {"paper_release_hash": "release-17", "_zcloud_source_path": str(release_path)},
                {"trial_hash": "validated-17"},
                {
                    "_zcloud_source_path": str(holdout_path),
                    "result": {"total_pnl": 0.0018, "cost_1_5x_pnl": 0.0010, "win_rate": 0.52},
                },
            )
            with patch.object(enhancements, "_ftmo_candidate", return_value=cand), \
                 patch.object(enhancements, "_ftmo_release", return_value=release), \
                 patch.object(enhancements, "_ftmo_lifecycle", return_value={}), \
                 patch.object(enhancements, "_ftmo_readiness", return_value={"available": True}):
                comparison = enhancements.quality_for("ftmo")["comparison"]

        self.assertEqual(str(trial_path), comparison["latest"]["source"])
        self.assertEqual(str(holdout_path), comparison["current"]["source"])
        for key in ("latest", "current"):
            observed_at = comparison[key].get("observed_at")
            self.assertTrue(observed_at, key)
            self.assertIsNotNone(datetime.fromisoformat(observed_at).tzinfo)

    def test_all_project_progress_has_documented_checkpoint_basis(self):
        projects = json.loads(Path("projects.json").read_text(encoding="utf-8"))
        self.assertTrue(projects)
        for project in projects:
            with self.subTest(project=project["id"]):
                self.assertTrue(project.get("milestone_revision"), "progress must identify its checkpoint revision")
                self.assertTrue(project.get("progress_basis"), "progress must explain what the percentage means")
                if "progress_override" in project:
                    self.assertTrue(
                        project.get("scorecard_url") or project.get("notion_url"),
                        "an override must point at a documented scorecard/source",
                    )

    def test_unknown_project_has_empty_comparison_contract(self):
        quality = enhancements.quality_for("unknown")
        self.assertFalse(quality["comparison"]["available"])
        self.assertIsNone(quality["comparison"]["latest"])
        self.assertIsNone(quality["comparison"]["current"])
        self.assertIsNone(quality["comparison"]["best"])

    def test_server_quality_hook_is_project_agnostic(self):
        server = Path("server.py").read_text(encoding="utf-8")
        self.assertIn("enhancements.quality_for(p['id'])", server)
        self.assertNotIn("enhancements.quality_for('haxlab')", server)
        self.assertNotIn("enhancements.quality_for('ftmo')", server)

    def test_vps_proof_checks_out_exact_pr_head_before_identity_gate(self):
        workflow = Path(
            ".github/workflows/zcloud-project-telemetry-adapters-proof.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            workflow,
        )
        self.assertIn("Prove live FTMO lifecycle telemetry is read-only", workflow)
        self.assertIn("ZCLOUD_FTMO_LIFECYCLE_TELEMETRY_READONLY_GREEN=1", workflow)
        self.assertIn("status_path.stat().st_mtime_ns", workflow)
        self.assertNotIn("status_path.write_", workflow)

    def test_renderer_is_project_agnostic(self):
        js = Path("public/enhancements.js").read_text(encoding="utf-8")
        start = js.index("function evidencePanel")
        end = js.index("function qPanel", start)
        renderer = js[start:end]
        self.assertNotIn("p.id===", renderer)
        self.assertNotIn("ftmo", renderer.lower())
        self.assertNotIn("haxlab", renderer.lower())
        self.assertIn("latest", renderer)
        self.assertIn("current", renderer)
        self.assertIn("best", renderer)
        self.assertIn("Current bottleneck", renderer)
        self.assertIn("observed_at", renderer)


if __name__ == "__main__":
    unittest.main(verbosity=2)