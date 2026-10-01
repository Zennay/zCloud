import importlib.util
import io
from pathlib import Path
import http.client
import urllib.error
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zssh_release_coordination.py"

spec = importlib.util.spec_from_file_location("zssh_release_coordination", SCRIPT)
coordination = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(coordination)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class ZsshReleaseCoordinationTests(unittest.TestCase):
    def test_get_json_retries_transient_disconnect(self):
        responses = [
            http.client.RemoteDisconnected("temporary monitor restart"),
            FakeResponse(b'{"ok": true}'),
        ]

        def fake_urlopen(_request, timeout=0):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            self.assertEqual(timeout, 15)
            return item

        with mock.patch.object(coordination.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(coordination.time, "sleep") as sleep:
            result = coordination.get_json("http://127.0.0.1:8765/api/status")

        self.assertEqual({"ok": True}, result)
        sleep.assert_called_once_with(1)

    def test_get_json_retries_transient_503(self):
        responses = [
            urllib.error.HTTPError(
                "http://127.0.0.1:8765/api/status",
                503,
                "Service Unavailable",
                {},
                io.BytesIO(b'{"error":"Monitor start op"}'),
            ),
            FakeResponse(b'{"ok": true, "service": "zcloud"}'),
        ]

        def fake_urlopen(_request, timeout=0):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch.object(coordination.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(coordination.time, "sleep"):
            result = coordination.get_json("http://127.0.0.1:8765/api/status")

        self.assertTrue(result["ok"])

    def test_get_json_fails_closed_on_non_transient_http_error(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/repos/Zennay/zSSH/branches/main",
            404,
            "Not Found",
            {},
            io.BytesIO(b'{"message":"Not Found"}'),
        )
        with mock.patch.object(coordination.urllib.request, "urlopen", side_effect=error), \
             mock.patch.object(coordination.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, r"rejected request \(404\)"):
                coordination.get_json(error.url)
        sleep.assert_not_called()

    def test_release_cleanup_is_idempotent_when_no_claim_exists(self):
        error = urllib.error.HTTPError(
            coordination.BASE + "/api/task-claims",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"released": false}'),
        )
        with mock.patch.dict(coordination.os.environ, {"GITHUB_RUN_ID": "36935116645"}), \
             mock.patch.object(coordination.urllib.request, "urlopen", side_effect=error), \
             mock.patch("builtins.print") as printer:
            coordination.release()

        printer.assert_called_once_with("ZSSH_RELEASE_CLAIM_RELEASED released=False")

    def test_post_conflict_still_fails_when_not_explicitly_allowed(self):
        error = urllib.error.HTTPError(
            coordination.BASE + "/api/task-claims",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"released": false}'),
        )
        with mock.patch.object(coordination.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, r"rejected request \(409\)"):
                coordination.post_json("/api/task-claims", {"action": "release"})


if __name__ == "__main__":
    unittest.main()
