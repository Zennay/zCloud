"""Validate CI admission without searching the workflow's own grep command."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / '.github/workflows/zcloud-runner-route-triage-hosted-20261013-w4.yml'


def screen(text):
    header, separator, jobs = text.partition('\njobs:\n')
    if not separator:
        raise ValueError('jobs_section_missing')
    if not header.startswith('name: zCloud deploy-ops runner-route triage (hosted only)'):
        raise ValueError('unexpected_workflow')
    if '  push:\n' not in header or '    branches:\n      - fix/deployops-runner-route-triage-20261013-w4\n' not in header:
        raise ValueError('unexpected_trigger')
    for trigger in ('pull_request:', 'workflow_run:', 'workflow_dispatch:', 'schedule:'):
        if trigger in header: raise ValueError('privileged_trigger')
    if re.search(r'(?m)^\s*(?:actions|administration|contents):\s*write\b', header):
        raise ValueError('write_permissions')
    if '\npermissions:\n  contents: read\n' not in header:
        raise ValueError('nonminimal_permissions')
    rows = [line.strip() for line in jobs.splitlines()]
    runs_on = [line for line in rows if line.startswith('runs-on:')]
    if not runs_on or runs_on != ['runs-on: ubuntu-latest']:
        raise ValueError('self_hosted_or_unknown_runner')
    if "github.repository == 'Zennay/zCloud'" not in jobs:
        raise ValueError('repository_gate_missing')
    if 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' not in jobs:
        raise ValueError('unpinned_checkout')
    if 'persist-credentials: false' not in jobs:
        raise ValueError('credentials_persisted')
    if "-p 'test_zcloud_runner_route*_20261013.py'" not in jobs:
        raise ValueError('validation_missing')
    if re.search(r'(?mi)^\s*(?:sudo\b|systemctl\b|curl\b.*-X\s*POST|gh\s+(?:api|workflow|run)\b)', jobs):
        raise ValueError('mutating_step')
    return True


class HostedOnlyWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = WF.read_text(encoding='utf-8')

    def test_hosted_workflow_green(self):
        self.assertTrue(screen(self.content))

    def test_privileged_event_denied(self):
        with self.assertRaisesRegex(ValueError, 'privileged_trigger'):
            screen(self.content.replace('  push:\n', '  workflow_dispatch:\n  push:\n', 1))

    def test_selfhosted_denied(self):
        with self.assertRaisesRegex(ValueError, 'self_hosted_or_unknown_runner'):
            screen(self.content.replace('runs-on: ubuntu-latest', 'runs-on: [self-hosted, zcloud, vps]', 1))

    def test_write_permission_denied(self):
        with self.assertRaisesRegex(ValueError, 'write_permissions'):
            screen(self.content.replace('  contents: read', '  contents: write', 1))

    def test_checkout_credentials_denied(self):
        with self.assertRaisesRegex(ValueError, 'credentials_persisted'):
            screen(self.content.replace('persist-credentials: false', 'persist-credentials: true', 1))

    def test_missing_ci_execution_denied(self):
        with self.assertRaisesRegex(ValueError, 'validation_missing'):
            screen(self.content.replace("-p 'test_zcloud_runner_route*_20261013.py'", "-p 'something_else.py'", 1))


if __name__ == '__main__':
    unittest.main()
