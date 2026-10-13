#!/usr/bin/env python3
"""Read-only candidate tag-rule evidence for zCloud recovery reservations.

A policy match is NOT an authorization to dispatch, create refs or deploy.
Read the canonical public GitHub rulesets API; if any evidence is missing,
ambiguous, partial, hidden or changing, fail closed. No repository writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zcloud_recovery_github_evidence_20261013 import (
    SourceDenied, github_get, read_main_sha,
)
from zcloud_recovery_git_ref_ledger_20261013 import PREFIX

SCHEMA = "zcloud-recovery-tag-ruleset-preflight-v1"
PREFIX_PATTERN = PREFIX + "/*"
MAX_PAGES = 10
PAGE_SIZE = 100
MAX_RULESETS = MAX_PAGES * PAGE_SIZE
ALLOWED_RULE_TYPES = {"deletion", "update"}


def result(reason, *, match=False, sha=None):
    return {
        "schema": SCHEMA,
        "reason": reason,
        "sha": sha,
        "tag_protection_candidate": match,
        "effective_protection_verified": False,
        "owner_review_required": True,
        "authorized": False,
        "dispatch_allowed": False,
        "mutations_performed": False,
    }


def exact_protection(detail):
    """Strong syntax screen, *not* proof of effective Github enforcement.

    Only exact prefix + SHA-tag pattern is admitted. Rulesets with unexposed
    bypass actors, unknown condition syntax, exclusion or partial rules DENY.
    Github ruleset authors/admins may still alter rules; no production grant.
    """
    if not isinstance(detail, dict):
        return False
    if detail.get("target") != "tag" or detail.get("enforcement") != "active":
        return False
    bypass = detail.get("bypass_actors")
    if type(bypass) is not list or bypass:
        return False
    conditions = detail.get("conditions")
    if not isinstance(conditions, dict):
        return False
    names = conditions.get("ref_name")
    if not isinstance(names, dict):
        return False
    includes, excludes = names.get("include"), names.get("exclude")
    if includes != [PREFIX_PATTERN] or excludes != []:
        return False
    rules = detail.get("rules")
    if not isinstance(rules, list) or len(rules) < 2 or len(rules) > 32:
        return False
    names = set()
    for rule in rules:
        if not isinstance(rule, dict) or type(rule.get("type")) is not str:
            return False
        names.add(rule["type"])
    return ALLOWED_RULE_TYPES.issubset(names)


def screen_snapshot(*, repo_id, before_sha, after_sha, listing_complete, summaries, details):
    if type(repo_id) is not int or repo_id != 1389227570:
        return result("untrusted_repository")
    from zcloud_recovery_git_ref_ledger_20261013 import _valid_sha
    if not _valid_sha(before_sha) or before_sha != after_sha:
        return result("main_drift_or_invalid_sha")
    if listing_complete is not True or not isinstance(summaries, list) or not isinstance(details, dict):
        return result("rulesets_incomplete", sha=before_sha)
    if len(summaries) > MAX_RULESETS:
        return result("rulesets_unbounded", sha=before_sha)
    ids = set()
    for entry in summaries:
        if not isinstance(entry, dict):
            return result("malformed_ruleset_summary", sha=before_sha)
        rule_id = entry.get("id")
        if type(rule_id) is not int or rule_id < 1 or rule_id in ids:
            return result("ambiguous_ruleset_id", sha=before_sha)
        ids.add(rule_id)
    if len(details) != len(ids) or set(details) != ids:
        return result("missing_or_extra_ruleset_details", sha=before_sha)
    if not ids:
        return result("no_rulesets", sha=before_sha)
    for summary in summaries:
        d = details[summary["id"]]
        if not isinstance(d, dict) or d.get("id") != summary["id"]:
            return result("detail_identity_mismatch", sha=before_sha)
        # Summary and full detail MUST agree on activation/target/source.
        if any(summary.get(k) != d.get(k) for k in (
                "source_type", "source", "enforcement")):
            return result("detail_summary_mismatch", sha=before_sha)
        if summary.get("source_type") not in {"Repository", "Organization"}:
            return result("invalid_ruleset_source", sha=before_sha)
        if summary["source_type"] == "Repository" and summary.get("source") != "Zennay/zCloud":
            return result("untrusted_ruleset_source", sha=before_sha)
    if any(exact_protection(details[i]) for i in ids):
        return result("candidate_ruleset_syntax_only", match=True, sha=before_sha)
    return result("no_strict_update_delete_protection", sha=before_sha)


def collect(get=github_get):
    """Bounded complete list+detail observer. A missing page aborts the proof.

    No trust is derived from Github list summaries, which omit bypass/rules.
    """
    before = read_main_sha(get)
    repo = get("")
    if not isinstance(repo, dict) or repo.get("id") != 1389227570 or repo.get("full_name") != "Zennay/zCloud":
        raise SourceDenied("untrusted_repository")
    summaries = []
    for page in range(1, MAX_PAGES + 1):
        batch = get(f"/rulesets?includes_parents=true&targets=tag&per_page={PAGE_SIZE}&page={page}")
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise SourceDenied("bad_ruleset_listing")
        summaries.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    else:
        raise SourceDenied("ruleset_pagination_exceeds_limit")
    details = {}
    for summary in summaries:
        if not isinstance(summary, dict) or type(summary.get("id")) is not int or summary["id"] < 1:
            raise SourceDenied("invalid_ruleset_id")
        ident = summary["id"]
        if ident in details:
            raise SourceDenied("duplicate_ruleset")
        details[ident] = get(f"/rulesets/{ident}?includes_parents=true")
    after = read_main_sha(get)
    return {"repo_id": repo["id"], "before_sha": before, "after_sha": after,
            "listing_complete": True, "summaries": summaries, "details": details}


def observe(get=github_get):
    try:
        data = collect(get)
        return screen_snapshot(**data)
    except SourceDenied as exc:
        return result(str(exc))
    except (TypeError, ValueError, KeyError):
        return result("malformed_ruleset_evidence")


if __name__ == "__main__":
    print(json.dumps(observe(), sort_keys=True, separators=(",", ":")))
