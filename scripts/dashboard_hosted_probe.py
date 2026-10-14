#!/usr/bin/env python3
"""Read-only hosted dashboard probe. No recovery privileges or side effects.

The probe intentionally never prints a response body, hostname, or exception text.
It is not wired to any workflow until the #1143 owner explicitly hands off.
"""
import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 65536
ALLOWED_ATTEMPTS = 6
REQUIRED_KEYS = ("time", "projects")


def check_once(url: str, timeout: float = 5.0, opener=None) -> str:
    """Return 'ok' or a bounded diagnostic code; never raise network errors."""
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            code = response.getcode()
            if code is None or not 200 <= code < 300:
                return "http_error"
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return "response_too_large"
    except urllib.error.HTTPError:
        return "http_error"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(reason, socket.gaierror):
            return "dns_error"
        if isinstance(reason, ConnectionRefusedError):
            return "connection_error"
        return "network_error"
    except (TimeoutError, socket.timeout):
        return "timeout"
    except (ConnectionError, OSError):
        return "network_error"

    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "invalid_json"
    if not isinstance(data, dict) or any(key not in data for key in REQUIRED_KEYS):
        return "schema_error"
    if not isinstance(data["time"], str) or not data["time"].strip():
        return "schema_error"
    if not isinstance(data["projects"], list):
        return "schema_error"
    return "ok"


def probe(url: str, attempts: int = ALLOWED_ATTEMPTS, delay: float = 2.0, checker=check_once, sleeper=time.sleep):
    if not 1 <= attempts <= ALLOWED_ATTEMPTS:
        raise ValueError("attempts out of bounds")
    if not 0 <= delay <= 30:
        raise ValueError("delay out of bounds")
    reasons = []
    for index in range(attempts):
        result = checker(url)
        if result == "ok":
            return {"status": "healthy", "attempts": index + 1, "transient_failures": index}
        reasons.append(result)
        if index + 1 < attempts:
            sleeper(delay)
    return {"status": "unhealthy", "attempts": attempts, "reason": reasons[-1], "failures": reasons}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="dashboard API status URL (not printed)")
    parser.add_argument("--attempts", type=int, default=ALLOWED_ATTEMPTS)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--delay", type=float, default=2)
    args = parser.parse_args(argv)
    if not args.url.startswith(("http://", "https://")):
        parser.error("URL must use HTTP(S)")
    if not 0 < args.timeout <= 30:
        parser.error("timeout must be between 0 and 30 seconds")
    try:
        result = probe(args.url, args.attempts, args.delay,
                       checker=lambda u: check_once(u, args.timeout))
    except ValueError as exc:
        parser.error(str(exc))
    print("ZCLOUD_DASHBOARD_PROBE " + json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
