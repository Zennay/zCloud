#!/usr/bin/env python3
"""Bounded, read-only HTTP dashboard probe; never authorizes recovery."""
import argparse
import json
import socket
import time
import urllib.error
import urllib.request


def classify(url, timeout=8, opener=None):
    """Return a stable category without logging endpoints, bodies or internals."""
    opener = opener or urllib.request.urlopen
    try:
        with opener(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            status = response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                return "http_status"
            raw = response.read(1048577)
        if len(raw) > 1048576:
            return "oversized_response"
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return "invalid_json"
        if not isinstance(payload, dict) or not payload.get("time") or not isinstance(payload.get("projects"), list):
            return "invalid_schema"
        return "ok"
    except urllib.error.HTTPError:
        return "http_status"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(reason, socket.gaierror):
            return "dns"
        return "connect_or_tls"
    except (TimeoutError, socket.timeout):
        return "timeout"
    except (OSError, ValueError):
        return "connect_or_tls"


def probe(url, attempts=6, delay=5, sleeper=time.sleep, checker=classify):
    if not 1 <= attempts <= 6:
        raise ValueError("attempts must be between 1 and 6")
    categories = []
    for index in range(attempts):
        category = checker(url)
        categories.append(category)
        if category == "ok":
            return {"result": "healthy", "attempts": index + 1,
                    "recovered_after_retry": index > 0, "categories": categories}
        if index + 1 < attempts:
            sleeper(delay)
    return {"result": "persistent_failure", "attempts": attempts,
            "recovered_after_retry": False, "categories": categories}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=5)
    args = parser.parse_args()
    if not 0 <= args.delay <= 30:
        parser.error("delay out of bounds")
    try:
        result = probe(args.url, attempts=args.attempts, delay=args.delay)
    except ValueError as exc:
        parser.error(str(exc))
    print("ZCLOUD_DASHBOARD_PROBE " + json.dumps(result, sort_keys=True))
    return 0 if result["result"] == "healthy" else 23


if __name__ == "__main__":
    raise SystemExit(main())
