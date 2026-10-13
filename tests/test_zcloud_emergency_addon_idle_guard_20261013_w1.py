"""Offline denial matrix for owner-confirmed manual addon reload trust and idle gates."""
import copy
import json
from pathlib import Path
import re
import unittest

from scripts import zcloud_emergency_addon_idle_guard_20261013_w1 as guard

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-emergency-addon-hot-reload.yml"


def ready():
    return {"chatgpt_runners": {
        "zcloud-vps-1": {"workers": [{
            "worker_id": "zcloud-vps-1::w1", "active": True,
            "sending": False, "generating": False, "desired_state": "active",
        }]}
    }}


class IdleDecisionTests(unittest.TestCase):
    def test_single_idle_active_worker(self):
        self.assertEqual(guard.safe_worker_ids(ready()), ("zcloud-vps-1::w1",))

    def test_two_idle_workers_order_independent(self):
        data = ready()
        other = copy.deepcopy(data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0])
        other["worker_id"] = "zcloud-vps-1::w2"
        data["chatgpt_runners"]["zcloud-vps-1"]["workers"].insert(0, other)
        self.assertEqual(guard.safe_worker_ids(data), ("zcloud-vps-1::w1", "zcloud-vps-1::w2"))

    def test_missing_observations_deny(self):
        for bad in (
            None, [], {}, {"chatgpt_runners": None},
            {"chatgpt_runners": {}},
            {"chatgpt_runners": {"runner": None}},
            {"chatgpt_runners": {"runner": {}}},
            {"chatgpt_runners": {"runner": {"workers": None}}},
            {"chatgpt_runners": {"runner": {"workers": []}}},
            {"chatgpt_runners": {"runner": {"workers": [{}]}}},
            {"chatgpt_runners": {"runner": {"workers": [None]}}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(guard.AdmissionDenied):
                    guard.safe_worker_ids(bad)

    def test_busy_unknown_or_malformed_flags_deny(self):
        for key in ("sending", "generating"):
            for bad in (True, None, "false", "true", 0, 1, [], {}):
                data = ready()
                data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0][key] = bad
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(guard.AdmissionDenied):
                        guard.safe_worker_ids(data)
            data = ready()
            del data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0][key]
            with self.assertRaises(guard.AdmissionDenied):
                guard.safe_worker_ids(data)

    def test_unsafe_desired_states_deny(self):
        for bad in (None, "draining", "paused", "stopped", "unknown", "generating", 1):
            data = ready()
            data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["desired_state"] = bad
            with self.subTest(state=bad):
                with self.assertRaises(guard.AdmissionDenied):
                    guard.safe_worker_ids(data)

    def test_duplicate_and_ambiguous_worker_ids_deny(self):
        for bad in ("", " ", 2, None, "x" * 129):
            data = ready()
            data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["worker_id"] = bad
            with self.subTest(id=bad):
                with self.assertRaises(guard.AdmissionDenied):
                    guard.safe_worker_ids(data)
        data = ready()
        data["chatgpt_runners"]["zcloud-vps-1"]["workers"] *= 2
        with self.assertRaises(guard.AdmissionDenied):
            guard.safe_worker_ids(data)

    def test_boolean_active_required_and_no_zero_active_admission(self):
        for bad in (None, 1, 0, "true", [], {}):
            data = ready()
            data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["active"] = bad
            with self.assertRaises(guard.AdmissionDenied):
                guard.safe_worker_ids(data)
        data = ready()
        data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["active"] = False
        with self.assertRaises(guard.AdmissionDenied):
            guard.safe_worker_ids(data)

    def test_two_matching_snapshots_required(self):
        calls = []
        def reader():
            calls.append("read")
            return ("w1",)
        self.assertEqual(guard.verify_two_snapshots(reader, lambda sec: calls.append(sec)), 1)
        self.assertEqual(calls, ["read", 3, "read"])

    def test_changed_roster_deny(self):
        values = iter([("w1",), ("w1", "w2")])
        with self.assertRaises(guard.AdmissionDenied):
            guard.verify_two_snapshots(lambda: next(values), lambda sec: None)

    def test_duplicate_json_properties_deny(self):
        with self.assertRaises(guard.AdmissionDenied):
            json.loads('{"active":false,"active":true}', object_pairs_hook=guard.unique_object)

    def test_redirect_denied(self):
        with self.assertRaises(guard.AdmissionDenied):
            guard.DenyRedirect().redirect_request(None, None, 302, "", {}, "http://evil/")

    def test_local_only_bounded_read_policy(self):
        self.assertEqual(guard.URL, "http://127.0.0.1:8765/api/runner-live")
        self.assertLessEqual(guard.MAX_RESPONSE_BYTES, 65536)


