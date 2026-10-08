#!/usr/bin/env python3
"""Fail-closed, offline GitHub Actions artifact URL boundary inspection.

Advisory only: this never authorizes release or touches remote resources.
"""
import argparse
import ipaddress
import json
from urllib.parse import urlsplit

TRUSTED_HOSTS = frozenset({"github.com", "api.github.com"})
DENY = {"deploy_authorized": False, "recovery_authorized": False, "mutation_performed": False}

def inspect(url: object) -> dict:
    reasons = []
    if not isinstance(url, str) or not url or len(url) > 2048:
        reasons.append("invalid_url")
    else:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if parsed.scheme != "https":
                reasons.append("https_required")
            if not host or host.lower() not in TRUSTED_HOSTS:
                reasons.append("untrusted_host")
            if parsed.username is not None or parsed.password is not None:
                reasons.append("userinfo_forbidden")
            if parsed.port not in (None, 443):
                reasons.append("nonstandard_port")
            if parsed.fragment:
                reasons.append("fragment_forbidden")
            if "\\" in url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
                reasons.append("unsafe_character")
            if host:
                try:
                    ipaddress.ip_address(host)
                    reasons.append("ip_literal_forbidden")
                except ValueError:
                    pass
            if parsed.path.startswith("//"):
                reasons.append("ambiguous_path")
        except ValueError:
            reasons.append("malformed_url")
    return dict(DENY, status="review_only" if not reasons else "deny",
                reasons=sorted(set(reasons)))

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    a = p.parse_args()
    result = inspect(a.url)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "review_only" else 2

if __name__ == "__main__":
    raise SystemExit(main())
