import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("zcloud_completion_sync", ROOT / "scripts" / "zcloud_completion_sync.py")
sync_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_module)


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SyncTests(unittest.TestCase):
    def test_sync_posts_every_open_pull_with_its_check_runs(self):
        pulls_page_1 = [{"number": 1, "head": {"sha": "abc"}}]
        calls = []

        def fake_urlopen(req, timeout=20):
            url = req.full_url
            calls.append(url)
            if "/pulls?per_page" in url and "page=1" in url:
                return _FakeResponse(pulls_page_1)
            if "/pulls?per_page" in url:
                return _FakeResponse([])
            if "/check-runs" in url:
                return _FakeResponse({"check_runs": [{"status": "completed", "conclusion": "success"}]})
            if url.endswith("/api/completion"):
                return _FakeResponse({"ok": True, "recorded": [{"pr_number": 1}]})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(sync_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = sync_module.sync("Zennay/zCloud", "cloud", "tok", "http://127.0.0.1:8765")

        self.assertEqual(result["open_pulls"], 1)
        self.assertTrue(any("check-runs" in u for u in calls))
        self.assertTrue(any(u.endswith("/api/completion") for u in calls))

    def test_apply_auto_merges_skips_pr_that_regressed_since_classification(self):
        dashboard_status = {"projects": {"cloud": {"mergeable_ready": [{"pr_number": 42}]}}}

        def fake_urlopen(req, timeout=20):
            url = req.full_url
            if url.endswith("/api/completion") and req.data is None:
                return _FakeResponse(dashboard_status)
            if url.endswith("/pulls/42"):
                # Regressed: now has merge conflicts even though the
                # dashboard still thinks it is clean.
                return _FakeResponse({"draft": False, "mergeable_state": "dirty", "head": {"sha": "zzz"}})
            raise AssertionError(f"unexpected URL {url} in regression test")

        with mock.patch.object(sync_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = sync_module.apply_auto_merges("Zennay/zCloud", "cloud", "tok", "http://127.0.0.1:8765", "squash")

        self.assertEqual(result["merged"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no longer clean", result["skipped"][0]["reason"])

    def test_apply_auto_merges_merges_pr_that_is_still_clean(self):
        dashboard_status = {"projects": {"cloud": {"mergeable_ready": [{"pr_number": 42}]}}}
        posted = []

        def fake_urlopen(req, timeout=20):
            url = req.full_url
            if url.endswith("/api/completion") and req.data is None:
                return _FakeResponse(dashboard_status)
            if url.endswith("/pulls/42"):
                return _FakeResponse({"draft": False, "mergeable_state": "clean", "head": {"sha": "zzz"}, "html_url": "https://x/42"})
            if url.endswith("/commits/zzz/check-runs?per_page=100"):
                return _FakeResponse({"check_runs": [{"status": "completed", "conclusion": "success"}]})
            if url.endswith("/pulls/42/merge"):
                return _FakeResponse({"merged": True, "sha": "newsha"})
            if url.endswith("/api/completion") and req.data is not None:
                posted.append(json.loads(req.data))
                return _FakeResponse({"ok": True})
            raise AssertionError(f"unexpected URL {url} in merge test")

        with mock.patch.object(sync_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = sync_module.apply_auto_merges("Zennay/zCloud", "cloud", "tok", "http://127.0.0.1:8765", "squash")

        self.assertEqual(len(result["merged"]), 1)
        self.assertEqual(result["skipped"], [])
        kinds = [p.get("kind") for p in posted if p.get("action") == "progress-event"]
        self.assertIn("merge", kinds)
        actions = [p.get("action") for p in posted]
        self.assertIn("task-completed", actions)


if __name__ == "__main__":
    unittest.main()
