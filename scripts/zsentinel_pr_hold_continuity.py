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



def _gate_sample(row, head):
    """Capture bounded EXACT-HEAD gate samples; no required-check inference.

    Check-run and classic status samples are separate API families. Approval
    count is a bounded same-head observation, not branch-protection approval.
    """
    ci, legacy, reviews = (row.get("ci"),
                           row.get("legacy_status_contexts"),
                           row.get("reviews"))
    if any(type(source) is not dict or source.get("head_sha") != head
           for source in (ci, legacy, reviews)):
        raise ValueError("exact-HEAD gate source mismatch")
    sampled, total = ci.get("sampled"), ci.get("total_reported")
    if (type(sampled) is not int or type(total) is not int
            or not 0 <= sampled <= 30 or not sampled <= total <= 1000000
            or type(ci.get("all_checks_observed")) is not bool
            or ci.get("branch_protection_verified") is not False
            or ci.get("status_contexts_verified") is not False):
        raise ValueError("untrusted bounded check-run gate source")
    def names(source, key, cap):
        values = source.get(key)
        if (type(values) is not list or len(values) > cap
                or any(type(name) is not str or not 1 <= len(name) <= 180
                       or any(ord(char) < 32 or ord(char) == 127 for char in name)
                       for name in values)):
            raise ValueError("invalid sampled GitHub gate names")
        return tuple(sorted(values))
    checks = {key: names(ci, key, 8)
              for key in ("failed", "pending", "inconclusive")}
    count = legacy.get("sampled")
    if (type(count) is not int or not 0 <= count <= 100
            or legacy.get("required_contexts_verified") is not False
            or legacy.get("branch_protection_verified") is not False
            or type(legacy.get("page_truncated_or_unknown")) is not bool
            or legacy.get("combined_observed") not in
               ("pending", "success", "failure", "error")):
        raise ValueError("invalid bounded classic status source")
    classic = {key: names(legacy, key, 8)
               for key in ("failed", "pending")}
    approvals = reviews.get("same_head_approvals_seen")
    if (type(approvals) is not int or not 0 <= approvals <= 30
            or type(reviews.get("same_head_changes_requested")) is not bool
            or type(reviews.get("review_page_full")) is not bool
            or reviews.get("independent_policy_verified") is not False):
        raise ValueError("invalid bounded same-head review source")
    return {
        "check_runs": (
            sampled, total, ci["all_checks_observed"],
            checks["failed"], checks["pending"], checks["inconclusive"]),
        "classic_statuses": (
            legacy["combined_observed"], count,
            legacy["page_truncated_or_unknown"],
            classic["failed"], classic["pending"]),
        "same_head_reviews": (
            approvals, reviews["same_head_changes_requested"],
            reviews["review_page_full"]),
    }


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
        repo = row.get("repository")
        number = row.get("pr_number")
        project = row.get("project_id")
        if (type(repo) is not str or not re.fullmatch(
                r"Zennay/[A-Za-z0-9_.-]{1,100}", repo)
                or type(number) is not int or not 1 <= number <= 100000000
                or type(project) is not str
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", project)
                or url != "https://github.com/" + repo + "/pull/" + str(number)
                or url in rows or coverage not in COVERAGE or
                action not in NEXT_ACTION_NL):
            raise ValueError("invalid or duplicate source PR URL/coverage")
        if coverage == EXACT:
            if type(head) is not str or not HEAD.fullmatch(head):
                raise ValueError("complete exact-PR coverage lacks head")
        elif head is not None:
            raise ValueError("incomplete PR source cannot carry current head")
        if (type(source.get("observed_at_utc")) is not str
                and source.get("observed_at_utc") is not None):
            raise ValueError("noncanonical PR source timestamp")
        # Only a FULL exact-head PR observation can prove which bounded
        # CI/status/review fields came from this source snapshot.
        gates = _gate_sample(row, head) if coverage == EXACT else None
        metadata = None
        if coverage == EXACT:
            base = row.get("base_sha")
            draft = row.get("draft")
            mergeable_known = row.get("mergeable_known")
            mergeable = row.get("mergeable")
            if (type(base) is not str or not HEAD.fullmatch(base)
                    or type(draft) is not bool
                    or type(mergeable_known) is not bool
                    or type(mergeable) is not bool):
                raise ValueError("invalid bounded PR draft/base/mergeability metadata")
            metadata = (base, draft, mergeable_known, mergeable)
        rows[url] = {"url": url, "head": head, "step": action,
                     "coverage": coverage, "gates": gates,
                     "metadata": metadata}
    return _clock(source.get("observed_at_utc")), rows



