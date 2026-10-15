"""Real bounded-RDP fixtures and CI trust gates for the VM metadata probe.

Entirely offline: no Firefox, socket connections, VPS, SQLite, workers or secrets.
"""
from __future__ import annotations

import importlib.util
import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/zcloud_vm_actor_readonly_probe_20261014.py"
WORKFLOW = ROOT / ".github/workflows/zcloud-v2-violentmonkey-actor-probe.yml"
SPEC = importlib.util.spec_from_file_location("vm_metadata_probe", SRC)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def packet(value):
    raw = json.dumps(value).encode("utf-8")
    return str(len(raw)).encode("ascii") + b":" + raw


class FakeSocket:
    def __init__(self, payload):
        self.payload = payload
        self.sent = []
        self.closed = False
        self.timeout = None

    def recv(self, maximum):
        result = self.payload[:maximum]
        self.payload = self.payload[maximum:]
        return result

    def sendall(self, value):
        self.sent.append(value)

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


class VmActorReadOnlyProbeTests(unittest.TestCase):
    def setUp(self):
        self.data = packet({"from": "root", "applicationType": "browser"}) + packet({
            "from": "root", "addons": [
                {"id": "private-project-runner-id", "debuggable": True,
                 "actor": "private-actor", "url": "https://private.test/" },
                {"id": "some-other-addon"},
            ]
        })

    def test_real_fake_socket_protocol_only_sends_list_addons(self):
        sock = FakeSocket(self.data)
        passed = []
        def factory(target, timeout):
            passed.append((target, timeout))
            return sock
        result = probe.inspect("firefox -start-debugger-server 6000", factory)
        self.assertEqual(passed, [(("127.0.0.1", 6000), 4)])
        self.assertEqual(result["relevant_addon_count"], 1)
        self.assertEqual(result["debuggable_count"], 1)
        self.assertEqual(sock.sent, [b'33:{"to":"root","type":"listAddons"}'])
        self.assertTrue(sock.closed)
        self.assertEqual(sock.timeout, 6)

    def test_sensitive_identifiers_are_never_returned(self):
        sock = FakeSocket(self.data)
        result = probe.inspect("firefox -start-debugger-server 6000",
                               lambda *_args, **_kw: sock)
        encoded = json.dumps(result)
        for token in ("private-project-runner-id", "private-actor",
                      "https://private.test", "worker_id", "prompt"):
            self.assertNotIn(token, encoded)
        self.assertFalse(result["production_ready_verified"])
        self.assertFalse(result["live_worker_generation_verified"])

    def test_duplicate_debuggers_refused_without_connection(self):
        with self.assertRaisesRegex(ValueError, "not_unique"):
            probe.inspect("firefox -start-debugger-server 6000\n"
                          "firefox -start-debugger-server 6001",
                          lambda *_args, **_kw: self.fail("must not connect"))

    def test_missing_or_invalid_debugger_port_denied(self):
        for text in ("", "firefox --remote-debugging-port=6000",
                     "firefox -start-debugger-server 0",
                     "firefox -start-debugger-server 65536"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                probe.unique_debugger_port(text)

    def test_empty_or_invalid_frame_header_denied(self):
        for frame in (b":{}", b"X:abc", b"12345678:hello", b""):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                probe.read_frame(FakeSocket(frame))

    def test_oversized_body_denied_before_read(self):
        sock = FakeSocket(b"131073:")
        with self.assertRaisesRegex(ValueError, "size_denied"):
            probe.read_frame(sock)

    def test_truncated_frame_denied_not_hung(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            probe.read_frame(FakeSocket(b"20:{}"))

    def test_non_dict_frame_denied(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            probe.read_frame(FakeSocket(packet([1, 2, 3])))

    def test_untrusted_greeting_cannot_redirect_root_command(self):
        sock = FakeSocket(packet({"from": "attacker"}) + packet({"addons": []}))
        with self.assertRaisesRegex(ValueError, "greeting"):
            probe.inspect("firefox -start-debugger-server 6000",
                          lambda *_args, **_kw: sock)
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)

    def test_untrusted_addon_reply_denied(self):
        for reply in ({"error": "private-secret"}, {"addons": {}},
                      {"addons": [{"id": 10}]},
                      {"addons": [0]}, {"addons": [{}]},
                      {"addons": [{"id": str(i)} for i in range(101)]}):
            with self.subTest(reply_type=str(type(reply))), self.assertRaises(ValueError):
                probe.summarize_reply(reply)

    def test_unavailable_main_redacts_exception(self):
        with patch.object(probe.subprocess, "run",
                          side_effect=OSError("private/path/token")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = probe.main()
        self.assertEqual(code, 2)
        self.assertEqual(buf.getvalue(), "VM_METADATA_PROBE_UNAVAILABLE=1\n")
        self.assertNotIn("token", buf.getvalue())

    def test_workflow_auto_events_are_hosted_only(self):
        source = WORKFLOW.read_text()
        self.assertIn("  pull_request:", source)
        self.assertIn("  push:", source)
        # The hosted branch is now explicitly gated for trusted same-repo PR
        # source before checkout; trusted main pushes remain hosted.
        hosted = source.split("  hosted-proof:\n", 1)[1].split("  inspect:\n", 1)[0]
        self.assertIn("    if: >-", hosted)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", hosted)
        self.assertIn("github.triggering_actor == 'Zennay'", hosted)
        self.assertIn("github.ref == 'refs/heads/main'", hosted)
        self.assertIn("runs-on: ubuntu-latest", source)
        gate = source.split("  inspect:\n", 1)[1].split("    runs-on:", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertNotIn("github.event_name == 'push'", gate)
        self.assertNotIn("||", gate)

    def test_manual_proof_requires_owner_sha_and_zcloud_runner(self):
        source = WORKFLOW.read_text()
        for required in ("github.actor == 'Zennay'",
                         "github.repository == 'Zennay/zCloud'",
                         "github.triggering_actor == 'Zennay'",
                         "github.run_attempt == 1",
                         "github.ref == 'refs/heads/main'",
                         "inputs.confirm_sha == github.sha",
                         "runs-on: [self-hosted, zcloud, vps]",
                         'test "$CONFIRM_SHA" = "$GITHUB_SHA"',
                         'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
                         'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
                         'test "$GITHUB_RUN_ATTEMPT" = 1',
                         'timeout 15 git ls-remote --exit-code origin refs/heads/main',
                         "python3 scripts/zcloud_vps_runner_guard.py --json"):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("runs-on: [self-hosted, ftmo-research]", source)

    def test_live_metadata_probe_rechecks_fresh_main_before_socket(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        observe = source.split(
            "      - name: Observe only bounded Firefox addon metadata", 1
        )[1]
        for check in (
            'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
            'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
            'test "$GITHUB_REF" = refs/heads/main',
            'test "$GITHUB_ACTOR" = Zennay',
            'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
            'test "$GITHUB_RUN_ATTEMPT" = 1',
            'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
            'timeout 15 git ls-remote --exit-code origin refs/heads/main',
        ):
            self.assertIn(check, observe.split(
                "python3 scripts/zcloud_vm_actor_readonly_probe_20261014.py", 1
            )[0])
        self.assertIn("cancel-in-progress: false", source)
        self.assertNotIn("runs-on: [self-hosted", source.split("  inspect:", 1)[0])

    def test_pinned_credential_free_checkout_and_no_live_mutation(self):
        source = WORKFLOW.read_text()
        self.assertEqual(source.count(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2)
        self.assertEqual(source.count("persist-credentials: false"), 2)
        self.assertIn("contents: read", source)
        for forbidden in ("actions: write", "systemctl restart",
                          "sudo ", "watchTargets", "getWatcher",
                          "RDP_RELEVANT_ADDON_ID", "browser.tabs", "rm -rf"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        helper = SRC.read_text()
        self.assertNotIn('"type":"getWatcher"', helper)
        self.assertNotIn('"type":"watchTargets"', helper)
        self.assertNotIn("sendall(", helper.split("def send_root_addon_inventory", 1)[0])


if __name__ == "__main__":
    unittest.main()
