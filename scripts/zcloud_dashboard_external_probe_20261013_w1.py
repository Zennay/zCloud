#!/usr/bin/env python3
"""Read-only, bounded external zCloud dashboard health classification (#1166).

This is an independent observability candidate; it does not replace or activate
the quarantined production recovery workflow and cannot authorize a restart.
Output includes only category and attempt counts, never response data or URLs.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

# Deliberately fixed. Arbitrary user-provided URLs or proxy routing are forbidden.
STATUS_URL = "http://198.244.191.182:8765/api/status"
MAX_BYTES = 128 * 1024
MAX_DEPTH = 32
MAX_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 5
TIMEOUT_SECONDS = 12
CATEGORIES = frozenset({
    "healthy", "dns", "connection", "timeout", "tls", "http_status",
    "http_redirect", "http_client_error", "http_server_error",
    "content_type", "body_too_large", "invalid_json", "invalid_schema",
    "transport_failure", "stale_timestamp", "future_timestamp", "invalid_headers",
})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _object_pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate key")
        output[key] = value
    return output


def _reject_constant(_value):
    raise ValueError("nonfinite JSON")


def _validate_tree(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("JSON nested too deeply")
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("surrogate codepoint")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON numeric overflow")
    elif isinstance(value, list):
        for item in value:
            _validate_tree(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_tree(key, depth + 1)
            _validate_tree(item, depth + 1)


def _validate_payload(raw, *, now=None):
    try:
        doc = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
        _validate_tree(doc)
    except (UnicodeError, ValueError, RecursionError):
        return "invalid_json"
    if not isinstance(doc, dict):
        return "invalid_schema"
    stamp, projects = doc.get("time"), doc.get("projects")
    if not isinstance(stamp, str) or len(stamp) > 80 or not isinstance(projects, list):
        return "invalid_schema"
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "invalid_schema"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "invalid_schema"
    reference = datetime.now(timezone.utc) if now is None else now
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("reference time must be timezone-aware")
    age = (reference - parsed).total_seconds()
    if age < -30:
        return "future_timestamp"
    if age > 180:
        return "stale_timestamp"
    return "healthy"


def _http_status_category(status):
    """Coarse, privacy-safe status group; never record error URLs or bodies."""
    if type(status) is not int:
        return "http_status"
    if 300 <= status < 400:
        return "http_redirect"
    if 400 <= status < 500:
        return "http_client_error"
    if 500 <= status < 600:
        return "http_server_error"
    return "http_status"


def _exception_category(exc):
    """Sanitize transport faults even if their cause chain is cyclic or corrupt."""
    visited = set()
    # HTTPS stacks can wrap errno in several URLError layers. Never recurse
    # through untrusted .reason indefinitely and NEVER stringify exceptions.
    for _ in range(8):
        if isinstance(exc, urllib.error.HTTPError):
            return _http_status_category(exc.code)
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(exc, ssl.SSLError):
            return "tls"
        if isinstance(exc, socket.gaierror):
            return "dns"
        if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
            return "connection"
        if isinstance(exc, urllib.error.URLError):
            if id(exc) in visited:
                return "transport_failure"
            visited.add(id(exc))
            exc = exc.reason
            continue
        if isinstance(exc, OSError):
            return "connection"
        return "transport_failure"
    return "transport_failure"


def probe_once(opener=None):
    """Single non-mutating GET. The injectable opener is for offline fixtures."""
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        STATUS_URL, method="GET", headers={
            "Accept": "application/json",
            # Don't ask HTTP caches to replay an old successful status.
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if type(response.status) is not int or response.status != 200:
                return _http_status_category(response.status)
            headers = response.headers
            # HTTPMessage.get_all exposes duplicate / conflicting header lines.
            # A dictionary test fixture cannot contain duplicate physical lines.
            if hasattr(headers, "get_all"):
                for header in ("Content-Type", "Content-Length", "Content-Encoding", "Transfer-Encoding"):
                    if len(headers.get_all(header, [])) > 1:
                        return "invalid_headers"
            # urllib/http.client de-chunks some Transfer-Encoding variants. A
            # framing/coding header here is not an attested representation of
            # the status body, especially when paired with Content-Length.
            # Prefer a false negative to claiming an ambiguous HTTP frame green.
            if headers.get("Transfer-Encoding") is not None:
                return "invalid_headers"
            # A conflicting or exotic charset cannot safely describe the
            # UTF-8 JSON bytes we parse. Reject duplicate/malformed params,
            # controls and opaque non-ASCII metadata instead of ignoring them.
            media_header = headers.get("Content-Type", "")
            if (not isinstance(media_header, str) or
                    not media_header.isascii() or len(media_header) > 120 or
                    any(ord(char) < 32 or ord(char) == 127 for char in media_header)):
                return "content_type"
            media_parts = media_header.split(";")
            if (media_parts[0].strip().lower() != "application/json" or
                    len(media_parts) > 2 or
                    (len(media_parts) == 2 and media_parts[1].strip().lower()
                     not in ('charset=utf-8', 'charset="utf-8"'))):
                return "content_type"
            # urllib does not transparently decompress bodies. Never accept a
            # potentially misleading compressed/encoded status representation.
            if headers.get("Content-Encoding", "").strip().lower() not in ("", "identity"):
                return "invalid_headers"
            length_text = headers.get("Content-Length")
            declared_length = None
            if length_text is not None:
                # Reject comma-folded headers, signs, Unicode digits, and
                # absurdly long strings before attempting int conversion.
                if (not isinstance(length_text, str) or
                        not length_text.isascii() or not length_text.isdigit() or
                        len(length_text) > 12):
                    return "invalid_headers"
                declared_length = int(length_text)
                if declared_length > MAX_BYTES:
                    return "body_too_large"
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return "body_too_large"
            if declared_length is not None and declared_length != len(raw):
                return "invalid_headers"
            return _validate_payload(raw)
    except Exception as exc:  # Never surface network exception text or response body.
        return _exception_category(exc)


@dataclass(frozen=True)
class ProbeSummary:
    status: str
    last_failure_category: str | None
    attempts: int
    recovered_after_retry: bool
    failure_counts: dict[str, int]
    probe_only: bool = True
    authorized: bool = False
    mutations_performed: bool = False
    # The pinned external endpoint is plain HTTP: no authenticated origin proof.
    transport_authenticated: bool = False


def run_probe(probe=probe_once, sleep=time.sleep, attempts=MAX_ATTEMPTS):
    if type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS:
        raise ValueError("attempt budget must be an integer from 1 to 6")
    failures = collections.Counter()
    for index in range(attempts):
        try:
            category = probe()
        except Exception:
            # An unexpected opener/test fixture failure is never a reason to
            # print the exception (which may contain a URL or credentials).
            # Preserve remaining retry budget without granting authority.
            category = "transport_failure"
        if type(category) is not str or category not in CATEGORIES:
            category = "transport_failure"
        if category == "healthy":
            return ProbeSummary(
                "healthy", None, index + 1, index > 0, dict(sorted(failures.items()))
            )
        failures[category] += 1
        if index + 1 < attempts:
            sleep(RETRY_DELAY_SECONDS)
    return ProbeSummary(
        "unhealthy", category, attempts, False, dict(sorted(failures.items()))
    )


def operator_markdown(summary: ProbeSummary) -> str:
    """Human-readable GitHub Actions summary; only fixed category names can appear."""
    labels = {
        "dns": "DNS / naamoplossing",
        "connection": "Verbinding geweigerd of verbroken",
        "timeout": "Time-out",
        "tls": "TLS-verbinding",
        "http_status": "HTTP-status niet 200",
        "http_redirect": "Onverwachte HTTP-doorverwijzing",
        "http_client_error": "HTTP-aanvraag geweigerd (4xx)",
        "http_server_error": "HTTP-serverfout (5xx)",
        "invalid_headers": "Tegenstrijdige HTTP-headers",
        "content_type": "Onverwacht antwoordtype",
        "body_too_large": "Antwoord te groot",
        "invalid_json": "Ongeldige JSON",
        "invalid_schema": "API-antwoord heeft verkeerde structuur",
        "transport_failure": "Onbekende transportfout",
        "stale_timestamp": "Verouderd statusantwoord",
        "future_timestamp": "Statusantwoord ligt in de toekomst",
    }
    is_healthy = summary.status == "healthy"
    lines = [
        "## zCloud · externe dashboardcontrole",
        "",
        f"**Status:** {'API bereikbaar' if is_healthy else 'API niet aantoonbaar bereikbaar'}",
        f"**Controlepogingen:** {summary.attempts}",
        f"**Hersteld na hercontrole:** {'ja' if summary.recovered_after_retry else 'nee'}",
    ]
    if summary.failure_counts:
        lines.extend(["", "**Waargenomen storingen (aantallen):**"])
        for category, count in sorted(summary.failure_counts.items()):
            lines.append(f"- {labels[category]}: {count}")
    lines.extend([
        "",
        "**Let op:** deze controle bewijst niet dat de volledige productie-deploy "
        "gezond is of dat AI-workers tekst genereren.",
        "**Bronvertrouwen:** publiek HTTP zonder TLS; de antwoordherkomst is "
        "niet cryptografisch bevestigd.",
        "**Herstel of herstart wordt nooit automatisch uitgevoerd.**",
    ])
    return chr(10).join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    options = parser.parse_args(argv)
    try:
        summary = run_probe(attempts=options.attempts)
    except ValueError:
        print(json.dumps({
            "status": "invalid_configuration", "authorized": False,
            "mutations_performed": False, "probe_only": True,
            "transport_authenticated": False,
        }, sort_keys=True))
        return 2
    print(operator_markdown(summary) if options.format == "markdown"
          else json.dumps(asdict(summary), sort_keys=True))
    return 0 if summary.status == "healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
