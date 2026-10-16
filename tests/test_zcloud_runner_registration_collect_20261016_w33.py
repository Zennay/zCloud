"""No network, no gh authentication: all cases use a mocked fixed GET."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SRC = SCRIPTS / 'zcloud_runner_registration_collect_20261016_w33.py'
spec = importlib.util.spec_from_file_location('zcloud_runner_registration_collect_20261016_w33', SRC)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
NOW = datetime(2026, 10, 16, 18, 35, tzinfo=timezone.utc)


def fixture(*, os='linux', ephemeral=False, name='zcloud-vps-1'):
    return {'total_count': 1, 'runners': [
        {'id': 25, 'name': name, 'status': 'online', 'busy': False,
         'os': os, 'ephemeral': ephemeral,
         'labels': [{'name': k, 'type': ('read-only' if k == 'self-hosted' else 'custom')} for k in ('self-hosted', 'zcloud', 'vps')]}
    ]}


def fake_get(*, output=None, code=0, failure=None):
    def run(argv, **kwargs):
        assert argv == collector.GH_GET
        assert kwargs == {
            'check': False, 'shell': False, 'timeout': 12,
            'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
            'stderr': subprocess.DEVNULL,
        }
        if failure is not None:
            raise failure
        return SimpleNamespace(returncode=code, stdout=(output if output is not None else json.dumps(fixture()).encode()))
    return run


class CollectorTests(unittest.TestCase):
    def check_safe(self, result):
        for k in ('independent_registration_verified', 'runner_group_access_verified',
                  'live_runner_health_verified', 'service_idle_verified',
                  'production_deploy_verified', 'restart_authorized',
                  'dispatch_authorized', 'mutation_performed'):
            self.assertIs(result[k], False, k)
        self.assertNotIn('runners', result)
        self.assertNotIn('id', result)
        self.assertNotIn('token', result)
        return result

    def test_canonical_fixed_get_and_expected_claim(self):
        result = collector.collect(executor=fake_get(), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'RUNNER_LISTED_ONLINE_IDLE')
        self.assertEqual(result['reason_code'], 'diagnostic_only')

    def test_hostname_and_path_are_hard_coded_not_user_inputs(self):
        self.assertEqual(collector.GH_GET, (
            'gh', 'api', '--hostname', 'github.com', '--method', 'GET',
            'repos/Zennay/zCloud/actions/runners?name=zcloud-vps-1&per_page=100'))

    def test_failed_get_does_not_leak_token_or_stderr(self):
        result = collector.collect(executor=fake_get(code=1, output=b'Authorization: bearer SECRET_VALUE'), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')
        self.assertNotIn('SECRET_VALUE', str(result))
        self.assertEqual(result['reason_code'], 'github_runner_read_failed')

    def test_auth_command_timeout_does_not_leak_arguments(self):
        err = subprocess.TimeoutExpired('gh api --hostname github.com bearer SECRET', 12, output=b'SECRET')
        result = collector.collect(executor=fake_get(failure=err), clock=NOW)
        self.assertEqual(self.check_safe(result)['reason_code'], 'github_runner_read_timeout')
        self.assertNotIn('SECRET', str(result))

    def test_missing_cli_binary_fails_closed(self):
        result = collector.collect(executor=fake_get(failure=FileNotFoundError('SECRET_PATH')), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')
        self.assertNotIn('SECRET_PATH', str(result))
        self.assertEqual(result['reason_code'], 'github_cli_not_installed')

    def test_unexpected_operating_system_error_redacted(self):
        result = collector.collect(executor=fake_get(failure=OSError('PRIVATE_SECRET_PATH')), clock=NOW)
        self.assertEqual(self.check_safe(result)['reason_code'], 'untrusted_runner_response')
        self.assertNotIn('PRIVATE_SECRET_PATH', str(result))

    def test_nonzero_cli_exit_cannot_be_claimed_as_runner_offline(self):
        result = collector.collect(executor=fake_get(code=4, output=json.dumps(fixture()).encode()), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')
        self.assertEqual(result['reason_code'], 'github_runner_read_failed')

    def test_malformed_json_denied(self):
        result=collector.collect(executor=fake_get(output=b'{invalid secret}'), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')
        self.assertEqual(result['reason_code'], 'untrusted_runner_response')

    def test_duplicate_json_keys_denied(self):
        raw=b'{"total_count":0,"total_count":1,"runners":[]}'
        result=collector.collect(executor=fake_get(output=raw), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')

    def test_too_large_data_denied(self):
        result=collector.collect(executor=fake_get(output=b'x'*(collector.MAX_INPUT+1)), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')

    def test_wrong_runner_name_denied(self):
        result=collector.collect(executor=fake_get(output=json.dumps(fixture(name='zcloud-vps-2')).encode()), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')

    def test_ephemeral_lookalike_denied(self):
        result=collector.collect(executor=fake_get(output=json.dumps(fixture(ephemeral=True)).encode()), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')

    def test_absent_runner_is_not_offline_proof(self):
        result=collector.collect(executor=fake_get(output=b'{"total_count":0,"runners":[]}'), clock=NOW)
        self.assertEqual(self.check_safe(result)['classification'], 'RUNNER_NOT_LISTED')

    def test_naive_clock_denied_without_mutation(self):
        result=collector.collect(executor=fake_get(), clock=datetime(2026, 10, 16, 18, 35))
        self.assertEqual(self.check_safe(result)['classification'], 'EVIDENCE_UNAVAILABLE')

    def test_cli_always_denies_even_if_claim_is_valid(self):
        with mock.patch.object(collector, 'collect', return_value=collector.collect(executor=fake_get(),clock=NOW)):
            with mock.patch('builtins.print') as printer:
                self.assertEqual(collector.main(), 2)
                result=json.loads(printer.call_args.args[0])
                self.check_safe(result)
                self.assertEqual(result['classification'], 'RUNNER_LISTED_ONLINE_IDLE')

    def test_real_bounded_capture_limits_bytes_before_buffering(self):
        # Exercises only Python subprocesses on the hosted test machine.
        cmd = (sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'x'*500000);sys.stdout.flush()")
        with mock.patch.object(collector, 'GH_GET', cmd):
            with self.assertRaisesRegex(collector.InvalidEvidence, 'invalid_input_size'):
                collector._bounded_gh_read(
                    cmd, check=False, shell=False, timeout=5,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

    def test_real_bounded_capture_accepts_small_response(self):
        cmd = (sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'{}');sys.stdout.flush()")
        with mock.patch.object(collector, 'GH_GET', cmd):
            done = collector._bounded_gh_read(
                cmd, check=False, shell=False, timeout=5,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, b'{}')

    def test_real_bounded_capture_times_out_without_waiting_forever(self):
        cmd = (sys.executable, '-c', "import time;time.sleep(2)")
        with mock.patch.object(collector, 'GH_GET', cmd):
            with self.assertRaises(subprocess.TimeoutExpired):
                collector._bounded_gh_read(
                    cmd, check=False, shell=False, timeout=0.05,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

    def test_timeout_terminates_spawned_stdout_holder(self):
        # Parent exits, but its descendant inherits the stdout pipe and
        # would otherwise survive an idle/timeout read. A file marker proves
        # the descendant did NOT survive long enough to execute its payload.
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            marker = Path(directory) / 'descendant-still-alive'
            code = (
                "import pathlib,time;"
                "time.sleep(0.6);"
                "pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
            )
            parent = (
                "import subprocess,sys;"
                "subprocess.Popen([sys.executable,'-c'," + repr(code) +
                "],stdout=sys.stdout,stderr=subprocess.DEVNULL);"
                "print('spawned',flush=True)"
            )
            argv=(sys.executable,'-c',parent)
            with mock.patch.object(collector, 'GH_GET', argv):
                with self.assertRaises(subprocess.TimeoutExpired):
                    collector._bounded_gh_read(
                        argv,check=False,shell=False,timeout=0.2,
                        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
            time.sleep(0.7)
            self.assertFalse(marker.exists(), 'timeout left a live descendant')

    def test_fixed_get_child_cannot_become_shell_script(self):
        with self.assertRaisesRegex(collector.InvalidEvidence, 'unexpected_runner_request'):
            collector._bounded_gh_read(
                ('sh','-c','true'), check=False, shell=False, timeout=1,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

    def test_bounded_capture_does_not_accept_untrusted_command(self):
        with self.assertRaisesRegex(collector.InvalidEvidence, 'unexpected_runner_request'):
            collector._bounded_gh_read(
                ('gh', 'api', 'other/repo'), check=False, shell=False, timeout=5,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

    def test_no_dynamic_shell_or_writes(self):
        source = SRC.read_text()
        for pattern in ('shell=True', 'systemctl', 'sudo ', 'os.system', 'with open(', 'write_text(',
                        'GH_TOKEN', 'GITHUB_TOKEN', 'requests.', 'urllib.request'):
            self.assertNotIn(pattern, source)


if __name__ == '__main__':
    unittest.main()
