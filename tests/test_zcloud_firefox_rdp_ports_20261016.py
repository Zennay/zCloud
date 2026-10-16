"""Adversarial tests for read-only Firefox debugger detection and CI wiring."""
import unittest
from pathlib import Path
from scripts.zcloud_firefox_rdp_ports_20261016 import extract_ports

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-firefox-worker-recovery-probe-20261013.yml"


class FirefoxPortTests(unittest.TestCase):
    def test_realistic_debugger_process(self):
        self.assertEqual(extract_ports(["123 1 /snap/firefox/usr/lib/firefox/firefox -start-debugger-server 6000 -profile /tmp/x"]), [6000])

    def test_whitespace_and_range_boundaries(self):
        self.assertEqual(extract_ports(["firefox\t-start-debugger-server   9222"]), [9222])
        self.assertEqual(extract_ports(["firefox -start-debugger-server 1"]), [1])
        self.assertEqual(extract_ports(["firefox -start-debugger-server 65535"]), [65535])

    def test_invalid_ports_fail_closed(self):
        for port in ("0", "65536", "-1", "123456", "abc", "6000x", "", "0x1770"):
            with self.subTest(port=port):
                self.assertEqual(extract_ports(["firefox -start-debugger-server " + port]), [])

    def test_reject_malformed_and_injected_process_lines(self):
        for entry in ("python -start-debugger-server 6000", "firefox -start-debugger-server\\s+(\\d+)",
                      "firefox -start-debugger-server 6000\nmalicious", "firefox -start-debugger-server 6000\x00x",
                      "firefox -start-debugger-server 6000 -start-debugger-server 9222"):
            with self.subTest(entry=repr(entry)):
                self.assertEqual(extract_ports([entry]), [])

    def test_reject_ambiguous_processes_and_oversize(self):
        self.assertEqual(extract_ports(["firefox -start-debugger-server 6000"] * 2), [])
        self.assertEqual(extract_ports(["firefox -start-debugger-server 6000"] * 17), [])
        self.assertEqual(extract_ports(["firefox " + "x" * 4096 + " -start-debugger-server 6000"]), [])
        self.assertEqual(extract_ports(["firefox -start-debugger-server 6000", 7]), [])

    def test_non_list_and_absent_proof(self):
        for evidence in (None, [], "", {}, "firefox -start-debugger-server 6000"):
            with self.subTest(type=type(evidence).__name__):
                self.assertEqual(extract_ports(evidence), [])

    def test_distinct_ports_remain_ambiguous_for_existing_single_parent_gate(self):
        ports = extract_ports(["firefox -start-debugger-server 6000",
                               "firefox -start-debugger-server 9222"])
        self.assertEqual(ports, [6000, 9222])

    def test_workflow_manual_read_only_admission_and_port_parser(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        for expected in (
            "github.event_name == 'workflow_dispatch'",
            "github.run_attempt == 1",
            "inputs.confirm_sha == github.sha",
            "runs-on: [self-hosted, zcloud, vps]",
            "test \"$(git ls-remote --exit-code origin refs/heads/main | cut -f1)\" = \"$CONFIRMED_SHA\"",
            "from scripts.zcloud_firefox_rdp_ports_20261016 import extract_ports",
            "ports=extract_ports(main)",
            "if len(ports)!=1:",
            "python3 -m unittest -v tests.test_zcloud_firefox_rdp_ports_20261016",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, content)


if __name__ == "__main__":
    unittest.main()
