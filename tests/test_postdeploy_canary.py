import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_postdeploy_canary.py"
SPEC = importlib.util.spec_from_file_location("zcloud_postdeploy_canary", MODULE_PATH)
canary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(canary)


class PostdeployCanaryTests(unittest.TestCase):
    def sample(self):
        status = {
            "errors": [],
            "chatgpt_firefox": {"state": "active", "active": True},
            "chatgpt_runners": {
                "cloud": {
                    "workers": [],
                    "desired_worker_count": 0,
                    "active_worker_count": 0,
                }
            },
            "incidents": {"items": [], "count": 0},
            "dynamic_workers": {"count": 8},
        }
        targets = {
            "projects": {"cloud::w1": {}},
            "max_workers": 8,
            "global_allocation": {"workers": []},
        }
        mapping = {
            "available": True,
            "sha256": "abc",
            "targets": 1,
            "workers": 1,
            "dynamic_worker_limit": 8,
        }
        return status, targets, mapping

    def evaluate(self, **kwargs):
        status, targets, mapping = self.sample()
        return canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            **kwargs,
        )

    def test_status_readiness_retries_transient_failure(self):
        attempts = [
            RuntimeError("HTTP 503: warming up"),
            {"errors": [], "dynamic_workers": {"count": 5}},
        ]
        ticks = iter([0.0, 0.1])
        with mock.patch.object(canary, "http_json", side_effect=attempts) as fetch:
            result = canary.http_json_ready(
                "http://127.0.0.1:8765/api/status",
                readiness_seconds=30,
                retry_interval=0.5,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: next(ticks),
            )
        self.assertEqual([], result["errors"])
        self.assertEqual(2, fetch.call_count)

    def test_status_readiness_default_allows_service_budget_warmup(self):
        attempts = [
            RuntimeError("timed out during sampler warmup"),
            {"errors": [], "dynamic_workers": {"count": 5}},
        ]
        ticks = iter([0.0, 31.0])
        with mock.patch.object(canary, "http_json", side_effect=attempts) as fetch:
            result = canary.http_json_ready(
                "http://127.0.0.1:8765/api/status",
                retry_interval=0.5,
                sleep_fn=lambda _: None,
                monotonic_fn=lambda: next(ticks),
            )
        self.assertEqual([], result["errors"])
        self.assertEqual(2, fetch.call_count)
        self.assertEqual(40.0, fetch.call_args_list[0].kwargs["timeout"])

    def test_status_readiness_persistent_failure_stays_fail_closed(self):
        ticks = iter([0.0, 31.0])
        with mock.patch.object(
            canary,
            "http_json",
            side_effect=RuntimeError("HTTP 503: still unavailable"),
        ) as fetch:
            with self.assertRaisesRegex(RuntimeError, "still unavailable"):
                canary.http_json_ready(
                    "http://127.0.0.1:8765/api/status",
                    readiness_seconds=30,
                    retry_interval=0.5,
                    sleep_fn=lambda _: None,
                    monotonic_fn=lambda: next(ticks),
                )
        self.assertEqual(1, fetch.call_count)

    def test_green_baseline(self):
        self.assertTrue(self.evaluate()["ok"])

    def test_intentional_violentmonkey_only_mode_allows_legacy_firefox_inactive(self):
        status, targets, mapping = self.sample()
        status["chatgpt_firefox"] = {"state": "inactive", "active": False}
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": False},
            static_assets_ok=True,
            source_runtime_match=False,
            legacy_firefox_disabled=True,
        )
        checks = {x["name"]: x for x in result["checks"]}
        self.assertTrue(result["ok"], result)
        self.assertTrue(checks["firefox_service"]["ok"])
        self.assertTrue(checks["firefox_runtime_active"]["ok"])
        self.assertTrue(checks["firefox_source_runtime_match"]["ok"])

    def test_inactive_legacy_firefox_without_explicit_disable_still_blocks(self):
        status, targets, mapping = self.sample()
        status["chatgpt_firefox"] = {"state": "inactive", "active": False}
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": False},
            static_assets_ok=True,
            source_runtime_match=False,
            legacy_firefox_disabled=False,
        )
        self.assertFalse(result["ok"])

    def test_blocks_service_failure(self):
        status, targets, mapping = self.sample()
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": False, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])

    def test_blocks_status_errors(self):
        status, targets, mapping = self.sample()
        status["errors"] = ["boom"]
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])

    def test_dynamic_worker_limit_mismatch_is_fail_closed(self):
        status, targets, mapping = self.sample()
        mapping["dynamic_worker_limit"] = 3
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        checks = {x["name"]: x for x in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["dynamic_worker_limit_consistent"]["ok"])

    def test_dynamic_worker_allocation_must_stay_within_limit(self):
        status, targets, mapping = self.sample()
        targets["max_workers"] = 1
        status["dynamic_workers"]["count"] = 1
        mapping["dynamic_worker_limit"] = 1
        targets["global_allocation"]["workers"] = [
            {"global_worker_slot": 1},
            {"global_worker_slot": 2},
        ]
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        checks = {x["name"]: x for x in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["dynamic_worker_allocation_bounded"]["ok"])

    def test_mapping_expectation_is_fail_closed(self):
        result = self.evaluate(expected_mapping_sha="different")
        checks = {x["name"]: x for x in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["mapping_unchanged"]["ok"])

    def test_worker_read_model_can_be_required(self):
        self.assertTrue(self.evaluate(require_worker_read_model=True)["ok"])
        status, targets, mapping = self.sample()
        del status["chatgpt_runners"]["cloud"]["workers"]
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            require_worker_read_model=True,
        )
        self.assertFalse(result["ok"])

    def test_incident_center_can_be_required(self):
        self.assertTrue(self.evaluate(require_incidents=True)["ok"])
        status, targets, mapping = self.sample()
        status.pop("incidents")
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            require_incidents=True,
        )
        self.assertFalse(result["ok"])

    def test_firefox_runtime_parity_covers_manifest_and_recovery_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            runtime = Path(tmp) / "runtime"
            (root / "firefox-extension").mkdir(parents=True)
            runtime.mkdir()
            for name, value in (
                ("background.js", "bg\n"),
                ("manifest.json", "{}\n"),
                ("recovery.js", "helper\n"),
            ):
                (root / "firefox-extension" / name).write_text(value)
                (runtime / name).write_text(value)
            result = canary.firefox_runtime_parity(
                root,
                runtime / "background.js",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(
                ["background.js", "manifest.json", "recovery.js"],
                result["checked"],
            )

    def test_firefox_runtime_parity_blocks_missing_recovery_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            runtime = Path(tmp) / "runtime"
            (root / "firefox-extension").mkdir(parents=True)
            runtime.mkdir()
            (root / "firefox-extension/background.js").write_text("bg\n")
            (root / "firefox-extension/manifest.json").write_text("{}\n")
            (root / "firefox-extension/recovery.js").write_text("helper\n")
            (runtime / "background.js").write_text("bg\n")
            (runtime / "manifest.json").write_text("{}\n")
            result = canary.firefox_runtime_parity(
                root,
                runtime / "background.js",
            )
            self.assertFalse(result["ok"])
            self.assertEqual("recovery.js", result["mismatches"][0]["file"])

    def test_mapping_fingerprint_matches_recovery_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "history.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE runner_targets("
                    "project_id TEXT, active INTEGER, worker_count INTEGER, conversation_id TEXT)"
                )
                conn.execute(
                    "CREATE TABLE runner_workers("
                    "project_id TEXT, worker_slot INTEGER, conversation_id TEXT)"
                )
                conn.execute(
                    "INSERT INTO runner_targets VALUES('cloud',1,2,'conversation-a')"
                )
                conn.execute(
                    "INSERT INTO runner_workers VALUES('cloud',1,'conversation-a')"
                )
                conn.execute(
                    "INSERT INTO runner_workers VALUES('cloud',2,'conversation-b')"
                )
            result = canary.mapping_fingerprint(db)
            payload = json.dumps({
                "targets": [{
                    "project_id": "cloud",
                    "conversation_id": "conversation-a",
                }],
                "workers": [
                    {"project_id": "cloud", "worker_slot": 1, "conversation_id": "conversation-a"},
                    {"project_id": "cloud", "worker_slot": 2, "conversation_id": "conversation-b"},
                ],
            }, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE runner_targets SET active=0, worker_count=7 WHERE project_id='cloud'"
                )
            self.assertEqual(result["sha256"], canary.mapping_fingerprint(db)["sha256"])
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE runner_targets SET conversation_id='conversation-c' WHERE project_id='cloud'"
                )
            self.assertNotEqual(result["sha256"], canary.mapping_fingerprint(db)["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
