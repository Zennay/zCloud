"""Offline negative tests for the live-tab debugger-port process boundary."""
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.zcloud_live_tab_rdp_port_20261016 import (
    detect_debugger_port, trusted_firefox_executable,
)

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
                # A non-Firefox process with no Firefox reference is not a candidate.
                if "firefox" not in binary:
                    self.assertEqual(detect_debugger_port([row(binary=binary)]), ("absent", None))
                # A forged argv which *mentions* Firefox must never establish
                # (or silently coexist with) debugger uniqueness.
                self.assertEqual(detect_debugger_port([row(binary=binary) + " /firefox"]), ("ambiguous", None))

    def test_spoof_invalidates_valid_single_debugger_proof(self):
        self.assertEqual(detect_debugger_port([row(), row(pid=999, binary="python3") + " /firefox"]), ("ambiguous", None))

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

    def test_kernel_executable_attestation_allows_only_system_firefox(self):
        for executable in (
            "/snap/firefox/777/usr/lib/firefox/firefox",
            "/snap/firefox/12/usr/lib/firefox/firefox-bin",
            "/usr/lib/firefox/firefox",
            "/usr/lib/firefox-esr/firefox-esr",
            "/usr/bin/firefox",
        ):
            with self.subTest(executable=executable):
                with patch("scripts.zcloud_live_tab_rdp_port_20261016.os.readlink",
                           return_value=executable) as readlink:
                    self.assertTrue(trusted_firefox_executable(123))
                    self.assertEqual(readlink.call_args.args, ("/proc/123/exe",))
                    self.assertEqual(
                        detect_debugger_port([row()],
                                             verify_executable=trusted_firefox_executable),
                        ("unique", 6000),
                    )

    def test_kernel_executable_attestation_denies_fake_binary_and_deleted_file(self):
        for executable in (
            "/tmp/firefox", "/home/ubuntu/firefox",
            "/snap/other/12/usr/lib/firefox/firefox",
            "/usr/lib/firefox/firefox-malware",
            "/snap/firefox/777/usr/lib/firefox/firefox (deleted)",
            "firefox", "",
        ):
            with self.subTest(executable=executable):
                with patch("scripts.zcloud_live_tab_rdp_port_20261016.os.readlink",
                           return_value=executable):
                    self.assertFalse(trusted_firefox_executable(123))
                    self.assertEqual(
                        detect_debugger_port([row()],
                                             verify_executable=trusted_firefox_executable),
                        ("ambiguous", None),
                    )

    def test_kernel_identity_unreadable_or_bad_pid_fails_closed(self):
        for invalid in (0, -1, 4294967296, True, "123", None):
            with self.subTest(pid=invalid):
                self.assertFalse(trusted_firefox_executable(invalid))
        with patch("scripts.zcloud_live_tab_rdp_port_20261016.os.readlink",
                   side_effect=OSError("permission denied")):
            self.assertFalse(trusted_firefox_executable(123))
            self.assertEqual(
                detect_debugger_port([row()],
                                     verify_executable=trusted_firefox_executable),
                ("ambiguous", None),
            )

    def test_executable_callback_failure_never_allows_port(self):
        self.assertEqual(
            detect_debugger_port([row()], verify_executable=lambda pid: False),
            ("ambiguous", None),
        )
        def raises(_pid):
            raise RuntimeError("untrusted proc evidence")
        self.assertEqual(
            detect_debugger_port([row()], verify_executable=raises),
            ("ambiguous", None),
        )

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
            "runs-on: [self-hosted, zcloud, vps]",
            "github.event_name == 'workflow_dispatch'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.approve_readonly_probe == true",
            "inputs.confirm_sha == github.sha",
            "persist-credentials: false",
            "python3 scripts/zcloud_vps_runner_guard.py --json",
            "test \"$current_main\" = \"$GITHUB_SHA\"",
            "from scripts.zcloud_live_tab_rdp_port_20261016 import (",
            "trusted_firefox_executable",
            "verify_executable=trusted_firefox_executable",
            "status,port=detect_debugger_port(",
            "verify_executable=trusted_firefox_executable,",
            "python3 -m unittest -v tests.test_zcloud_live_tab_rdp_port_20261016",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, workflow)


if __name__ == "__main__":
    unittest.main()
