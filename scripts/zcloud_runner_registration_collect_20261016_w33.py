#!/usr/bin/env python3
"""Read-only, credential-redacting operator-side zCloud runner inventory capture.

Requires an authenticated, trusted GitHub CLI (gh) in the operator's local
environment. Only one fixed, repository-scoped GET is allowed. Never stores
or prints the raw response or reveals the bearer token. Exit code 2 is
intentional even on valid classification: evidence is not release authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import subprocess
import sys

from zcloud_runner_registration_evidence_20261016_w33 import (
    API_URL, MAX_INPUT, SCHEMA, InvalidEvidence, inspect, load_json,
)

GH_GET = (
    'gh', 'api', '--hostname', 'github.com', '--method', 'GET',
    'repos/Zennay/zCloud/actions/runners?name=zcloud-vps-1&per_page=100',
)


def collect(*, executor=subprocess.run, clock=None):
    """Return sanitized untrusted diagnostic result, never runner source JSON."""
    try:
        # Constant argv: no arbitrary request URL, host, runner name or shell.
        proc = executor(
            GH_GET, check=False, shell=False, timeout=12,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            raise InvalidEvidence('runner_admin_read_unavailable')
        if type(proc.stdout) is not bytes or len(proc.stdout) > MAX_INPUT:
            raise InvalidEvidence('invalid_input_size')
        payload = load_json(proc.stdout)
        observation_time = datetime.now(timezone.utc) if clock is None else clock
        if not isinstance(observation_time, datetime) or observation_time.tzinfo is None:
            raise InvalidEvidence('unknown_clock')
        envelope = {
            'repository': 'Zennay/zCloud',
            'request_url': API_URL,
            'method': 'GET',
            # A successful GH CLI JSON response for this fixed GET is
            # not transport identity or runner-group attestation.
            'http_status': 200,
            'captured_at': observation_time.astimezone(timezone.utc).isoformat(),
            'response': payload,
        }
        report = inspect(envelope, now=observation_time)
        report['reason_code'] = 'diagnostic_only'
        return report
    except (InvalidEvidence, subprocess.TimeoutExpired, OSError, ValueError,
            TypeError, OverflowError) as error:
        # Only fixed safe constants: never echo subprocess stderr, HTTP data,
        # token-bearing argv or exception text to the operator or CI logs.
        if isinstance(error, FileNotFoundError):
            reason = 'github_cli_not_installed'
        elif isinstance(error, subprocess.TimeoutExpired):
            reason = 'github_runner_read_timeout'
        elif type(error) is InvalidEvidence and str(error) == 'runner_admin_read_unavailable':
            reason = 'github_runner_read_failed'
        else:
            reason = 'untrusted_runner_response'
        return {
            'schema': SCHEMA, 'classification': 'EVIDENCE_UNAVAILABLE',
            'reason_code': reason,
            'registration_claim_parsed': False,
            'independent_registration_verified': False,
            'runner_group_access_verified': False,
            'live_runner_health_verified': False,
            'service_idle_verified': False,
            'production_deploy_verified': False,
            'restart_authorized': False,
            'dispatch_authorized': False,
            'mutation_performed': False,
        }


def main():
    print(json.dumps(collect(), sort_keys=True, separators=(',', ':')))
    return 2


if __name__ == '__main__':
    sys.exit(main())
