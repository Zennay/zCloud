#!/usr/bin/env python3
"""Read-only, fail-closed GitHub Actions runner-route triage for zCloud #1326.

Reads only GitHub Actions GET endpoints, or an offline snapshot; never mutates
queues, services, runners, browsers or deployments. Results are not authority.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = 'Zennay/zCloud'
MAX_BODY = 262144
MAX_PAGES = 5

class EvidenceError(Exception):
    pass

def unique_pairs(pairs):
    output = {}
    for k, v in pairs:
        if k in output: raise EvidenceError('duplicate_json_key')
        output[k] = v
    return output

def strict_json(raw):
    try:
        result = json.loads(raw, object_pairs_hook=unique_pairs,
                            parse_constant=lambda c: (_ for _ in ()).throw(EvidenceError('nonfinite_json')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceError('invalid_json') from exc
    def validate(obj, depth=0):
        if depth > 40: raise EvidenceError('deep_json')
        if isinstance(obj, str) and (len(obj) > 8192 or any(0xd800 <= ord(c) <= 0xdfff for c in obj)):
            raise EvidenceError('invalid_json_string')
        if isinstance(obj, dict):
            for k, v in obj.items(): validate(k, depth + 1); validate(v, depth + 1)
        if isinstance(obj, list):
            for v in obj: validate(v, depth + 1)
    validate(result)
    return result

def when(value):
    if not isinstance(value, str) or len(value) > 48:
        raise EvidenceError('invalid_timestamp')
    try: result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc: raise EvidenceError('invalid_timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise EvidenceError('naive_timestamp')
    return result.astimezone(timezone.utc)

def array(value, label):
    if not isinstance(value, list): raise EvidenceError('missing_' + label)
    return value

def labels(value):
    names = set()
    for item in array(value, 'labels'):
        name = item.get('name') if isinstance(item, dict) else item
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name):
            raise EvidenceError('invalid_label')
        names.add(name.lower())
    return names

def classify(payload, now, stale_seconds=180):
    if not isinstance(payload, dict) or payload.get('repository') != REPO:
        raise EvidenceError('repository_mismatch')
    if abs((now - when(payload.get('captured_at'))).total_seconds()) > 120:
        raise EvidenceError('stale_or_future_snapshot')
    runners = array(payload.get('runners'), 'runners')
    runs = array(payload.get('queued_runs'), 'queued_runs')
    jobs_by_run = payload.get('jobs_by_run')
    if not isinstance(jobs_by_run, dict): raise EvidenceError('missing_jobs')
    if len(runners) > 500 or len(runs) > 40: raise EvidenceError('incomplete_inventory')
    ids = set()
    for runner in runners:
        if not isinstance(runner, dict) or type(runner.get('id')) is not int or runner['id'] <= 0 or runner['id'] in ids:
            raise EvidenceError('invalid_or_duplicate_runner')
        ids.add(runner['id'])
        if runner.get('status') not in ('online', 'offline') or type(runner.get('busy')) is not bool:
            raise EvidenceError('unknown_runner_state')
        labels(runner.get('labels'))
    counters = {'offline': 0, 'busy': 0, 'label_or_access_gap': 0, 'group_access_unverified': 0, 'available_but_queued': 0}
    seen_runs = set()
    for run in runs:
        if not isinstance(run, dict) or type(run.get('id')) is not int or run['id'] <= 0 or run['id'] in seen_runs or run.get('status') != 'queued':
            raise EvidenceError('invalid_or_duplicate_run')
        rid = run['id']; seen_runs.add(rid)
        if str(rid) not in jobs_by_run: raise EvidenceError('incomplete_run_jobs')
        jobs = array(jobs_by_run[str(rid)], 'run_jobs')
        if len(jobs) > 500: raise EvidenceError('incomplete_run_jobs')
        seen_jobs = set()
        for job in jobs:
            if not isinstance(job, dict) or type(job.get('id')) is not int or job['id'] <= 0 or job['id'] in seen_jobs:
                raise EvidenceError('invalid_or_duplicate_job')
            seen_jobs.add(job['id'])
            if job.get('status') != 'queued': continue
            required = labels(job.get('labels'))
            if 'self-hosted' not in required: continue
            assigned = job.get('runner_id')
            if assigned is not None and (type(assigned) is not int or assigned < 0):
                raise EvidenceError('invalid_runner_assignment')
            created = when(run.get('created_at'))
            if (created - now).total_seconds() > 120: raise EvidenceError('future_run_clock')
            if assigned or (now - created).total_seconds() < stale_seconds:
                continue
            group_id, group_name = job.get('runner_group_id'), job.get('runner_group_name')
            if group_id is not None and (type(group_id) is not int or group_id <= 0):
                raise EvidenceError('invalid_runner_group')
            if group_name is not None and (not isinstance(group_name, str) or len(group_name) > 80):
                raise EvidenceError('invalid_runner_group')
            if group_id or group_name:
                counters['group_access_unverified'] += 1
                continue
            matching = [r for r in runners if required <= labels(r['labels'])]
            if not matching: counters['label_or_access_gap'] += 1
            elif any(r['status'] == 'online' and not r['busy'] for r in matching):
                counters['available_but_queued'] += 1
            elif any(r['status'] == 'online' and r['busy'] for r in matching):
                counters['busy'] += 1
            else: counters['offline'] += 1
    count = sum(counters.values())
    verdict = ('no_stale_unassigned_jobs' if count == 0 else
               'route_available_but_queued' if counters['available_but_queued'] else
               'runner_busy' if counters['busy'] else
               'runner_offline' if counters['offline'] and not (counters['label_or_access_gap'] or counters['group_access_unverified']) else
               'labels_or_access_unproven')
    return {'classification': verdict, 'stale_unassigned_jobs': count,
            'observed_runners': len(runners), 'by_cause': counters,
            'recovery_authorized': False, 'queue_cancel_authorized': False,
            'deploy_authorized': False, 'generation_verified': False}

class RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EvidenceError('github_api_redirect_denied')

def api(path, token):
    if not path.startswith('/repos/Zennay/zCloud/actions/'):
        raise EvidenceError('forbidden_endpoint')
    request = Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'User-Agent': 'zcloud-runner-route-triage/1',
        'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with build_opener(RefuseRedirect()).open(request, timeout=12) as response:
            if response.status != 200 or not response.headers.get('content-type', '').lower().startswith('application/json'):
                raise EvidenceError('untrusted_api_response')
            data = response.read(MAX_BODY + 1)
    except (OSError, TimeoutError) as exc:
        raise EvidenceError('api_unavailable_or_permission_denied') from exc
    if len(data) > MAX_BODY: raise EvidenceError('oversized_response')
    result = strict_json(data)
    if not isinstance(result, dict): raise EvidenceError('invalid_api_response')
    return result

def pages(path, field, token, limit=500):
    found = []
    for page in range(1, MAX_PAGES + 1):
        response = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}', token)
        batch = array(response.get(field), 'api_page')
        if len(batch) > 100: raise EvidenceError('oversized_api_page')
        found.extend(batch)
        if len(found) > limit: raise EvidenceError('incomplete_inventory')
        if len(batch) < 100:
            if type(response.get('total_count')) is int and response['total_count'] > len(found):
                raise EvidenceError('incomplete_inventory')
            return found
    raise EvidenceError('pagination_cap_reached')

def live(token):
    if not token: raise EvidenceError('missing_github_token')
    runners = pages(f'/repos/{REPO}/actions/runners', 'runners', token)
    runs = pages(f'/repos/{REPO}/actions/runs?status=queued', 'workflow_runs', token, 40)
    jobs = {}
    for run in runs:
        if not isinstance(run, dict) or type(run.get('id')) is not int:
            raise EvidenceError('invalid_run')
        jobs[str(run['id'])] = pages(f'/repos/{REPO}/actions/runs/{run["id"]}/jobs?filter=latest', 'jobs', token)
    return {'repository': REPO, 'captured_at': datetime.now(timezone.utc).isoformat(),
            'runners': runners, 'queued_runs': runs, 'jobs_by_run': jobs}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--fixture', help='local JSON (no network)')
    source.add_argument('--live', action='store_true', help='GET only; needs GITHUB_TOKEN with runner read permission')
    parser.add_argument('--stale-seconds', type=int, default=180)
    args = parser.parse_args(argv)
    if not 60 <= args.stale_seconds <= 3600: parser.error('stale-seconds 60..3600')
    try:
        if args.fixture:
            with open(args.fixture, 'rb') as fh: raw = fh.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY: raise EvidenceError('oversized_fixture')
            snapshot = strict_json(raw)
        else:
            snapshot = live(os.getenv('GITHUB_TOKEN', ''))
        result = classify(snapshot, datetime.now(timezone.utc), args.stale_seconds)
        result['evidence_mode'] = 'offline_fixture' if args.fixture else 'github_api_get_only'
        print(json.dumps(result, sort_keys=True))
        return 0 if result['classification'] == 'no_stale_unassigned_jobs' else 1
    except (EvidenceError, OSError) as exc:
        print(json.dumps({'classification': 'unknown_incomplete_evidence',
                          'reason': str(exc) if isinstance(exc, EvidenceError) else 'unreadable_fixture',
                          'recovery_authorized': False, 'queue_cancel_authorized': False,
                          'deploy_authorized': False}, sort_keys=True))
        return 2

if __name__ == '__main__': sys.exit(main())
