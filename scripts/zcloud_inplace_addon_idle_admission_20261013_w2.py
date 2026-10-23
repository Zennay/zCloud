#!/usr/bin/env python3
"""Read-only, deny-first admission for a manually approved in-place addon reload.

A positive local observation is not production-release authority. The serialized
operator must separately approve the maintenance window; a later worker can
start generating after this snapshot, so the runtime guard must run just before
the privileged reload.
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765/api/runner-live"
LIMIT = 65536
MAX_ACTIVE = 128


class Denied(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Denied("ambiguous JSON keys")
        result[key] = value
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Denied("redirect not allowed")


def classify(data):
    if not isinstance(data, dict):
        raise Denied("status response not a map")
    runners = data.get("chatgpt_runners")
    if not isinstance(runners, dict) or not runners:
        raise Denied("runner inventory missing")
    active = set()
    for key, runner in runners.items():
        if not isinstance(key, str) or not key or not isinstance(runner, dict):
            raise Denied("invalid runner")
        workers = runner.get("workers")
        if not isinstance(workers, list):
            raise Denied("worker inventory incomplete")
        for worker in workers:
            if not isinstance(worker, dict) or type(worker.get("active")) is not bool:
                raise Denied("worker active flag absent")
            if not worker["active"]:
                continue
            worker_id = worker.get("worker_id")
            if (not isinstance(worker_id, str) or not worker_id
                    or len(worker_id) > 128 or "::w" not in worker_id):
                raise Denied("active worker identity malformed")
            prefix, slot = worker_id.rsplit("::w", 1)
            if not prefix or not slot.isdigit() or slot.startswith("0"):
                raise Denied("active worker slot malformed")
            if worker_id in active:
                raise Denied("duplicate active worker")
            active.add(worker_id)
            if len(active) > MAX_ACTIVE:
                raise Denied("worker inventory exceeds bound")
            for flag in ("sending", "generating"):
                if type(worker.get(flag)) is not bool or worker[flag]:
                    raise Denied("worker busy or flag unknown")
            if worker.get("desired_state") not in ("running", "active", "idle"):
                raise Denied("worker draining or state unavailable")
    if not active:
        raise Denied("no active workers to protect")
    return tuple(sorted(active))


def read_status(opener=None):
    opener = opener or urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(URL, headers={
        "Accept": "application/json", "User-Agent": "zcloud-inplace-addon-admission"
    })
    with opener.open(req, timeout=5) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise Denied("untrusted status response")
        data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise Denied("status too large")
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    return classify(parsed)


def check_stable(read=read_status, sleep=time.sleep):
    previous = read()
    sleep(3)
    current = read()
    if previous != current:
        raise Denied("worker set changed")
    return len(current)


def main():
    try:
        count = check_stable()
    except (Denied, ValueError, OSError, TimeoutError, TypeError, UnicodeError) as exc:
        print(json.dumps({
            "status": "deny", "reason_class": type(exc).__name__,
            "mutation_performed": False, "deploy_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "status": "idle-observed", "active_workers": count,
        "mutation_performed": False, "deploy_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
