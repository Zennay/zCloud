"""Owner-only, non-shell ChatGPT VPS bridge contract tests."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_chatgpt_runner_bridge.py"
spec = importlib.util.spec_from_file_location("zcloud_chatgpt_runner_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def make_event(body="/zssh status"):
    return {
        "action": "created",
        "repository": {"full_name": "Zennay/zCloud"},
        "issue": {"number": 1278},
        "comment": {
            "user": {"login": "Zennay"},
            "author_association": "OWNER",
            "performed_via_github_app": {"id": 1144995},
            "body": body,
        },
    }


class ChatgptRunnerBridgeTests(unittest.TestCase):
    def test_valid_connector_owner_request(self):
        for cmd in [
            "/zssh status",
            "/zssh service zennay-cloud",
            "/zssh service zssh",
            "/zssh service zssh-public",
            "/zssh project cloud",
            "/zssh run write-probe",
            "/zssh run zcloud-guard-test",
            "/zssh run zcloud-health-smoke",
        ]:
            self.assertEqual(bridge.parse_request(make_event(cmd), "Zennay"), cmd[6:])

    def test_direct_owner_comment_is_supported(self):
        event = make_event()
        event["comment"]["performed_via_github_app"] = None
        self.assertEqual(bridge.parse_request(event, "Zennay"), "status")

    def test_untrusted_origin_denied(self):
        changes = (
            lambda e: e.update(action="edited"),
            lambda e: e["repository"].update(full_name="Evil/zCloud"),
            lambda e: e["issue"].update(number=1279),
            lambda e: e["issue"].update(pull_request={"url": "https://example.test"}),
            lambda e: e["comment"]["user"].update(login="someone-else"),
            lambda e: e["comment"].update(author_association="COLLABORATOR"),
            lambda e: e["comment"].update(performed_via_github_app={"id": 123}),
        )
        for mutate in changes:
            event = make_event()
            mutate(event)
            with self.subTest(change=str(mutate)), self.assertRaises(bridge.RequestRejected):
                bridge.parse_request(event, "Zennay")
        with self.assertRaises(bridge.RequestRejected):
            bridge.parse_request(make_event(), "someone-else")

    def test_never_interprets_arbitrary_shell(self):
        invalid = (
            "/zssh sh -c id",
            "/zssh exec ls",
            "/zssh run ../../etc/passwd",
            "/zssh service ssh",
            "/zssh status; id",
            "/zssh status\nwhoami",
            "/zssh status\r",
            "/zssh status && curl attacker.test",
            "/zssh run write-probe --foo",
            "/zssh run bad.name",
            "/zssh run abc/def",
            "/zssh STATUS",
            "/zssh status ",
            " /zssh status",
            "/zssh status" + " " * 150,
        )
        for command in invalid:
            with self.subTest(command=repr(command)), self.assertRaises(bridge.RequestRejected):
                bridge.parse_request(make_event(command), "Zennay")

    def test_runner_identity_is_fail_closed(self):
        with patch.object(bridge.socket, "gethostname", return_value="vps-bb300bba"), \
             patch.object(bridge.os, "geteuid", return_value=1000), \
             patch.dict(os.environ, {"USER": "ubuntu", "RUNNER_NAME": "zcloud-vps-1"}):
            bridge.ensure_runner()
        with patch.object(bridge.socket, "gethostname", return_value="other-host"), \
             patch.object(bridge.os, "geteuid", return_value=1000), \
             patch.dict(os.environ, {"USER": "ubuntu", "RUNNER_NAME": "zcloud-vps-1"}):
            with self.assertRaisesRegex(RuntimeError, "wrong_runner_host"):
                bridge.ensure_runner()
        with patch.object(bridge.socket, "gethostname", return_value="vps-bb300bba"), \
             patch.object(bridge.os, "geteuid", return_value=0), \
             patch.dict(os.environ, {"USER": "ubuntu", "RUNNER_NAME": "zcloud-vps-1"}):
            with self.assertRaisesRegex(RuntimeError, "wrong_runner_user"):
                bridge.ensure_runner()

    def test_temporary_write_probe_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bridge, "ensure_runner"), \
             patch.dict(os.environ, {"RUNNER_TEMP": directory}):
            result = bridge.execute("run write-probe")
            self.assertTrue(result["ok"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_status_exposes_only_fixed_read_only_commands(self):
        calls = []
        def read_only_stub(argv, **kwargs):
            calls.append(argv)
            return 0, "OK"
        with patch.object(bridge, "ensure_runner"), \
             patch.object(bridge, "run_checked", side_effect=read_only_stub):
            result = bridge.execute("status")
        self.assertTrue(result["ok"])
        self.assertEqual([v[0] for v in calls], ["/usr/bin/uptime", "/usr/bin/df", "/usr/bin/free"])
        self.assertTrue(all(isinstance(v, list) for v in calls))

    def test_workflow_never_runs_pr_code_on_self_hosted_runner(self):
        content = (ROOT / ".github/workflows/zssh-chatgpt-runner-bridge.yml").read_text()
        self.assertIn("issue_comment:", content)
        self.assertNotIn("pull_request:", content)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", content)
        self.assertIn("needs.authorize.result == 'success'", content)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("ref: ${{ github.sha }}", content)
        self.assertIn("issues: write", content)
        self.assertNotIn("github.event.comment.body }}", content)


if __name__ == "__main__":
    unittest.main()