class SnapshotTransportTests(unittest.TestCase):
    """Exercise actual HTTP payload validation with a fake local-only opener."""

    @staticmethod
    def opener(body, status=200, mime="application/json"):
        from email.message import Message

        class Response:
            def __init__(self):
                self.status = status
                self.headers = Message()
                self.headers["Content-Type"] = mime

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, count):
                return body[:count]

        class Opener:
            def open(self, request, timeout):
                assert request.full_url == guard.URL
                assert timeout == 5
                return Response()

        return Opener()

    def test_valid_json_observation_over_local_transport(self):
        content = json.dumps(ready()).encode()
        self.assertEqual(
            guard.read_snapshot(self.opener(content)), ("zcloud-vps-1::w1",)
        )

    def test_http_error_and_untrusted_mime_are_denied(self):
        good = json.dumps(ready()).encode()
        for status, mime in ((403, "application/json"), (503, "application/json"),
                             (200, "text/html"), (200, "text/plain")):
            with self.subTest(status=status, mime=mime):
                with self.assertRaises(guard.AdmissionDenied):
                    guard.read_snapshot(self.opener(good, status, mime))

    def test_oversized_malformed_and_duplicate_json_response_denied(self):
        for body in (
            b"x" * (guard.MAX_RESPONSE_BYTES + 1),
            b"{invalid json",
            b'{"chatgpt_runners": {}, "chatgpt_runners": {}}',
            b'{"chatgpt_runners": "\xff"}',
        ):
            with self.subTest(length=len(body)):
                with self.assertRaises((guard.AdmissionDenied, ValueError, UnicodeError)):
                    guard.read_snapshot(self.opener(body))

    def test_missing_busy_and_corrupt_http_payloads_denied(self):
        for sample in (
            {},
            {"chatgpt_runners": {}},
            {"chatgpt_runners": {"zcloud": {"workers": [{"active": True}]}}},
        ):
            body = json.dumps(sample).encode()
            with self.assertRaises(guard.AdmissionDenied):
                guard.read_snapshot(self.opener(body))


class WorkflowTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    @staticmethod
    def job(source, name):
        body = source.split("\njobs:\n", 1)[1]
        marker = f"\n  {name}:\n"
        if marker not in "\n" + body:
            raise AssertionError(f"missing job {name}")
        return re.split(r"\n  [a-z0-9-]+:\n", ("\n" + body).split(marker, 1)[1], maxsplit=1)[0]

    def test_no_auto_push_activation(self):
        events = self.source.split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:\n", events)
        self.assertIn("\n  workflow_dispatch:\n", events)
        self.assertNotIn("\n  push:\n", events)
        self.assertNotIn("\n  workflow_run:\n", events)
        self.assertIn("confirm_sha:", events)
        self.assertIn("required: true", events)

    def test_pr_runs_only_hosted(self):
        hosted = self.job(self.source, "validate")
        self.assertIn("github.event_name == 'pull_request'", hosted)
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("self-hosted", hosted)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_emergency_addon_idle_guard_20261013_w1", hosted)

    def test_vps_strict_owner_main_manual(self):
        action = self.job(self.source, "activate")
        self.assertIn("github.event_name == 'workflow_dispatch'", action)
        self.assertIn("github.repository == 'Zennay/zCloud'", action)
        self.assertIn("github.ref == 'refs/heads/main'", action)
        self.assertIn("github.actor == 'Zennay'", action)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", action)
        self.assertEqual(self.source.count("runs-on: [self-hosted, zcloud, vps]"), 1)
        self.assertNotIn("github.event_name == 'push'", action)

    def test_manual_input_exact_sha_and_guard_before_reads(self):
        action = self.job(self.source, "activate")
        self.assertIn("CONFIRMED_SHA: INPUT_EXPR", action.replace("GH_EXPR", "GH_EXPR").replace("$"+"{{ inputs.confirm_sha }}", "INPUT_EXPR"))
        gates = action.index("Verify manually approved exact current main")
        probes = action.index("Deny reload during generation")
        reload_at = action.index("Recheck exact main and idle workers")
        self.assertLess(gates, probes)
        self.assertLess(probes, reload_at)
        first = action[gates:probes]
        for condition in (
            '[[ "$CONFIRMED_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            'git ls-remote --exit-code origin refs/heads/main',
            'test "$current_main" = "$GITHUB_SHA"',
            'test "$(hostname)" = "vps-bb300bba"',
            'test "$(id -un)" = "ubuntu"',
            'python3 scripts/zcloud_vps_runner_guard.py --json',
        ):
            with self.subTest(condition=condition):
                self.assertIn(condition, first)

    def test_checkout_immutable_with_no_persistent_credentials(self):
        for name in ("validate", "activate"):
            job = self.job(self.source, name)
            self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", job)
            self.assertIn("ref: " + "$"+"{{ github.sha }}", job)
            self.assertIn("persist-credentials: false", job)
            self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', job)

    def test_read_only_idle_twice_and_live_source_check(self):
        action = self.job(self.source, "activate")
        self.assertEqual(action.count("python3 scripts/zcloud_emergency_addon_idle_guard_20261013_w1.py"), 2)
        self.assertIn('cmp -- "$source" "$live"', action)
        self.assertIn("node --check firefox-extension/background.js", action)
        self.assertLess(action.index("cmp --"), action.index("node scripts/zcloud_reload_extension.mjs"))
        self.assertIn('test "$main_pid" = "$new_pid"', action)

    def test_no_direct_browser_restart(self):
        action = self.job(self.source, "activate")
        # Negative grep checks are safe; forbidden API text is expected there.
        executed = "\n".join(
            line for line in action.splitlines()
            if not line.lstrip().startswith("! grep -Fq")
        )
        for unsafe in ("systemctl restart", "pkill", "kill -9", "tabs.remove(", "browser.tabs.create("):
            self.assertNotIn(unsafe, executed)
        self.assertIn("cancel-in-progress: false", self.source)
        self.assertIn("permissions:\n  contents: read", self.source)


if __name__ == "__main__":
    unittest.main()
