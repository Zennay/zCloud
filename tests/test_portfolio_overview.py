from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_portfolio_overview", ROOT / "scripts" / "zcloud_portfolio_overview.py"
)
overview = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(overview)


class PortfolioOverviewTests(unittest.TestCase):
    def fixture(self):
        return overview.build_overview(
            {
                "chatgpt_runners": {
                    "cloud": {"active_worker_count": 2, "desired_worker_count": 2},
                    "ftmo": {"active_worker_count": 0, "desired_worker_count": 1, "attention_worker_count": 0},
                }
            },
            {
                "items": [
                    {"queue_id": "SECRET_Q1", "project_id": "cloud", "status": "running", "title": "SECRET_TITLE"},
                    {"queue_id": "SECRET_Q2", "project_id": "ftmo", "status": "blocked", "title": "SECRET_BLOCKER"},
                ]
            },
            {
                "items": [
                    {
                        "attention_id": "SECRET_A",
                        "project_id": "ftmo",
                        "status": "open",
                        "severity": "urgent",
                        "action": "SECRET_ACTION",
                        "detail": "SECRET_DETAIL",
                    }
                ]
            },
            {
                "cloud": {
                    "managed": True,
                    "active_units": 1,
                    "cpu_percent": 12.5,
                    "memory_bytes": 1024,
                    "priority": "high",
                },
                "ftmo": {
                    "managed": True,
                    "active_units": 0,
                    "cpu_percent": 0,
                    "memory_bytes": 0,
                    "priority": "normal",
                },
                "_summary": {"host_cpu_percent": 50},
            },
        )

    def test_compacts_running_resource_blocked_and_attention_state(self):
        result = self.fixture()
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(
            {"projects": 2, "running": 1, "using_resources": 1, "blocked": 1, "needs_attention": 1},
            result["summary"],
        )
        by_id = {item["project_id"]: item for item in result["projects"]}
        self.assertTrue(by_id["cloud"]["running"])
        self.assertTrue(by_id["cloud"]["uses_resources"])
        self.assertFalse(by_id["cloud"]["needs_attention"])
        self.assertTrue(by_id["ftmo"]["blocked"])
        self.assertEqual("urgent", by_id["ftmo"]["attention_severity"])
        self.assertTrue(by_id["ftmo"]["needs_attention"])

    def test_output_omits_titles_ids_actions_and_details(self):
        encoded = json.dumps(self.fixture(), sort_keys=True)
        for secret in ("SECRET_Q1", "SECRET_Q2", "SECRET_A", "SECRET_TITLE", "SECRET_BLOCKER", "SECRET_ACTION", "SECRET_DETAIL"):
            self.assertNotIn(secret, encoded)

    def test_runner_attention_requests_attention(self):
        result = overview.build_overview(
            {"chatgpt_runners": {"cloud": {"active_worker_count": 1, "desired_worker_count": 1, "attention_worker_count": 1}}},
            {"items": []},
            {"items": []},
            {"cloud": {}},
        )
        item = result["projects"][0]
        self.assertEqual(1, item["attention_workers"])
        self.assertTrue(item["needs_attention"])

    def test_failed_queue_item_requests_attention_without_claiming_blocked(self):
        result = overview.build_overview(
            {"chatgpt_runners": {"supa": {"active_worker_count": 1, "desired_worker_count": 1}}},
            {"items": [{"project_id": "supa", "status": "failed"}]},
            {"items": []},
            {"supa": {}},
        )
        item = result["projects"][0]
        self.assertFalse(item["blocked"])
        self.assertTrue(item["needs_attention"])
        self.assertEqual(1, item["queue"]["failed"])

    def test_closed_attention_is_not_counted(self):
        result = overview.build_overview(
            {"chatgpt_runners": {"cloud": {}}},
            {"items": []},
            {"items": [{"project_id": "cloud", "status": "resolved", "severity": "urgent"}]},
            {},
        )
        self.assertEqual(0, result["projects"][0]["attention_count"])

    def test_unknown_attention_severity_is_fail_visible(self):
        result = overview.build_overview(
            {"chatgpt_runners": {}},
            {"items": []},
            {"items": [{"project_id": "cloud", "status": "open", "severity": "mystery"}]},
            {},
        )
        self.assertFalse(result["coverage_complete"])
        self.assertEqual("attention", result["projects"][0]["attention_severity"])
        self.assertEqual(1, result["malformed"]["attention"])

    def test_malformed_resource_numbers_do_not_invent_usage(self):
        result = overview.build_overview(
            {"chatgpt_runners": {"cloud": {}}},
            {"items": []},
            {"items": []},
            {"cloud": {"active_units": "bad", "cpu_percent": "bad", "memory_bytes": "bad"}},
        )
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["projects"][0]["uses_resources"])

    def test_symlink_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "runner.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(overview.OverviewError):
                overview._load(link)


if __name__ == "__main__":
    unittest.main()
