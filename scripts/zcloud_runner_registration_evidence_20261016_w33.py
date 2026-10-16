#!/usr/bin/env python3
"""Offline, strictly read-only triage of a captured zCloud repository runner list.

Accepts an operator-captured GitHub REST Actions runners response from stdin.
This is a *claim* inspection: no bearer token, GET, shell, service or write.
It NEVER proves fresh runner health/registration-group access or authorizes a restart.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import sys

REPO = 'Zennay/zCloud'
API_URL = 'https://api.github.com/repos/Zennay/zCloud/actions/runners?name=zcloud-vps-1&per_page=100'
RUNNER = 'zcloud-vps-1'
REQUIRED_LABELS = frozenset({'self-hosted', 'zcloud', 'vps'})
MAX_INPUT = 262144
MAX_AGE = 300
MAX_FUTURE_SKEW = 15
SCHEMA = 'zcloud-runner-registration-evidence-v1'
SAFE_DENIAL_REASONS = frozenset({
    'invalid_input_size', 'duplicate_json_key', 'nonfinite_number', 'malformed_json',
    'not_object', 'bad_observation_time', 'unbound_repository', 'unbound_api_response',
    'unknown_clock', 'stale_observation', 'future_observation', 'missing_runner_response',
    'incomplete_runner_inventory', 'unbounded_runner_inventory', 'malformed_runner',
    'invalid_runner_identity', 'ambiguous_runner_identity', 'invalid_runner_state',
    'invalid_runner_labels', 'duplicate_runner_label', 'runner_name_filter_mismatch',
    'unexpected_runner_os', 'unexpected_runner_lifecycle',
    'invalid_runner_label_provenance',
})

class InvalidEvidence(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence('duplicate_json_key')
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise InvalidEvidence('nonfinite_number')
    return number


def load_json(raw: bytes):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_INPUT:
        raise InvalidEvidence('invalid_input_size')
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(InvalidEvidence('nonfinite_number')),
                         parse_float=lambda n: _finite_float(n))
        # json.loads accepts escaped lone surrogates; they cannot be valid canonical evidence.
        json.dumps(obj, ensure_ascii=False).encode('utf-8')
    except InvalidEvidence:
        raise
    except (UnicodeError, ValueError, RecursionError, TypeError) as exc:
        raise InvalidEvidence('malformed_json') from exc
    if not isinstance(obj, dict):
        raise InvalidEvidence('not_object')
    return obj


def _timestamp(value):
    if type(value) is not str or len(value) > 40 or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value):
        raise InvalidEvidence('bad_observation_time')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise InvalidEvidence('bad_observation_time') from exc
    if dt.tzinfo is None:
        raise InvalidEvidence('bad_observation_time')
    return dt.astimezone(timezone.utc)


def inspect(envelope, *, now=None):
    """Validate a complete single-page repo runner sample; report claimed state only."""
    if not isinstance(envelope, dict) or envelope.get('repository') != REPO:
        raise InvalidEvidence('unbound_repository')
    # Require the precise repository-scoped endpoint and successful GET receipt.
    # These fields are operator-supplied claims, NOT network authenticity proof.
    if (envelope.get('request_url') != API_URL
            or envelope.get('method') != 'GET'
            or type(envelope.get('http_status')) is not int
            or envelope['http_status'] != 200):
        raise InvalidEvidence('unbound_api_response')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise InvalidEvidence('unknown_clock')
    dt = _timestamp(envelope.get('captured_at'))
    age = (now.astimezone(timezone.utc) - dt).total_seconds()
    if age > MAX_AGE:
        raise InvalidEvidence('stale_observation')
    if age < -MAX_FUTURE_SKEW:
        raise InvalidEvidence('future_observation')
    payload = envelope.get('response')
    if not isinstance(payload, dict):
        raise InvalidEvidence('missing_runner_response')
    total = payload.get('total_count')
    entries = payload.get('runners')
    if type(total) is not int or total < 0 or total > 100 or not isinstance(entries, list) or len(entries) != total:
        raise InvalidEvidence('incomplete_runner_inventory')
    if len(entries) > 100:
        raise InvalidEvidence('unbounded_runner_inventory')
    ids, names, target = set(), set(), None
    for runner in entries:
        if not isinstance(runner, dict):
            raise InvalidEvidence('malformed_runner')
        rid, name = runner.get('id'), runner.get('name')
        if type(rid) is not int or rid <= 0 or type(name) is not str or not (1 <= len(name) <= 128):
            raise InvalidEvidence('invalid_runner_identity')
        if rid in ids or name.casefold() in names:
            raise InvalidEvidence('ambiguous_runner_identity')
        ids.add(rid)
        names.add(name.casefold())
        # A name-filtered API request must not return unrelated runner records.
        if name.casefold() != RUNNER.casefold():
            raise InvalidEvidence('runner_name_filter_mismatch')
        if runner.get('status') not in ('online', 'offline') or type(runner.get('busy')) is not bool:
            raise InvalidEvidence('invalid_runner_state')
        labels = runner.get('labels')
        if not isinstance(labels, list) or len(labels) > 64:
            raise InvalidEvidence('invalid_runner_labels')
        normalized = set()
        for label in labels:
            if not isinstance(label, dict):
                raise InvalidEvidence('invalid_runner_labels')
            n = label.get('name')
            if type(n) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', n):
                raise InvalidEvidence('invalid_runner_labels')
            label_kind = label.get('type')
            if label_kind not in ('read-only', 'custom'):
                raise InvalidEvidence('invalid_runner_labels')
            canonical = n.casefold()
            if canonical in normalized:
                raise InvalidEvidence('duplicate_runner_label')
            # GitHub assigns the default self-hosted label as read-only.
            # zcloud and vps are custom operator labels, not implicit host facts.
            if ((canonical == 'self-hosted' and label_kind != 'read-only')
                    or (canonical in ('zcloud', 'vps') and label_kind != 'custom')):
                raise InvalidEvidence('invalid_runner_label_provenance')
            normalized.add(canonical)
        if name.casefold() == RUNNER.casefold():
            # This is the permanent Linux VPS listener, not an ephemeral
            # runner provisioned with a coincidentally matching name.
            if runner.get('os') != 'linux':
                raise InvalidEvidence('unexpected_runner_os')
            if type(runner.get('ephemeral')) is not bool or runner['ephemeral']:
                raise InvalidEvidence('unexpected_runner_lifecycle')
            target = (runner['status'], runner['busy'], normalized)
    if target is None:
        category, next_action = 'RUNNER_NOT_LISTED', 'check_repository_runner_registration_and_group_entitlement'
    elif target[1] and target[0] == 'offline':
        # GitHub reports the listener offline while a job is still busy.
        # Do not route an operator to label edits or an idle recovery.
        category, next_action = 'RUNNER_LISTED_OFFLINE_BUSY', 'preserve_potential_active_worker_and_inspect_process_independently'
    elif target[1]:
        # A busy listener always outranks label mismatch in operator triage.
        category, next_action = 'RUNNER_LISTED_BUSY', 'preserve_active_worker_and_check_concurrency'
    elif not REQUIRED_LABELS.issubset(target[2]):
        category, next_action = 'LABEL_MISMATCH', 'inspect_missing_routing_labels_after_proving_worker_idle'
    elif target[0] == 'offline':
        category, next_action = 'RUNNER_LISTED_OFFLINE', 'inspect_listener_and_active_worker_on_independent_authorized_host'
    else:
        category, next_action = 'RUNNER_LISTED_ONLINE_IDLE', 'correlate_queued_run_approval_labels_and_group_access'
    return {'schema': SCHEMA, 'classification': category, 'next_action': next_action,
            'runner_target': RUNNER, 'required_labels': sorted(REQUIRED_LABELS),
            'sampled_runner_count': total, 'observation_age_seconds': max(0, int(age)),
            'routing_labels_match': (REQUIRED_LABELS.issubset(target[2]) if target is not None else None),
            'registration_claim_parsed': True, 'independent_registration_verified': False,
            'runner_group_access_verified': False, 'live_runner_health_verified': False,
            'service_idle_verified': False, 'production_deploy_verified': False,
            'restart_authorized': False, 'dispatch_authorized': False, 'mutation_performed': False}


def main():
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        result = inspect(load_json(data))
        result['reason_code'] = 'diagnostic_only'
        # Exit is NEVER an admission signal: diagnostic claims always deny by default.
        code = 2
    except (InvalidEvidence, ValueError, TypeError, OverflowError) as exc:
        # Never reflect attacker-controlled exception strings or source content.
        reason = str(exc) if type(exc) is InvalidEvidence else None
        result = {'schema': SCHEMA, 'classification': 'EVIDENCE_UNAVAILABLE',
                  'reason_code': reason if reason in SAFE_DENIAL_REASONS else 'unknown_error',
                  'registration_claim_parsed': False, 'independent_registration_verified': False,
                  'runner_group_access_verified': False, 'live_runner_health_verified': False,
                  'service_idle_verified': False, 'production_deploy_verified': False,
                  'restart_authorized': False, 'dispatch_authorized': False, 'mutation_performed': False}
        code = 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    sys.exit(main())
