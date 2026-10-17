"""Hosted offline regression for explicit manual-only worker v2 activation."""
import ast
from email.message import Message
import json
from pathlib import Path
import re
import textwrap
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-v2-one-shot-activation.yml"


def baseline():
    return {"chatgpt_runners": {"zcloud-vps-1": {"workers": [
        {"worker_id": "zcloud-vps-1::w1", "active": True,
         "sending": False, "generating": False, "desired_state": "active"}
    ]}}}


class FakeResponse:
    def __init__(self, payload, content_type="application/json", status=200):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.body[:size]


class OneShotManualGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.action = cls.text.split("  activate:\n", 1)[1]
        # Extract the literal Python idle classifier, compile only its
        # definitions, and validate them without any localhost/network calls.
        marker = "          python3 - <<'PY'\n"
        assert marker in cls.action
        code = cls.action.split(marker, 1)[1].split("\n          PY", 1)[0]
        tree = ast.parse(textwrap.dedent(code))
        defs = [item for item in tree.body if isinstance(item, ast.FunctionDef)]
        assert {d.name for d in defs} == {"unique", "snapshot"}
        scope = {"json": json, "urllib": urllib}
        exec(compile(ast.Module(body=defs, type_ignores=[]), "<offline-idle-guard>", "exec"), scope)
        cls.snapshot = staticmethod(scope["snapshot"])

    def test_on_push_is_gone(self):
        events = self.text.split("\npermissions:", 1)[0]
        self.assertIn("\n  workflow_dispatch:\n", events)
        self.assertIn("\n  pull_request:\n", events)
        self.assertNotIn("\n  push:\n", events)
        self.assertNotIn("\n  workflow_run:\n", events)
        self.assertIn("confirm_sha:", events)
        self.assertIn("confirm_maintenance_window:", events)
        self.assertEqual(events.count("required: true"), 2)

    def test_pr_validation_only_hosted(self):
        hosted = self.text.split("  validate:\n", 1)[1].split("\n  activate:\n", 1)[0]
        self.assertIn("github.event_name == 'pull_request'", hosted)
        self.assertIn("runs-on: ubuntu-latest", hosted)
        self.assertNotIn("self-hosted", hosted)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_one_shot_v2_manual_gate_20261013_w1", hosted)

    def test_privileged_job_strict_manual_owner_main_and_ack(self):
        for requirement in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.confirm_maintenance_window == 'I_APPROVE_SHARED_VPS_ONE_SHOT'",
            "runs-on: [self-hosted, zcloud, vps]",
        ):
            self.assertIn(requirement, self.action)
        self.assertNotIn("runs-on: [self-hosted, ftmo-research]", self.action)

    def test_exact_sha_and_runner_before_any_live_mutation(self):
        first = self.action.index("Fail closed on stale SHA")
        idle = self.action.index("Read-only stable worker idle proof")
        install = self.action.index("Verify one-tab engine from current main")
        self.assertLess(first, idle)
        self.assertLess(idle, install)
        gate = self.action[first:idle]
        for proof in (
            '[[ "$CONFIRMED_SHA" =~ ^[0-9a-f]{40}$ ]]',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
            "git ls-remote --exit-code origin refs/heads/main",
            'test "$(hostname)" = vps-bb300bba',
            'test "$(id -un)" = ubuntu',
            "python3 scripts/zcloud_vps_runner_guard.py --json",
        ):
            self.assertIn(proof, gate)

    def test_exact_checkout_no_credentials_in_both_jobs(self):
        self.assertEqual(
            self.text.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2
        )
        self.assertEqual(self.text.count("persist-credentials: false"), 2)
        self.assertEqual(self.text.count("ref: " + "$"+"{{ github.sha }}"), 2)

    def test_no_pr_writer_and_existing_install_bounded_by_gate(self):
        hosted = self.text.split("  validate:\n", 1)[1].split("\n  activate:\n", 1)[0]
        for forbidden in ("install -m", "systemctl ", "sudo ", "zcloud_reload_extension.mjs", "zcloud_retire_legacy_worker_services.sh"):
            self.assertNotIn(forbidden, hosted)
        self.assertIn("bash scripts/zcloud_retire_legacy_worker_services.sh", self.action)
        self.assertIn("node scripts/zcloud_reload_extension.mjs", self.action)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_idle_classifier_requires_two_stable_snapshots(self):
        content = self.action.split("          python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        self.assertIn("first = snapshot()", content)
        self.assertIn("time.sleep(3)", content)
        self.assertIn("if snapshot() != first:", content)

    def test_idle_success_synthetic(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(baseline())):
            self.assertEqual(self.snapshot(), ("zcloud-vps-1::w1",))

    def test_running_generation_or_unknown_flags_deny(self):
        for key in ("sending", "generating"):
            for invalid in (True, None, "false", 0, {}, []):
                data = baseline()
                data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0][key] = invalid
                with self.subTest(key=key, invalid=invalid):
                    with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
                        with self.assertRaises(ValueError):
                            self.snapshot()

    def test_malformed_worker_status_denies(self):
        for data in (
            {}, {"chatgpt_runners": {}},
            {"chatgpt_runners": {"r": {"workers": None}}},
            {"chatgpt_runners": {"r": {"workers": [None]}}},
        ):
            with self.subTest(data=data):
                with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
                    with self.assertRaises(ValueError):
                        self.snapshot()

    def test_duplicate_json_keys_denied(self):
        raw = b'{"chatgpt_runners": {}, "chatgpt_runners": {}}'
        with patch("urllib.request.urlopen", return_value=FakeResponse(raw)):
            with self.assertRaises(ValueError):
                self.snapshot()

    def test_wrong_http_status_mime_or_oversize_denied(self):
        payload = baseline()
        for response in (
            FakeResponse(payload, "text/html"),
            FakeResponse(payload, status=500),
            FakeResponse(b"x" * 65537),
        ):
            with patch("urllib.request.urlopen", return_value=response):
                with self.assertRaises(ValueError):
                    self.snapshot()

    def test_ambiguous_identity_and_drain_denied(self):
        for field, value in (
            ("worker_id", ""), ("worker_id", None),
            ("active", "true"), ("desired_state", "draining"),
            ("desired_state", "unknown")
        ):
            data = baseline()
            data["chatgpt_runners"]["zcloud-vps-1"]["workers"][0][field] = value
            with self.subTest(field=field, value=value):
                with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
                    with self.assertRaises(ValueError):
                        self.snapshot()


if __name__ == "__main__":
    unittest.main()
