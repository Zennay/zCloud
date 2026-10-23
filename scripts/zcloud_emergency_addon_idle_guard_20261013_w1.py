#!/usr/bin/env python3
"""Fail-closed read-only idle observation before an explicitly approved addon reload.

This command never sends worker commands, obtains mutation leases, or reloads a browser.
It is an advisory snapshot only; it cannot grant production deploy authority.
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765/api/runner-live"
MAX_RESPONSE_BYTES = 65536


class AdmissionDenied(ValueError):
    pass


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise AdmissionDenied("duplicate JSON evidence key")
        obj[key] = value
    return obj


def safe_worker_ids(payload):
    if not isinstance(payload, dict):
        raise AdmissionDenied("observation must be a mapping")
    runners = payload.get("chatgpt_runners")
    if not isinstance(runners, dict) or not runners:
        raise AdmissionDenied("runner inventory unavailable")
    identities = set()
    for runner_id, runner in runners.items():
        if not isinstance(runner_id, str) or not runner_id or not isinstance(runner, dict):
            raise AdmissionDenied("invalid runner identity or value")
        workers = runner.get("workers")
        if not isinstance(workers, list):
            raise AdmissionDenied("worker observations missing")
        for worker in workers:
            if not isinstance(worker, dict) or type(worker.get("active")) is not bool:
                raise AdmissionDenied("ambiguous worker observation")
            if not worker["active"]:
                continue
            ident = worker.get("worker_id")
            if not isinstance(ident, str) or not ident.strip() or len(ident) > 128:
                raise AdmissionDenied("active worker identity unavailable")
            if ident in identities:
                raise AdmissionDenied("duplicate active worker identity")
            identities.add(ident)
            # Missing, string-valued or stale flags are NOT evidence of idleness.
            for key in ("sending", "generating"):
                if type(worker.get(key)) is not bool or worker[key]:
                    raise AdmissionDenied("worker potentially busy or unknown")
            if worker.get("desired_state") not in ("running", "active", "idle"):
                raise AdmissionDenied("worker desired state unknown or draining")
    if not identities:
        raise AdmissionDenied("no known active worker to protect")
    return tuple(sorted(identities))


class DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise AdmissionDenied("redirect forbidden for local status evidence")


def read_snapshot(opener=None):
    opener = opener or urllib.request.build_opener(DenyRedirect)
    request = urllib.request.Request(
        URL, headers={"Accept": "application/json", "User-Agent": "zcloud-addon-idle-preflight"}
    )
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise AdmissionDenied("status endpoint not healthy")
        if response.headers.get_content_type() != "application/json":
            raise AdmissionDenied("non-JSON status evidence")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise AdmissionDenied("status evidence too large")
        data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    return safe_worker_ids(data)


def verify_two_snapshots(read=read_snapshot, sleep=time.sleep):
    first = read()
    sleep(3)
    second = read()
    if first != second:
        raise AdmissionDenied("worker roster changed during idle observation")
    return len(first)


def main():
    try:
        count = verify_two_snapshots()
    except (AdmissionDenied, OSError, ValueError, TypeError, TimeoutError, UnicodeError) as exc:
        print(json.dumps({
            "admitted": False, "reason": type(exc).__name__,
            "production_authorized": False, "mutation_performed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "admitted": True, "active_workers_proven_idle": count,
        "production_authorized": False, "mutation_performed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
