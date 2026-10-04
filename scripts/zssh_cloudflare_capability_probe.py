#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ZONE_NAME = "cheapgpt.shop"
TOKEN_VERIFY_URL = "https://api.cloudflare.com/client/v4/user/tokens/verify"
ZONE_LOOKUP_URL = "https://api.cloudflare.com/client/v4/zones"

_TOKEN_SCRIPT = r"""
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
  printf "%s" "$CLOUDFLARE_API_TOKEN"
elif [ -n "${CF_API_TOKEN:-}" ]; then
  printf "%s" "$CF_API_TOKEN"
fi
"""

_ZONE_ID_SCRIPT = r"""
if [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
  printf "%s" "$CLOUDFLARE_ZONE_ID"
fi
"""


def _login_value(script: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            ["bash", "-lc", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _login_ok(command: str, timeout: int = 10) -> bool:
    try:
        result = subprocess.run(
            ["bash", "-lc", command],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _cloudflare_get(url: str, token: str, timeout: int = 10) -> dict[str, Any] | None:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "zcloud-zssh-capability-probe/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        ValueError,
        urllib.error.HTTPError,
        urllib.error.URLError,
    ):
        return None
    return payload if isinstance(payload, dict) else None


def build_probe() -> dict[str, Any]:
    token = _login_value(_TOKEN_SCRIPT)
    zone_id = _login_value(_ZONE_ID_SCRIPT)

    token_verified = False
    zone_readable = False

    if token:
        verification = _cloudflare_get(TOKEN_VERIFY_URL, token)
        if verification and verification.get("success") is True:
            result = verification.get("result") or {}
            token_verified = isinstance(result, dict) and result.get("status") == "active"

        if token_verified:
            query = urllib.parse.urlencode({"name": ZONE_NAME, "status": "active"})
            zone_payload = _cloudflare_get(f"{ZONE_LOOKUP_URL}?{query}", token)
            if zone_payload and zone_payload.get("success") is True:
                result = zone_payload.get("result") or []
                zone_readable = (
                    isinstance(result, list)
                    and len(result) == 1
                    and isinstance(result[0], dict)
                    and result[0].get("name") == ZONE_NAME
                )

    wrangler_present = _login_ok("command -v wrangler >/dev/null 2>&1")
    wrangler_authenticated = wrangler_present and _login_ok(
        "wrangler whoami >/dev/null 2>&1",
        timeout=20,
    )
    cloudflared_present = _login_ok("command -v cloudflared >/dev/null 2>&1")

    result = {
        "schema_version": 1,
        "project": "zssh",
        "probe": "persistent-vps-cloudflare-capability",
        "hostname": socket.gethostname().split(".", 1)[0],
        "zone": ZONE_NAME,
        "persistent_token_present": bool(token),
        "persistent_zone_id_present": bool(zone_id),
        "token_verified": token_verified,
        "zone_readable": zone_readable,
        "wrangler_present": wrangler_present,
        "wrangler_authenticated": wrangler_authenticated,
        "cloudflared_present": cloudflared_present,
        "mutation_attempted": False,
    }
    result["persistent_provider_session_present"] = (
        token_verified and zone_readable
    ) or wrangler_authenticated
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = build_probe()
    output = Path(args.output)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    output.chmod(0o600)

    print(json.dumps(result, sort_keys=True, indent=2))
    if result["token_verified"] and result["zone_readable"]:
        print(f"ZSSH_VPS_CLOUDFLARE_API_SESSION_PRESENT zone={ZONE_NAME}")
    elif result["wrangler_authenticated"]:
        print(f"ZSSH_VPS_CLOUDFLARE_WRANGLER_SESSION_PRESENT zone={ZONE_NAME}")
    else:
        print(f"ZSSH_VPS_CLOUDFLARE_SESSION_ABSENT zone={ZONE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
