#!/usr/bin/env python3
"""Bounded exact GitHub issue pinning for zSentinel P0-first operator windows.

A pinned issue is an extra read-only source receipt, never an active worker
claim, release permission, retry request or proof of the underlying outage.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from zsentinel_product_issue_inbox import bounded_get, observed_time, priority

PIN = re.compile(r"^([a-z][a-z0-9_-]{0,39}):([1-9][0-9]{0,8})$")
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
MAX_PIN_READS = 3


def known_owner_gate_specs(registry, selected):
    """Auto-refresh only registered existing owner-held P0 issue identities.

    A known issue identity says where to read, not whether a runner is down,
    an account needs payment, or a worker may be claimed/restarted.
    """
    if (not isinstance(registry, dict) or not isinstance(selected, list)
            or len(selected) > 3 or len(selected) != len(set(selected))):
        raise ValueError("invalid auto pin project window")
    if "cloud" not in selected or registry.get("cloud") != "Zennay/zCloud":
        return []
    from zsentinel_problem_picker import OWNER_GATED_ISSUES
    found = []
    for url in sorted(OWNER_GATED_ISSUES):
        match = re.fullmatch(
            r"https://github[.]com/Zennay/zCloud/issues/([1-9][0-9]{0,8})", url)
        if match:
            found.append("cloud:" + match.group(1))
    if len(found) > MAX_PIN_READS:
        # Do not silently discard new critical owner gates.
        raise ValueError("too many known owner-only gates for bounded auto pin window")
    return found


def validate_pin_specs(registry, selected, pins):
    """Pure preflight: invalid operator input must fail before any GitHub GET."""
    if (not isinstance(registry, dict) or not isinstance(selected, list)
            or not 1 <= len(selected) <= 3
            or len(selected) != len(set(selected))
            or not isinstance(pins, list) or len(pins) > MAX_PIN_READS):
        raise ValueError("invalid bounded issue pin selector")
    numbered = []
    seen = set()
    for raw in pins:
        match = PIN.fullmatch(raw) if type(raw) is str else None
        if not match or match.group(1) not in selected:
            raise ValueError("pin requires PROJECT:ISSUE in selected GitHub window")
        pid, number = match.group(1), int(match.group(2))
        repo = registry.get(pid)
        if (type(repo) is not str or not REPO.fullmatch(repo)
                or number > 100000000 or (pid, number) in seen):
            raise ValueError("invalid or duplicate canonical issue pin")
        seen.add((pid, number))
        numbered.append((pid, number, repo))
    return numbered


def read_pinned_issues(inbox, registry, selected, pins, token=None):
    """Add up to three independently re-fetched open issues to one source inbox.

    The output never silently claims that an unsuccessful pin is closed:
    missing, denied or changed issue receipts stay visibly unavailable.
    A prior normal issue-window record may keep its already observed PR
    references, but does not convey ownership authority or queue leases.
    """
    if (not isinstance(inbox, dict)
            or inbox.get("schema") != "zsentinel-product-issue-inbox-v1"
            or inbox.get("actions_authorized") is not False
            or inbox.get("active_claims_verified") is not False
            or inbox.get("queue_ownership_known") is not False
            or not isinstance(inbox.get("items"), list)
            or len(inbox["items"]) > 150
            or not isinstance(inbox.get("coverage"), list)
            or not isinstance(registry, dict)
            or not isinstance(selected, list) or len(selected) > 3
            or not isinstance(pins, list) or len(pins) > MAX_PIN_READS):
        raise ValueError("untrusted bounded exact-issue source")
    numbered = validate_pin_specs(registry, selected, pins)
    if not numbered:
        return inbox
    existing = {
        (row.get("project"), row.get("number")): row
        for row in inbox["items"] if isinstance(row, dict)
    }
    items = list(inbox["items"])
    receipts = []
    requests = 0
    denied = inbox.get("stopped_after_rate_or_auth_denial") is True
    now = datetime.now(timezone.utc)
    for pid, number, repo in numbered:
        original = existing.get((pid, number))
        # An exact targeted observation supersedes an earlier partial list
        # entry. If the target is closed/denied/unavailable, an older sampled
        # "open" row must NOT survive as a fresh pinned blocker.
        items = [row for row in items if not (
            isinstance(row, dict) and row.get("project") == pid
            and row.get("number") == number)]
        if denied:
            receipts.append({"project_id": pid, "issue_number": number,
                             "coverage": "not_queried_after_auth_or_rate_limit",
                             "owner_verified": False, "actions_authorized": False})
            continue
        requests += 1
        url = "https://api.github.com/repos/" + repo + "/issues/" + str(number)
        canonical_url = "https://github.com/" + repo + "/issues/" + str(number)
        try:
            issue = bounded_get(url, token)
            if (not isinstance(issue, dict) or issue.get("number") != number
                    or issue.get("html_url") != canonical_url
                    or "pull_request" in issue
                    or issue.get("state") not in ("open", "closed")):
                raise ValueError("pinned_issue_identity_or_state_invalid")
            if issue["state"] == "closed":
                receipts.append({"project_id": pid, "issue_number": number,
                                 "coverage": "observed_closed_not_a_current_blocker",
                                 "owner_verified": False, "actions_authorized": False})
                continue
            title = issue.get("title")
            assignees = issue.get("assignees")
            if (type(title) is not str or not 1 <= len(title) <= 1000
                    or not isinstance(assignees, list)):
                raise ValueError("pinned_issue_fields_invalid")
            tier, qa_only = priority(issue)
            created = observed_time(issue.get("created_at"))
            updated = observed_time(issue.get("updated_at"))
            if created > now or updated > now or updated < created:
                raise ValueError("pinned_issue_time_invalid")
            replacement = {
                "id": repo + "#" + str(number),
                "project": pid, "repository": repo, "number": number,
                "title": title[:180], "priority": tier, "qa_only": qa_only,
                "has_assignee": bool(assignees), "active_claim": "not_verified",
                "related_open_prs": (list(original.get("related_open_prs", []))
                                     if isinstance(original, dict) else []),
                "created_at": created.isoformat(timespec="seconds"),
                "updated_at": updated.isoformat(timespec="seconds"),
                "url": canonical_url, "state": "open",
                "pinned_exact_issue_read": True,
            }
            items.append(replacement)
            receipts.append({"project_id": pid, "issue_number": number,
                             "coverage": ("observed_open_qa_only" if qa_only
                                          else "observed_open_exact_issue"),
                             "owner_verified": False, "actions_authorized": False})
        except (OSError, ValueError) as error:
            label = str(error)
            if label == "github_permission_or_rate_limit":
                denied = True
            receipts.append({"project_id": pid, "issue_number": number,
                             "coverage": ("unavailable_or_denied" if label ==
                                          "github_permission_or_rate_limit"
                                          else "unavailable_not_a_closed_proof"),
                             "owner_verified": False, "actions_authorized": False})
    if len(items) > 150:
        raise ValueError("bounded pinned inbox exceeded source limit")
    output = {**inbox,
              "items": items,
              "request_count": inbox["request_count"] + requests,
              "pinned_issue_receipts": receipts,
              "pin_observation_complete": all(row["coverage"].startswith("observed_")
                                              for row in receipts),
              "stopped_after_rate_or_auth_denial": denied,
              "fully_synchronized": False}
    return output


PIN_STATUS = {
    "observed_open_exact_issue": "EXACT OPEN: bronissue waargenomen; storing zelf niet bewezen",
    "observed_open_qa_only": "EXACT OPEN QA-only: niet aanbieden als productwerk",
    "observed_closed_not_a_current_blocker": "EXACT GESLOTEN: geen huidig open issue, runtimeherstel onbekend",
    "unavailable_or_denied": "BRONTOEGANG GEWEIGERD: status onbekend, geen vervolg-GET",
    "unavailable_not_a_closed_proof": "BRON ONBESCHIKBAAR: niet als gesloten tellen",
    "not_queried_after_auth_or_rate_limit": "NIET GERAADPLEEGD na auth/rate-limit: status onbekend",
}


def pinned_receipts(source):
    """Validate the optional UI source; never display untrusted API text."""
    if not isinstance(source, dict):
        return []
    rows = source.get("pinned_issue_receipts")
    if rows is None:
        return []
    if not isinstance(rows, list) or len(rows) > MAX_PIN_READS:
        raise ValueError("untrusted bounded pinned issue receipts")
    observed = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid pinned issue receipt")
        project = row.get("project_id")
        issue = row.get("issue_number")
        status = row.get("coverage")
        if (type(project) is not str
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", project)
                or type(issue) is not int or not 1 <= issue <= 100000000
                or status not in PIN_STATUS
                or row.get("owner_verified") is not False
                or row.get("actions_authorized") is not False):
            raise ValueError("invalid pinned issue source authority")
        observed.append((project, issue, PIN_STATUS[status]))
    return observed


def pinned_receipts_text(source):
    rows = pinned_receipts(source)
    if not rows:
        if isinstance(source, dict) and type(source.get("pinned_requested")) is int and 0 < source["pinned_requested"] <= MAX_PIN_READS:
            return "GERICHTE ISSUE-BRON: ONBESCHIKBAAR (geen exacte open/gesloten-status bewezen)."
        return ""
    return "\n".join(
        "GERICHT OPEN ISSUE " + pid + "#" + str(num) + ": " + status +
        " · geen workerclaim/retry/mergebevoegdheid"
        for pid, num, status in rows)


def pinned_receipts_html(source):
    rows = pinned_receipts(source)
    if not rows:
        if isinstance(source, dict) and type(source.get("pinned_requested")) is int and 0 < source["pinned_requested"] <= MAX_PIN_READS:
            return ('<section><h2>Gerichte issuebron</h2><p class="banner">'
                    'GEEN betrouwbare exacte GitHub-bron; niet als gesloten of hersteld tellen.'
                    '</p></section>')
        return ""
    from html import escape
    return ('<section aria-label="Gerichte GitHub-bronissues">'
            '<h2>Gerichte P0/P1-broncontroles (hoogstens drie)</h2>'
            '<p>Dit zijn uitsluitend exacte GitHub-issuebronnen, nooit'
            ' runnerherstel, workerlease, facturatie- of mergebevoegdheid.</p><ul>' +
            ''.join('<li>' + escape(pid) + '#' + str(num) + ': ' +
                    escape(status) + '</li>'
                    for pid, num, status in rows) + '</ul></section>')
