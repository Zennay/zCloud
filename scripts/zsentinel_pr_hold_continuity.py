#!/usr/bin/env python3
"""Source-bound continuity for two private, bounded existing-PR owner snapshots.

A repeated exact-head/next-action signal is NOT a proof that no other PR
changed, nor permission to merge. Missing PRs are NOT merged or closed.
No provider reads, queue access, claim, GitHub write, or background action.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zsentinel_pr_delivery import NEXT_ACTION_NL

SCHEMA = "zsentinel-private-pr-gate-changes-v1"
PR_URL = re.compile(r"^https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,8}$")
HEAD = re.compile(r"^[a-f0-9]{40}$")
FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
EXACT = "bounded_exact_head_review_and_checks"
COVERAGE = frozenset((EXACT, "source_incomplete_or_changed",
                      "not_queried_after_auth_or_rate_limit"))
MAX_ROWS = 3


def _clock(value):
    if type(value) is not str or not 10 <= len(value) <= 45:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _rows(source):
    if (type(source) is not dict or
            source.get("schema") != "zsentinel-pr-delivery-desk-v1" or
            source.get("source") != "github_canonical_readonly_bounded_pr_gets" or
            any(source.get(k) is not False for k in
                ("merge_authorized", "deploy_authorized", "owner_verified",
                 "writes_performed"))):
        raise ValueError("invalid prior/current existing PR source")
    records = source.get("items")
    if type(records) is not list or len(records) > MAX_ROWS:
        raise ValueError("unbounded private existing PR snapshot")
    rows = {}
    for row in records:
        if (type(row) is not dict or
                row.get("source") != "github_exact_pr_readonly" or
                any(row.get(k) is not False for k in
                    ("merge_authorized", "deploy_authorized", "owner_verified",
                     "action_authorized"))):
            raise ValueError("untrusted exact-PR sample")
        url = row.get("url")
        coverage = row.get("coverage")
        head = row.get("head_sha")
        action = row.get("next_step")
        if (type(url) is not str or not PR_URL.fullmatch(url) or
                url in rows or coverage not in COVERAGE or
                action not in NEXT_ACTION_NL):
            raise ValueError("invalid or duplicate source PR URL/coverage")
        if coverage == EXACT:
            if type(head) is not str or not HEAD.fullmatch(head):
                raise ValueError("complete exact-PR coverage lacks head")
        elif head is not None:
            raise ValueError("incomplete PR source cannot carry current head")
        rows[url] = {"url": url, "head": head, "step": action,
                     "coverage": coverage}
    return _clock(source.get("observed_at_utc")), rows


def compare(current, previous, previous_sha256, now=None):
    """Summarize only observed gate changes in two private PR report windows."""
    if type(previous_sha256) is not str or not FINGERPRINT.fullmatch(previous_sha256):
        raise ValueError("untrusted private comparison file fingerprint")
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("invalid comparison clock")
    now = now.astimezone(timezone.utc)
    current_at, curr = _rows(current)
    previous_at, prior = _rows(previous)
    # A missing/denied GitHub source is not a fresh observation. Show a
    # bounded UNKNOWN state; never assert unchanged PRs or merge completion.
    comparable_clock = (
        current_at is not None and previous_at is not None and
        previous_at < current_at <= now + timedelta(minutes=2) and
        previous_at >= now - timedelta(days=30)
    )
    changes = []
    for url, item in curr.items():
        old = prior.get(url)
        if not comparable_clock:
            state = "incomparable_source_times_NO_GATE_DELTA"
        elif old is None:
            state = "newly_in_bounded_sample_NOT_NEW_PR"
        elif (item["coverage"] != EXACT or old["coverage"] != EXACT):
            state = "partial_exact_source_NO_GATE_DELTA"
        elif item["head"] != old["head"]:
            state = "exact_head_changed_REINSPECT_GATE"
        elif item["step"] != old["step"]:
            state = "same_head_owner_step_changed_REVIEW_GATE"
        else:
            state = "same_observed_head_and_owner_step_NOT_ALL_CLEAR"
        changes.append({
            "pr_url": url,
            "state": state,
            "observed_head_sha": item["head"],
            "previous_head_sha": old["head"] if old else None,
            "observed_next_step": item["step"],
            "previous_next_step": old["step"] if old else None,
            "gate_changed_proven": state in (
                "exact_head_changed_REINSPECT_GATE",
                "same_head_owner_step_changed_REVIEW_GATE"),
            "merge_verified": False, "owner_verified": False,
        })
    rank = {"exact_head_changed_REINSPECT_GATE": 0,
            "same_head_owner_step_changed_REVIEW_GATE": 1,
            "partial_exact_source_NO_GATE_DELTA": 2,
            "incomparable_source_times_NO_GATE_DELTA": 2,
            "newly_in_bounded_sample_NOT_NEW_PR": 3,
            "same_observed_head_and_owner_step_NOT_ALL_CLEAR": 4}
    changes.sort(key=lambda x: (rank[x["state"]], x["pr_url"]))
    previous_missing = sorted(set(prior) - set(curr))
    return {
        "schema": SCHEMA,
        "previous_sha256": previous_sha256,
        "previous_source_observed_at_utc":
            previous_at.isoformat() if previous_at else None,
        "current_source_observed_at_utc":
            current_at.isoformat() if current_at else None,
        "source_clocks_comparable": comparable_clock,
        "sampled_current_prs": len(curr),
        "sampled_previous_prs": len(prior),
        "observed_changed_gate_count": sum(x["gate_changed_proven"] for x in changes),
        "same_head_and_owner_step_observed_count": sum(
            x["state"] == "same_observed_head_and_owner_step_NOT_ALL_CLEAR"
            for x in changes),
        "current_prs": changes,
        "previously_sampled_not_current_NOT_MERGED": previous_missing,
        "complete_pr_inventory_verified": False,
        "unchanged_blocker_verified": False,
        "merge_verified": False,
        "owner_verified": False,
        "actions_performed": False,
    }


def text_summary(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("merge_verified") is not False
            or report.get("actions_performed") is not False
            or type(report.get("current_prs")) is not list
            or len(report["current_prs"]) > MAX_ROWS):
        raise ValueError("untrusted PR continuity report")
    lines = ["Bestaande PR’s · wat is veranderd sinds privé-bron?",
             "Alleen exact waargenomen bronovereenkomsten; geen merge- of claimrecht."]
    if not report.get("source_clocks_comparable"):
        lines.append("Bronmomenten onvergelijkbaar; wijzigingen ONBEKEND.")
    for row in report["current_prs"]:
        description = {
            "exact_head_changed_REINSPECT_GATE": "HEAD veranderd: exacte CI/reviews opnieuw inspecteren",
            "same_head_owner_step_changed_REVIEW_GATE": "Owneractie veranderd: actuele releasegate beoordelen",
            "same_observed_head_and_owner_step_NOT_ALL_CLEAR": "Zelfde waargenomen HEAD/owneractie, overige gates ONBEKEND",
            "newly_in_bounded_sample_NOT_NEW_PR": "Nieuw in steekproef, geen bewijs van nieuwe PR",
            "partial_exact_source_NO_GATE_DELTA": "Bron gedeeltelijk: geen betrouwbare gatevergelijking",
            "incomparable_source_times_NO_GATE_DELTA": "Bronmomenten onvergelijkbaar",
        }[row["state"]]
        lines.append(row["pr_url"] + " · " + description)
    for url in report["previously_sampled_not_current_NOT_MERGED"]:
        lines.append(url + " · niet meer in beperkte steekproef, NIET bewezen gemerged")
    if not report["current_prs"] and not report["previously_sampled_not_current_NOT_MERGED"]:
        lines.append("Geen vergelijkbare PR’s in dit beperkte venster; portfolio status ONBEKEND.")
    return "\n".join(lines)


def html_panel(report):
    """Script-free operator cards for changed existing-PR gates, never release."""
    from html import escape
    # One validation path shared by owner text and offline HTML.
    text_summary(report)
    descriptions = {
        "exact_head_changed_REINSPECT_GATE":
            "HEAD gewijzigd · controleer verplichte CI en reviews opnieuw",
        "same_head_owner_step_changed_REVIEW_GATE":
            "Vervolgactie gewijzigd · huidige gate opnieuw bekijken",
        "same_observed_head_and_owner_step_NOT_ALL_CLEAR":
            "Zelfde geobserveerde HEAD en gate-actie · geen bewijs van herstel",
        "newly_in_bounded_sample_NOT_NEW_PR":
            "Nu in beperkte steekproef · niet bewezen nieuw",
        "partial_exact_source_NO_GATE_DELTA":
            "Onvolledige bron · echte gatewijziging onbekend",
        "incomparable_source_times_NO_GATE_DELTA":
            "Bronmomenten niet vergelijkbaar · wijzigingen onbekend",
    }
    cards = []
    for item in report["current_prs"]:
        state = item["state"]
        if state not in descriptions or not PR_URL.fullmatch(item["pr_url"]):
            raise ValueError("untrusted private PR delta card")
        head = item.get("observed_head_sha")
        if head is not None and (type(head) is not str or not HEAD.fullmatch(head)):
            raise ValueError("invalid private current PR commit")
        cards.append(
            '<li><a target="_blank" rel="noopener noreferrer" href="' +
            escape(item["pr_url"], quote=True) + '">' +
            escape(item["pr_url"].rsplit("/", 1)[-1]) + '</a> · ' +
            escape(descriptions[state]) + ' · HEAD ' +
            escape(head[:12] if head else "ONBEKEND") + '</li>')
    previously_missing = report["previously_sampled_not_current_NOT_MERGED"]
    if not isinstance(previously_missing, list) or len(previously_missing) > MAX_ROWS:
        raise ValueError("invalid previously sampled PR delta count")
    for url in previously_missing:
        if type(url) is not str or not PR_URL.fullmatch(url):
            raise ValueError("untrusted missing sampled PR URL")
        cards.append('<li><a href="' + escape(url, quote=True) +
                     '" target="_blank" rel="noopener noreferrer">' +
                     escape(url.rsplit("/", 1)[-1]) +
                     '</a> · Niet opnieuw gezien, geen bewijs van merge/sluiting</li>')
    warning = ("Bronmomenten niet vergelijkbaar, delta ONBEKEND."
               if not report["source_clocks_comparable"]
               else "Alleen binnen de huidige/beperkte PR-bron. Geen eigenaarschap of mergebevoegdheid.")
    return ('<section><h2>Bestaande PR’s · bronwijzigingen sinds vorige privéweergave</h2>'
            '<p class="muted">' + escape(warning) + '</p><ul>' +
            (''.join(cards) if cards else
             '<li>Geen vergelijkbare PR’s; status niet bewezen vrij.</li>') +
            '</ul></section>')