def _observed_transition(current, prior):
    """Owner-friendly before→after counts, never a complete CI/review verdict."""
    if current is None or prior is None:
        return []
    changes = []
    run_new, run_old = current["check_runs"], prior["check_runs"]
    if run_new != run_old:
        if run_new[0] != run_old[0]:
            changes.append("getoonde check-runs: " +
                           str(run_old[0]) + " → " + str(run_new[0]))
        labels = (("getoonde falende checks", 3),
                  ("getoonde wachtende checks", 4),
                  ("getoonde onbesliste checks", 5))
        for name, offset in labels:
            left, right = len(run_old[offset]), len(run_new[offset])
            if left != right or run_old[offset] != run_new[offset]:
                changes.append(name + ": " + str(left) + " → " + str(right))
        if run_new[1] != run_old[1]:
            changes.append("gerapporteerde totale check-runs: " +
                           str(run_old[1]) + " → " + str(run_new[1]))
        if run_new[2] != run_old[2]:
            changes.append("getoond checkvenster volledig: " +
                           ("ja" if run_old[2] else "nee") + " → " +
                           ("ja" if run_new[2] else "nee"))
    legacy_new, legacy_old = current["classic_statuses"], prior["classic_statuses"]
    if legacy_new != legacy_old:
        if legacy_new[0] != legacy_old[0]:
            changes.append("klassieke gezamenlijke status: " +
                           legacy_old[0] + " → " + legacy_new[0])
        for name, offset in (("falende legacy-contexts", 3),
                             ("wachtende legacy-contexts", 4)):
            left, right = len(legacy_old[offset]), len(legacy_new[offset])
            if left != right or legacy_old[offset] != legacy_new[offset]:
                changes.append(name + ": " + str(left) + " → " + str(right))
        if legacy_new[1] != legacy_old[1]:
            changes.append("getoonde legacy-contexts: " +
                           str(legacy_old[1]) + " → " + str(legacy_new[1]))
        if legacy_new[2] != legacy_old[2]:
            changes.append("legacy-pagina afgekapt: " +
                           ("ja" if legacy_old[2] else "nee") + " → " +
                           ("ja" if legacy_new[2] else "nee"))
    review_new, review_old = current["same_head_reviews"], prior["same_head_reviews"]
    if review_new != review_old:
        if review_new[0] != review_old[0]:
            changes.append("getoonde same-head approvals: " +
                           str(review_old[0]) + " → " + str(review_new[0]))
        if review_new[1] != review_old[1]:
            changes.append("getoonde changes-requested: " +
                           ("ja" if review_old[1] else "nee") + " → " +
                           ("ja" if review_new[1] else "nee"))
        if review_new[2] != review_old[2]:
            changes.append("reviewpagina afgekapt: " +
                           ("ja" if review_old[2] else "nee") + " → " +
                           ("ja" if review_new[2] else "nee"))
    return changes[:14]



