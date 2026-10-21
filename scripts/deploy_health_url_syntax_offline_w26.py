"""Offline-only deploy health URL syntax screen; never performs I/O or authorizes recovery.

This screen is deliberately narrower than a network trust decision: DNS, redirects,
TLS, response freshness and authenticated environment identity require independent
checks by the serialized deployment owner. No URLs are fetched here.
"""
from urllib.parse import urlsplit
import ipaddress

def screen_health_url(value: object, allowed_hosts: frozenset[str]) -> bool:
    """Accept only canonical HTTPS /health URLs at explicitly configured DNS names."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        return False
    if not isinstance(allowed_hosts, frozenset) or not allowed_hosts:
        return False
    if any(not isinstance(h, str) or not h or h != h.lower() for h in allowed_hosts):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != "https" or not host or host != host.lower()
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443)
                or parts.query or parts.fragment
                or parts.path != "/health"
                or parts.netloc != host and parts.netloc != host + ":443"):
            return False
        if host not in allowed_hosts or host.endswith(".") or ".." in host:
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return all(label.isascii() and label and label[0].isalnum()
                   and label[-1].isalnum() and all(ch.isalnum() or ch == "-"
                   for ch in label) for label in host.split("."))
    except (ValueError, TypeError):
        return False

def live_recovery_authorized(*_args: object, **_kwargs: object) -> bool:
    """Offline syntax evidence is never permission to touch the VPS."""
    return False
