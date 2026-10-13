"""Offline trust matrix for manual in-place Worker-V2 addon activation."""
from contextlib import redirect_stdout
from email.message import Message
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import zcloud_inplace_addon_idle_admission_20261013_w2 as gate

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-v2-activate-addon-in-place.yml"


def ready():
    return {"chatgpt_runners": {"supa": {"workers": [{
        "worker_id": "supa::w1",
        "active": True,
        "sending": False, "generating": False, "desired_state": "running",
    }]}}}


class Response:
    def __init__(self, body, status=200, mime="application/json"):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = mime

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, count):
        return self.body[:count]


class Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        assert request.full_url == gate.URL
        assert timeout == 5
        return self.response


class IdleGuardTests(unittest.TestCase):
    def test_good_worker_stable(self):
        self.assertEqual(gate.classify(ready()), ("supa::w1",))

    def test_multiple_workers_order_independent(self):
        data = ready()
        data["chatgpt_runners"]["supa"]["workers"].append({
            "worker_id": "supa::w2", "active": True,
            "sending": False, "generating": False, "desired_state": "running",
        })
        self.assertEqual(gate.classify(data), ("supa::w1", "supa::w2"))

    def test_missing_runner_and_worker_observations_deny(self):
        for bad in (
            None, [], {}, {"chatgpt_runners": []},
            {"chatgpt_runners": {}},
            {"chatgpt_runners": {"supa": None}},
            {"chatgpt_runners": {"supa": {}}},
            {"chatgpt_runners": {"supa": {"workers": None}}},
            {"chatgpt_runners": {"supa": {"workers": []}}},
            {"chatgpt_runners": {"supa": {"workers": [None]}}},
        ):
            with self.subTest(bad=bad), self.assertRaises(gate.Denied):
                gate.classify(bad)

    def test_busy_unknown_flags_deny(self):
        for name in ("sending", "generating"):
            for flag in (None, "false", True, 0, 1, [], {}):
                data = ready()
                data["chatgpt_runners"]["supa"]["workers"][0][name] = flag
                with self.subTest(name=name, flag=flag), self.assertRaises(gate.Denied):
                    gate.classify(data)
            data = ready()
            del data["chatgpt_runners"]["supa"]["workers"][0][name]
            with self.assertRaises(gate.Denied):
                gate.classify(data)

    def test_paused_draining_stopped_unknown_states_deny(self):
        for state in (None, "draining", "paused", "stopped", "generating", 1):
            data = ready()
            data["chatgpt_runners"]["supa"]["workers"][0]["desired_state"] = state
            with self.subTest(state=state), self.assertRaises(gate.Denied):
                gate.classify(data)

    def test_untyped_active_and_zero_active_deny(self):
        for active in (None, 0, 1, "true", False):
            data = ready()
            data["chatgpt_runners"]["supa"]["workers"][0]["active"] = active
            with self.subTest(active=active), self.assertRaises(gate.Denied):
                gate.classify(data)

    def test_invalid_identity_and_duplicate_deny(self):
        for wid in ("", None, 1, "no-slot", "::w1", "supa::w0", "supa::w01", "supa::w",
                    "supa::w1" + "x" * 129):
            data = ready()
            data["chatgpt_runners"]["supa"]["workers"][0]["worker_id"] = wid
            with self.subTest(wid=wid), self.assertRaises(gate.Denied):
                gate.classify(data)
        data = ready()
        data["chatgpt_runners"]["supa"]["workers"] *= 2
        with self.assertRaises(gate.Denied):
            gate.classify(data)

    def test_too_many_workers_denied(self):
        data = ready()
        data["chatgpt_runners"]["supa"]["workers"] = [{
            "worker_id": f"supa::w{x}", "active": True,
            "sending": False, "generating": False, "desired_state": "running",
        } for x in range(1, gate.MAX_ACTIVE + 2)]
        with self.assertRaises(gate.Denied):
            gate.classify(data)

    def test_duplicate_json_keys_and_redirect_denied(self):
        with self.assertRaises(gate.Denied):
            json.loads('{"a": 1, "a": 2}', object_pairs_hook=gate.unique_object)
        with self.assertRaises(gate.Denied):
            gate.NoRedirect().redirect_request(None, None, 302, "", {}, "http://elsewhere")

    def test_local_endpoint_bounded_response_accept(self):
        self.assertEqual(gate.read_status(Opener(Response(ready()))), ("supa::w1",))

    def test_untrusted_http_response_denied(self):
        for result in (
            Response(ready(), status=401),
            Response(ready(), mime="text/html"),
            Response(b"x" * (gate.LIMIT + 1)),
            Response(b"{malformed"),
            Response(b'{"chatgpt_runners":{},"chatgpt_runners":{}}'),
        ):
            with self.subTest(status=result.status, length=len(result.body)), \
                    self.assertRaises((gate.Denied, ValueError)):
                gate.read_status(Opener(result))

    def test_stable_double_snapshot(self):
        reads = []
        def get():
            reads.append(True)
            return ("supa::w1",)
        self.assertEqual(gate.check_stable(get, lambda sec: self.assertEqual(sec, 3)), 1)
        self.assertEqual(len(reads), 2)

    def test_changed_workers_between_snapshots_denied(self):
        values = iter([("supa::w1",), ("supa::w1", "supa::w2")])
        with self.assertRaises(gate.Denied):
            gate.check_stable(lambda: next(values), lambda _: None)

    def test_failure_output_never_leaks_status_and_never_authorizes(self):
        buf = io.StringIO()
        with patch.object(gate, "check_stable",
                          side_effect=gate.Denied("private worker URL and queue")), \
             redirect_stdout(buf):
            self.assertEqual(gate.main(), 2)
        result = json.loads(buf.getvalue())
        self.assertEqual(result["status"], "deny")
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])
        self.assertNotIn("private", buf.getvalue())

    def test_success_is_advisory_only(self):
        buf = io.StringIO()
        with patch.object(gate, "check_stable", return_value=2), redirect_stdout(buf):
            self.assertEqual(gate.main(), 0)
        result = json.loads(buf.getvalue())
        self.assertEqual(result["active_workers"], 2)
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.validate = cls.text.split("  validate:\n", 1)[1].split("\n  activate:\n", 1)[0]
        cls.activate = cls.text.split("\n  activate:\n", 1)[1]

    def test_no_push_or_workflow_run(self):
        events = self.text.split("\npermissions:", 1)[0]
        self.assertNotIn("\n  push:\n", events)
        self.assertNotIn("\n  workflow_run:\n", events)
        self.assertIn("\n  pull_request:\n", events)
        self.assertIn("\n  workflow_dispatch:\n", events)
        self.assertIn("confirm_sha:", events)
        self.assertIn("maintenance_ack:", events)
        self.assertEqual(events.count("required: true"), 2)

    def test_pr_isolation_on_hosted_ci(self):
        self.assertIn("github.event_name == 'pull_request'", self.validate)
        self.assertIn("runs-on: ubuntu-latest", self.validate)
        self.assertNotIn("self-hosted", self.validate)
        self.assertNotIn("zcloud_reload_extension.mjs", self.validate)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_inplace_addon_idle_admission_20261013_w2", self.validate)

    def test_manual_activation_requires_owner_ack_and_main(self):
        for claim in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.maintenance_ack == 'I_APPROVE_ADDON_ONLY_RELOAD'",
            "runs-on: [self-hosted, zcloud, vps]",
        ):
            self.assertIn(claim, self.activate)
        self.assertEqual(self.text.count("runs-on: [self-hosted, zcloud, vps]"), 1)

    def test_no_stored_git_credentials_or_floating_checkout(self):
        self.assertEqual(self.text.count("persist-credentials: false"), 2)
        self.assertEqual(self.text.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2)
        self.assertEqual(self.text.count("ref: " + "$"+"{{ github.sha }}"), 2)

    def test_main_freshness_identity_and_runner_come_before_mutation(self):
        preflight = self.activate.index("Enforce manual SHA, permanent runner and current main")
        live = self.activate.index("Verify installed addon matches approved main")
        action = self.activate.index("Recheck exact main and stable idle workers")
        self.assertLess(preflight, live)
        self.assertLess(live, action)
        first = self.activate[preflight:live]
        for value in (
            '[[ "$CONFIRMED_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            "git ls-remote --exit-code origin refs/heads/main",
            "python3 scripts/zcloud_vps_runner_guard.py --json",
            'test "$(hostname)" = vps-bb300bba',
            'test "$(id -un)" = ubuntu',
        ):
            self.assertIn(value, first)

    def test_verified_installed_bytes_and_idle_twice_before_reload(self):
        self.assertIn('cmp -- "firefox-extension/$file" "$addon/$file"', self.activate)
        self.assertIn("background.js worker-controller-v2.js manifest.json", self.activate)
        self.assertEqual(self.activate.count("python3 scripts/zcloud_inplace_addon_idle_admission_20261013_w2.py"), 2)
        self.assertLess(self.activate.index('cmp -- "firefox-extension/'),
                        self.activate.index("node scripts/zcloud_reload_extension.mjs"))
        self.assertLess(self.activate.rindex("python3 scripts/zcloud_inplace_addon_idle_admission_20261013_w2.py"),
                        self.activate.index("node scripts/zcloud_reload_extension.mjs"))

    def test_serialized_production_deploy_mutex(self):
        self.assertIn("group: zcloud-production-deploy", self.activate)
        self.assertIn("cancel-in-progress: false", self.activate)
        self.assertNotIn("systemctl restart", self.activate)
        self.assertNotIn("sudo ", self.activate)
        self.assertNotIn("pkill ", self.activate)

    def test_browser_pid_continuity_checked(self):
        self.assertIn('test "$before" = "$after"', self.activate)
        self.assertIn('test -n "$before"', self.activate)
        self.assertIn('kill -0 "$before"', self.activate)


if __name__ == "__main__":
    unittest.main()