def _operator_step_for_sample(item, old, changed_fields):
    """Name safe manual next work on newly observed evidence, not release."""
    if not changed_fields:
        return None
    new = item["gates"]
    before = old["gates"]
    if ("check_runs" in changed_fields and
            new["check_runs"][3] != before["check_runs"][3] and
            new["check_runs"][3]):
        return ("Onderzoek de actuele falende check-runs op deze PR-HEAD "
                "met de bevoegde code-eigenaar; geen automatische CI-rerun.")
    if ("classic_statuses" in changed_fields and
            new["classic_statuses"][3] != before["classic_statuses"][3] and
            new["classic_statuses"][3]):
        return ("Onderzoek falende klassieke GitHub-statuscontexts op de "
                "exacte PR-HEAD; stem herstel af met de eigenaar.")
    if ("same_head_reviews" in changed_fields and
            new["same_head_reviews"][1] and
            not before["same_head_reviews"][1]):
        return ("Verwerk het nieuwe same-head wijzigingsverzoek met de "
                "huidige PR-eigenaar; merge blijft geblokkeerd.")
    if "same_head_reviews" in changed_fields:
        return ("Beoordeel de gewijzigde same-head reviews met een "
                "onafhankelijke reviewer en huidige branch protection.")
    return ("Controleer exact-HEAD CI en vereiste reviewgates opnieuw met "
            "de PR-eigenaar; het gewijzigde bronvenster geeft geen mergevrijgave.")



def _pr_metadata_changes(item, old):
    """Only compare concrete values on the SAME exact PR source HEAD."""
    current, prior = item["metadata"], old["metadata"]
    if (current is None or prior is None
            or len(current) != 4 or len(prior) != 4):
        raise ValueError("missing exact PR metadata for continuity")
    changes = []
    if current[0] != prior[0]:
        changes.append("target_base_sha_changed")
    if current[1] != prior[1]:
        changes.append("draft_state_changed")
    if current[2:] != prior[2:]:
        changes.append("github_mergeability_hint_changed")
    return changes


