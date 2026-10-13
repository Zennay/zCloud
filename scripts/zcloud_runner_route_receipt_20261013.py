#!/usr/bin/env python3
"""Sanitize a read-only Actions queue snapshot into an operator-safe receipt.

This parser cannot promote observation to runner, cancellation, or deploy
authority. It consumes the local output of --jobs-only and never uses network.
"""
import argparse
import json
from pathlib import Path
import sys
from zcloud_runner_route_triage_20261013 import EvidenceError, MAX_BODY, strict_json

SAFE_CLASSES = {
    0: {'no_stale_unassigned_jobs'},
    1: {'runner_busy', 'runner_offline', 'labels_or_access_unproven',
        'route_available_but_queued'},
    2: {'unknown_incomplete_evidence'},
}
DENIED = ('recovery_authorized', 'queue_cancel_authorized', 'deploy_authorized')


def checked_count(value):
    if type(value) is not int or not 0 <= value <= 500:
        raise EvidenceError('invalid_count')
    return value


def receipt(blob, exit_code):
    if type(exit_code) is not int or exit_code not in SAFE_CLASSES:
        raise EvidenceError('invalid_probe_exit')
    obj = strict_json(blob)
    if not isinstance(obj, dict) or obj.get('classification') not in SAFE_CLASSES[exit_code]:
        raise EvidenceError('invalid_probe_classification')
    if any(obj.get(k) is not False for k in DENIED):
        raise EvidenceError('unexpected_mutation_authority')
    if 'generation_verified' in obj and obj['generation_verified'] is not False:
        raise EvidenceError('generation_not_disproved')
    if exit_code == 2:
        # On a failed API request, even the queue inventory may be absent.
        count = checked_count(obj.get('stale_unassigned_jobs', 0))
        gaps = checked_count(obj.get('unknown_stale_queued_runs', 0))
        if obj.get('runner_inventory_verified') is True:
            raise EvidenceError('untrusted_runner_roster')
    else:
        if obj.get('runner_inventory_verified') is not False or obj.get('evidence_mode') != 'github_api_jobs_only':
            raise EvidenceError('not_jobs_only_evidence')
        count = checked_count(obj.get('stale_unassigned_jobs'))
        gaps = checked_count(obj.get('unknown_stale_queued_runs'))
        if exit_code == 0 and (count or gaps):
            raise EvidenceError('false_green_queue_receipt')
        if exit_code == 1 and (not count or gaps):
            raise EvidenceError('contradictory_queue_receipt')
    return {
        'evidence': 'github_actions_get_only',
        'classification': obj['classification'],
        'probe_exit': exit_code,
        'stale_unassigned_jobs': count,
        'unknown_stale_queued_runs': gaps,
        'runner_inventory_verified': False,
        'mutation_authorized': False,
        'production_status_verified': False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot_file')
    p.add_argument('probe_exit', type=int)
    args = p.parse_args(argv)
    try:
        with Path(args.snapshot_file).open('rb') as fh:
            body = fh.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise EvidenceError('oversized_probe_output')
        item = receipt(body, args.probe_exit)
    except (EvidenceError, OSError):
        # Never echo the path, raw response, GitHub token, or incident metadata.
        print(json.dumps({'evidence': 'invalid_probe_output',
                          'mutation_authorized': False,
                          'production_status_verified': False}, sort_keys=True))
        return 2
    print(json.dumps(item, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
