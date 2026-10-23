"""Adversarial tests for Firefox debugger process identity and CI wiring."""
import unittest
from pathlib import Path
from scripts.zcloud_firefox_rdp_ports_20261016 import extract_ports

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-firefox-worker-recovery-probe-20261013.yml"


def row(pid, port, *, binary="/snap/firefox/usr/lib/firefox/firefox", ppid=1):
    return f"{pid} {ppid} {binary} -start-debugger-server {port}"


class FirefoxPortTests(unittest.TestCase):
    def test_realistic_debugger_process(self):
        self.assertEqual(extract_ports([row(123, 6000) + " -profile /tmp/x"]), [6000])

    def test_whitespace_and_range_boundaries(self):
        self.assertEqual(extract_ports([" 123 0 firefox\t-start-debugger-server   9222"]), [9222])
        self.assertEqual(extract_ports([row(12, 1, binary="firefox-bin")]), [1])
        self.assertEqual(extract_ports([row(12, 65535, binary="/usr/bin/firefox")]), [65535])

    def test_invalid_ports_fail_closed(self):
        for port in ("0", "65536", "-1", "123456", "abc", "6000x", "", "0x1770"):
            with self.subTest(port=port):
                self.assertEqual(extract_ports([row(12, port)]), [])

    def test_reject_non_firefox_executable_even_when_args_contain_name(self):
        for binary in ("python3", "/usr/bin/bash", "grep", "/usr/bin/firefox-proxy",
                       "/usr/bin/evilfirefox", "/snap/firefox/usr/lib/firefox/firefox-script"):
            with self.subTest(binary=binary):
                self.assertEqual(extract_ports([row(123, 6000, binary=binary)]), [])

    def test_reject_missing_or_forged_pid_and_parent(self):
        for command in (
            "firefox -start-debugger-server 6000",
            "x 1 firefox -start-debugger-server 6000",
            "0 1 firefox -start-debugger-server 6000",
            "-1 1 firefox -start-debugger-server 6000",
            "123 -1 firefox -start-debugger-server 6000",
            "4294967296 1 firefox -start-debugger-server 6000",
            "1 4294967296 firefox -start-debugger-server 6000",
        ):
            with self.subTest(command=command):
                self.assertEqual(extract_ports([command]), [])

    def test_reject_malformed_and_injected_process_lines(self):
        for entry in (
            "123 1 firefox -start-debugger-server\\s+(\\d+)",
            row(12, 6000) + "\nmalicious",
            row(12, 6000) + "\x00x",
            row(12, 6000) + " -start-debugger-server 9222",
            row(12, 6000) + " --flag 3 -start-debugger-server bad",
        ):
            with self.subTest(entry=repr(entry)):
                self.assertEqual(extract_ports([entry]), [])

    def test_reject_duplicate_pid_even_with_distinct_ports(self):
        self.assertEqual(extract_ports([row(123, 6000), row(123, 9222)]), [])

    def test_reject_duplicate_ports_even_with_distinct_pids(self):
        self.assertEqual(extract_ports([row(123, 6000), row(456, 6000)]), [])

    def test_reject_oversized_or_non_string_process_data(self):
        self.assertEqual(extract_ports([row(123, 6000)] * 17), [])
        self.assertEqual(extract_ports([row(123, 6000) + "x" * 4096]), [])
        self.assertEqual(extract_ports([row(123, 6000), 7]), [])

    def test_non_list_and_absent_proof(self):
        for evidence in (None, [], "", {}, row(123, 6000)):
            with self.subTest(type=type(evidence).__name__):
                self.assertEqual(extract_ports(evidence), [])

    def test_two_distinct_processes_remain_ambiguous_for_single_parent_gate(self):
        self.assertEqual(extract_ports([row(123, 6000), row(456, 9222)]), [6000, 9222])

    def test_workflow_read_only_manual_admission_and_test_wiring(self):
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