def _metadata_transition(item, old, names):
    current, prior = item["metadata"], old["metadata"]
    result = []
    if "target_base_sha_changed" in names:
        result.append("PR-basis SHA: " + prior[0][:12] + " → " + current[0][:12])
    if "draft_state_changed" in names:
        result.append("draftstatus: " + ("draft" if prior[1] else "reviewklaar") +
                      " → " + ("draft" if current[1] else "reviewklaar"))
    if "github_mergeability_hint_changed" in names:
        def hint(row):
            return "onbekend" if not row[2] else "mergeable" if row[3] else "niet mergeable"
        result.append("GitHub mergeable-hint: " + hint(prior) + " → " + hint(current))
    return result


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
        elif item["metadata"][0] != old["metadata"][0]:
            state = "same_head_target_base_changed_REINSPECT_GATE"
        elif item["metadata"][1] != old["metadata"][1]:
            state = "same_head_draft_changed_REVIEW_GATE"
        elif item["metadata"][2:] != old["metadata"][2:]:
            state = "same_head_mergeability_hint_changed_RECHECK_RULES"
        elif item["step"] != old["step"]:
            state = "same_head_owner_step_changed_REVIEW_GATE"
        elif item["gates"] != old["gates"]:
            # A check can move from queued to failed without a new commit,
            # even when the coarse human next_step remains unchanged.
            state = "same_head_observed_ci_or_review_changed_REVIEW_GATE"
        else:
            state = "same_observed_head_and_gate_sample_NOT_ALL_CLEAR"
        gate_fields_changed = (
            [field for field in (
                "check_runs", "classic_statuses", "same_head_reviews")
             if item["gates"][field] != old["gates"][field]]
            if (comparable_clock and old is not None
                and item["coverage"] == EXACT and old["coverage"] == EXACT
                and item["head"] == old["head"]) else [])
        metadata_fields_changed = (
            _pr_metadata_changes(item, old)
            if (comparable_clock and old is not None
                and item["coverage"] == EXACT and old["coverage"] == EXACT
                and item["head"] == old["head"]) else [])
        changes.append({
            "pr_url": url,
            "state": state,
            "observed_head_sha": item["head"],
            "previous_head_sha": old["head"] if old else None,
            "observed_next_step": item["step"],
            "previous_next_step": old["step"] if old else None,
            "observed_metadata_changed": metadata_fields_changed,
            "observed_metadata_transitions": (
                _metadata_transition(item, old, metadata_fields_changed)
                if metadata_fields_changed else []),
            "observed_gate_families_changed": gate_fields_changed,
            "observed_gate_sample_changed": bool(gate_fields_changed),
            "observed_gate_transitions": (
                _observed_transition(item["gates"], old["gates"])
                if gate_fields_changed else []),
            "observed_gate_owner_step": (
                _operator_step_for_sample(item, old, gate_fields_changed)
                if gate_fields_changed else None),
            "gate_changed_proven": state in (
                "exact_head_changed_REINSPECT_GATE",
                "same_head_owner_step_changed_REVIEW_GATE",
                "same_head_observed_ci_or_review_changed_REVIEW_GATE",
                "same_head_target_base_changed_REINSPECT_GATE",
                "same_head_draft_changed_REVIEW_GATE",
                "same_head_mergeability_hint_changed_RECHECK_RULES"),
            "merge_verified": False, "owner_verified": False,
        })
    rank = {"exact_head_changed_REINSPECT_GATE": 0,
            "same_head_target_base_changed_REINSPECT_GATE": 0,
            "same_head_draft_changed_REVIEW_GATE": 1,
            "same_head_mergeability_hint_changed_RECHECK_RULES": 1,
            "same_head_observed_ci_or_review_changed_REVIEW_GATE": 1,
            "same_head_owner_step_changed_REVIEW_GATE": 1,
            "partial_exact_source_NO_GATE_DELTA": 2,
            "incomparable_source_times_NO_GATE_DELTA": 2,
            "newly_in_bounded_sample_NOT_NEW_PR": 3,
            "same_observed_head_and_gate_sample_NOT_ALL_CLEAR": 4}
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
            x["state"] == "same_observed_head_and_gate_sample_NOT_ALL_CLEAR"
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
            "same_head_target_base_changed_REINSPECT_GATE": "Doelbranch SHA gewijzigd: hercontroleer PR-basis en CI",
            "same_head_draft_changed_REVIEW_GATE": "Draftstatus gewijzigd: bepaal actuele reviewroute",
            "same_head_mergeability_hint_changed_RECHECK_RULES": "GitHub mergeable-indicatie veranderd: vereiste regels opnieuw lezen",
            "same_head_owner_step_changed_REVIEW_GATE": "Owneractie veranderd: actuele releasegate beoordelen",
            "same_head_observed_ci_or_review_changed_REVIEW_GATE": "Zelfde HEAD, maar geobserveerde CI/status/review gewijzigd: gate opnieuw bekijken",
            "same_observed_head_and_gate_sample_NOT_ALL_CLEAR": "Zelfde HEAD, eigenaaractie en begrensde CI/reviewsteekproef; overige gates ONBEKEND",
            "newly_in_bounded_sample_NOT_NEW_PR": "Nieuw in steekproef, geen bewijs van nieuwe PR",
            "partial_exact_source_NO_GATE_DELTA": "Bron gedeeltelijk: geen betrouwbare gatevergelijking",
            "incomparable_source_times_NO_GATE_DELTA": "Bronmomenten onvergelijkbaar",
        }[row["state"]]
        families = row.get("observed_gate_families_changed", [])
        labels = {
            "check_runs": "check-runs",
            "classic_statuses": "klassieke statussen",
            "same_head_reviews": "reviews",
        }
        if (type(families) is not list or len(families) > 3
                or any(f not in labels for f in families)):
            raise ValueError("invalid observed PR gate family changes")
        transitions = row.get("observed_gate_transitions", [])
        if (type(transitions) is not list or len(transitions) > 14
                or any(type(x) is not str or not 1 <= len(x) <= 150
                       or any(ord(ch) < 32 for ch in x) for x in transitions)):
            raise ValueError("invalid observed PR gate change details")
        owner_action = row.get("observed_gate_owner_step")
        if (owner_action is not None and
                (type(owner_action) is not str
                 or not 1 <= len(owner_action) <= 280
                 or any(ord(ch) < 32 for ch in owner_action))):
            raise ValueError("invalid observed gate owner next step")
        metadata_transitions = row.get("observed_metadata_transitions", [])
        if (type(metadata_transitions) is not list or
                len(metadata_transitions) > 3 or
                any(type(x) is not str or len(x) > 150 or
                    any(ord(ch) < 32 for ch in x) for x in metadata_transitions)):
            raise ValueError("invalid PR metadata transition")
        lines.append(row["pr_url"] + " · " + description +
                     (" · " + "; ".join(metadata_transitions)
                      if metadata_transitions else "") +
                     (" · geobserveerd: " + ", ".join(labels[x] for x in families)
                      if families else "") +
                     (" · " + "; ".join(transitions) if transitions else "") +
                     (" · eigenaaractie: " + owner_action
                      if owner_action else ""))
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
        "same_head_target_base_changed_REINSPECT_GATE":
            "Doelbranch gewijzigd · controleer exact-HEAD status tegen actuele basis",
        "same_head_draft_changed_REVIEW_GATE":
            "Draftstatus gewijzigd · bespreek onafhankelijke review met huidige eigenaar",
        "same_head_mergeability_hint_changed_RECHECK_RULES":
            "GitHub mergeable-hint veranderd · geen bewijs van beschermde-branch toestemming",
        "same_head_owner_step_changed_REVIEW_GATE":
            "Vervolgactie gewijzigd · huidige gate opnieuw bekijken",
        "same_head_observed_ci_or_review_changed_REVIEW_GATE":
            "Geobserveerde CI/status/review gewijzigd bij dezelfde HEAD · herinspecteer gate",
        "same_observed_head_and_gate_sample_NOT_ALL_CLEAR":
            "Zelfde waargenomen HEAD, actie en beperkte CI/reviewbron · geen bewijs van herstel",
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
        families = item.get("observed_gate_families_changed")
        labels = {"check_runs": "check-runs",
                  "classic_statuses": "klassieke status",
                  "same_head_reviews": "reviews"}
        if (type(families) is not list or len(families) > 3
                or len(set(families)) != len(families)
                or any(field not in labels for field in families)):
            raise ValueError("invalid bounded PR gate differences")
        transitions = item.get("observed_gate_transitions", [])
        if (type(transitions) is not list or len(transitions) > 14
                or any(type(v) is not str or not 1 <= len(v) <= 150
                       or any(ord(ch) < 32 for ch in v) for v in transitions)):
            raise ValueError("invalid PR observed state transitions")
        delta = (" · gewijzigd: " + ", ".join(labels[x] for x in families)
                 if families else "")
        metadata_transitions = item.get("observed_metadata_transitions", [])
        if (type(metadata_transitions) is not list or
                len(metadata_transitions) > 3 or
                any(type(x) is not str or len(x) > 150 or
                    any(ord(ch) < 32 for ch in x) for x in metadata_transitions)):
            raise ValueError("invalid PR metadata changes in operator HTML")
        detail = (" · " + "; ".join(metadata_transitions)
                  if metadata_transitions else "") + (
                    " · " + "; ".join(transitions) if transitions else "")
        owner_action = item.get("observed_gate_owner_step")
        if (owner_action is not None and
                (type(owner_action) is not str or
                 not 1 <= len(owner_action) <= 280 or
                 any(ord(ch) < 32 for ch in owner_action))):
            raise ValueError("invalid private gate owner followup")
        if owner_action:
            detail += " · eigenaaractie: " + owner_action
        cards.append(
            '<li><a target="_blank" rel="noopener noreferrer" href="' +
            escape(item["pr_url"], quote=True) + '">' +
            escape(item["pr_url"].rsplit("/", 1)[-1]) + '</a> · ' +
            escape(descriptions[state] + delta + detail) + ' · HEAD ' +
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
