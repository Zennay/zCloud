#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Callable

ZONE = "cheapgpt.shop"


def login_shell_ok(script: str, *, timeout: int = 20) -> bool:
    try:
        completed = subprocess.run(
            ["bash", "-lc", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def probe(run: Callable[..., bool] = login_shell_ok) -> dict[str, object]:
    token_present = run(
        'test -n "${CLOUDFLARE_API_TOKEN:-${CF_API_TOKEN:-}}"',
        timeout=5,
    )
    zone_id_present = run('test -n "${CLOUDFLARE_ZONE_ID:-}"', timeout=5)

    token_verified = False
    zone_readable = False

    if token_present:
        token_verified = run(
            r'''
set -Eeuo pipefail
token="${CLOUDFLARE_API_TOKEN:-${CF_API_TOKEN:-}}"
auth_cfg="$(mktemp)"
verify_json="$(mktemp)"
cleanup() { rm -f "$auth_cfg" "$verify_json"; }
trap cleanup EXIT
chmod 600 "$auth_cfg"
printf 'header = "Authorization: Bearer %s"\n' "$token" > "$auth_cfg"
curl --fail --silent --show-error --config "$auth_cfg" \
  https://api.cloudflare.com/client/v4/user/tokens/verify > "$verify_json"
python3 -c 'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); r=p.get("result") or {}; raise SystemExit(0 if p.get("success") is True and r.get("status") == "active" else 1)' "$verify_json"
''',
            timeout=20,
        )

        if token_verified:
            zone_readable = run(
                r'''
set -Eeuo pipefail
token="${CLOUDFLARE_API_TOKEN:-${CF_API_TOKEN:-}}"
auth_cfg="$(mktemp)"
zone_json="$(mktemp)"
cleanup() { rm -f "$auth_cfg" "$zone_json"; }
trap cleanup EXIT
chmod 600 "$auth_cfg"
printf 'header = "Authorization: Bearer %s"\n' "$token" > "$auth_cfg"
curl --fail --silent --show-error --config "$auth_cfg" \
  "https://api.cloudflare.com/client/v4/zones?name=cheapgpt.shop&status=active" > "$zone_json"
python3 -c 'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); r=p.get("result") or []; raise SystemExit(0 if p.get("success") is True and len(r) == 1 and r[0].get("name") == "cheapgpt.shop" else 1)' "$zone_json"
''',
                timeout=20,
            )

    wrangler_present = run("command -v wrangler >/dev/null 2>&1", timeout=5)
    wrangler_authenticated = (
        wrangler_present and run("wrangler whoami >/dev/null 2>&1", timeout=20)
    )
    cloudflared_present = run("command -v cloudflared >/dev/null 2>&1", timeout=5)

    result: dict[str, object] = {
        "schema_version": 1,
        "project": "zssh",
        "probe": "persistent-vps-cloudflare-capability",
        "runner_name": os.environ.get("RUNNER_NAME", ""),
        "hostname": socket.gethostname().split(".", 1)[0],
        "zone": ZONE,
        "persistent_token_present": token_present,
        "persistent_zone_id_present": zone_id_present,
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
    result = probe()
    print(json.dumps(result, sort_keys=True, indent=2))
    if result["token_verified"] and result["zone_readable"]:
        print(f"ZSSH_VPS_CLOUDFLARE_API_SESSION_PRESENT zone={ZONE}")
    elif result["wrangler_authenticated"]:
        print(f"ZSSH_VPS_CLOUDFLARE_WRANGLER_SESSION_PRESENT zone={ZONE}")
    else:
        print(f"ZSSH_VPS_CLOUDFLARE_SESSION_ABSENT zone={ZONE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
