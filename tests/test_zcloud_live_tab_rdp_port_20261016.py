"""Offline negative tests for the live-tab debugger-port process boundary."""
import unittest
from pathlib import Path

from scripts.zcloud_live_tab_rdp_port_20261016 import detect_debugger_port

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-live-tab-version-probe.yml"


def row(pid=123, ppid=1, binary="/snap/firefox/usr/lib/firefox/firefox", port="6000"):
    return f"{pid} {ppid} {binary} -start-debugger-server {port}"


class LiveTabPortIdentityTests(unittest.TestCase):
    def test_positive_real_firefox_command(self):
        self.assertEqual(detect_debugger_port([row()]), ("unique", 6000))
        self.assertEqual(detect_debugger_port([row(binary="firefox-bin", port="9222")]), ("unique", 9222))

    def test_no_match_stays_absent(self):
        self.assertEqual(detect_debugger_port(["42 1 /usr/bin/python3 worker.py"]), ("absent", None))
        self.assertEqual(detect_debugger_port([]), ("absent", None))

    def test_child_content_process_excluded(self):
        self.assertEqual(detect_debugger_port([
            row(), row(pid=124) + " -contentproc 1",
        ]), ("unique", 6000))

    def test_shell_python_and_grep_spoofs_are_not_valid_debugger(self):
        for binary in ("bash", "/usr/bin/python3", "grep", "/opt/firefox-fake", "evilfirefox"):
            with self.subTest(binary=binary):
                self.assertEqual(detect_debugger_port([row(binary=binary)]), ("ambiguous", None))

    def test_spoof_invalidates_valid_single_debugger_proof(self):
        self.assertEqual(detect_debugger_port([row(), row(pid=999, binary="python3")]), ("ambiguous", None))

    def test_bad_process_fields(self):
        for line in (
            "firefox -start-debugger-server 6000",
            "0 1 firefox -start-debugger-server 6000",
            "-1 1 firefox -start-debugger-server 6000",
            "1 -1 firefox -start-debugger-server 6000",
            "4294967296 1 firefox -start-debugger-server 6000",
            "1 4294967296 firefox -start-debugger-server 6000",
        ):
            with self.subTest(line=line):
                self.assertEqual(detect_debugger_port([line]), ("ambiguous", None))

    def test_invalid_debugger_ports(self):
        for port in ("0", "65536", "-1", "123456", "x", "6000x", ""):
            with self.subTest(port=port):
                self.assertEqual(detect_debugger_port([row(port=port)]), ("ambiguous", None))

    def test_duplicated_flag_invalid_even_if_second_malformed(self):
        for line in (
            row() + " -start-debugger-server 7000",
            row() + " -start-debugger-server broken",
            row() + " -start-debugger-server=9222",
        ):
            with self.subTest(line=line):
                self.assertEqual(detect_debugger_port([line]), ("ambiguous", None))

    def test_multiple_processes_are_ambiguous(self):
        self.assertEqual(detect_debugger_port([row(), row(pid=456, port="9222")]), ("ambiguous", None))
        self.assertEqual(detect_debugger_port([row(), row(pid=456)]), ("ambiguous", None))

    def test_duplicate_pid_ambiguous(self):
        self.assertEqual(detect_debugger_port([row(), row(port="9222")]), ("ambiguous", None))

    def test_invalid_or_oversized_evidence_is_ambiguous(self):
        for item in (None, {}, "", 12, ["x"] * 32769, [row() + "x" * 4096],
                     [row() + "\nmalicious"], [row() + "\0"], [row(), None]):
            with self.subTest(item=repr(item)[:60]):
                self.assertEqual(detect_debugger_port(item), ("ambiguous", None))

    def test_unnumbered_or_non_firefox_debugger_row_is_not_accepted(self):
        for line in (
            "42 1 /usr/bin/another -start-debugger-server 6000 /firefox",
            "42 1 /snap/firefox-not-browser -start-debugger-server 6000",
        ):
            self.assertEqual(detect_debugger_port([line]), ("ambiguous", None))

    def test_not_authorization_and_no_process_or_network_io(self):
        source = (ROOT / "scripts/zcloud_live_tab_rdp_port_20261016.py").read_text()
        self.assertNotIn("socket.", source)
        self.assertNotIn("subprocess.", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("http", source)

    def test_workflow_preserves_manual_gate_and_checks_exact_candidate(self):
        workflow = WORKFLOW.read_text()
        for marker in (
            "runs-on: ubuntu-latest",
            "runs-on: [self-hosted, ftmo-research]",
            "github.event_name == 'workflow_dispatch'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.approve_readonly_probe == true",
            "inputs.confirm_sha == github.sha",
            "persist-credentials: false",
            "test \"$current_main\" = \"$GITHUB_SHA\"",
            "from scripts.zcloud_live_tab_rdp_port_20261016 import detect_debugger_port",
            "status,port=detect_debugger_port(proc.stdout.splitlines())",
            "python3 -m unittest -v tests.test_zcloud_live_tab_rdp_port_20261016",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, workflow)


if __name__ == "__main__":
    unittest.main()
