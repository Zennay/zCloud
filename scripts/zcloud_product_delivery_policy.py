"""Product-first admission policy for *new* zCloud portfolio queue claims.

Existing claimed/running/verifying tasks, priority, permission checks and
independent CI gates are not modified by this pure classifier.
"""
from __future__ import annotations

import json
import re

# These are *work package types*, not words that may appear in an ordinary
# feature description. A product bug can mention tests without being a QA lane.
QA_LANES = frozenset({
    "qa", "quality-validation", "qa-validation", "regression-validation",
    "test-validation", "evaluation-validation", "test-only", "qa-only",
    "kwaliteitsvalidatie", "kwaliteitscontrole", "qa-validatie",
    "testvalidatie", "test-only-validatie", "regressie-validatie",
    "qa-only-validatie", "alleen-qa", "uitsluitend-qa",
})
QA_TITLE = re.compile(
    r"^(?:(?:p[0-3])\s*[:.·/-]\s*)?"
    r"(?:qa(?:\b|[-_/])|quality[ -]validation\b|"
    r"regression[ -](?:test|suite|validation)\b|"
    r"kwaliteits[ -]?(?:controle|validatie)\b|"
    r"regressie[ -]?(?:test|controle)\b|"
    r"(?:test|tests|testing|verify|validate|validation|audit|inspect|"
    r"review|testen|toets|toetsen|controle|controleer|valideer|"
    r"validatie|inspecteer|beoordeel)[ :·/-])",
    re.IGNORECASE,
)
DELIVERY_VERBS = re.compile(
    r"\b(?:build|implement|integrate|ship|deliver|deploy|fix|repair|"
    r"migrate|restore|unblock|enable|release|merge|refactor|"
    r"bouw|bouwen|implementeer|implementeren|integreer|integreren|"
    r"repareer|repareren|herstel|herstellen|"
    r"lever|leveren|uitrollen|vrijgeven|samenvoegen|verhelp|"
    r"mergen|fixen|los\s+op|oplossen)\b",
    re.IGNORECASE,
)
# Negative delivery phrases must not turn a QA-only ticket into product work.
NEGATED_DELIVERY = re.compile(
    r"\b(?:do\s+not|don't|never|not|no|geen|niet|zonder|"
    r"mag\s+niet)\s+(?:[\w-]+\s+){0,3}"
    r"(?:merge|deploy|release|build|fix|repair|implement|integrate|"
    r"ship|deliver|refactor|migrate|restore|enable|herstel|"
    r"repareer|bouw|implementeer|integreer|lever|samenvoegen|"
    r"mergen|fixen|oplossen|uitrollen|los\s+op)\b",
    re.IGNORECASE,
)



# Imperative test-only tasks also exist as old backlog titles without QA lane
# metadata ("Run full regression suite", "Draai alle regressietests").
QA_RUN_ONLY_TITLE = re.compile(
    r"^(?:(?:p[0-3])\s*[:.·/-]\s*)?"
    r"(?:run|rerun|execute|start|draai|voer(?:\s+uit)?)\s+"
    r"(?:[\w-]+\s+){0,4}"
    r"(?:qa(?:\b|[-_/])|regression(?:\s+(?:test|suite))?\b|"
    r"regressie(?:tests?|suite|ronde)?\b|tests?\b|test[- ]suite\b|"
    r"kwaliteitscontrole\b|validatieronde\b)",
    re.IGNORECASE,
)


# "QA-only" is an explicit lane declaration even if a note mentions a fix.
EXPLICIT_QA_ONLY_TITLE = re.compile(
    r"^(?:(?:p[0-3])\s*[:.·/-]\s*)?"
    r"(?:qa[-_ /]?only|test[-_ /]?only|uitsluitend\s+qa|alleen\s+qa)"
    r"\b",
    re.IGNORECASE,
)

def _positive_delivery(text):
    return bool(DELIVERY_VERBS.search(NEGATED_DELIVERY.sub(" ", text)))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate delivery metadata key")
        result[key] = value
    return result


def _metadata(item):
    # The persisted raw receipt wins over a separate presentation object.
    # Otherwise a caller supplying both could shadow corrupt/QA-only raw
    # metadata with a sanitized {} and convert a hold into a candidate.
    raw = item.get("metadata_json")
    if raw is not None and raw != "":
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError("unbounded delivery metadata")
        parsed = json.loads(raw, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                ValueError("invalid nonfinite metadata")))
        if not isinstance(parsed, dict):
            raise ValueError("invalid delivery metadata")
        return parsed
    if raw is not None and not isinstance(raw, str):
        raise ValueError("invalid delivery metadata")
    data = item.get("metadata")
    if data is not None:
        if not isinstance(data, dict):
            raise ValueError("invalid delivery metadata object")
        return data
    return {}


def _lane_value(value):
    """Normalize only known work-type labels; never infer ownership."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s_/]+", "-", value.strip().casefold())


def product_delivery_classification(item):
    """Explain a potential *new* worker admission, without granting a claim.

    Only fixed reason codes are emitted. This decision never alters an active
    lease, human gate, reviewer requirement or protected release.
    """
    if not isinstance(item, dict):
        return "invalid", "invalid_row"
    if str(item.get("status") or "").strip().casefold() != "queued":
        return "invalid", "not_queued"
    try:
        metadata = _metadata(item)
    except (ValueError, TypeError, UnicodeError):
        return "invalid", "invalid_metadata"
    for key in ("lane", "lane_id", "focus", "work_type", "lane_focus",
                "workstream"):
        value = metadata.get(key)
        if value is not None and not isinstance(value, str):
            return "invalid", "invalid_lane_metadata"
        if _lane_value(value) in QA_LANES:
            return "qa_only", "explicit_qa_lane"
    execution_lane = metadata.get("execution_lane")
    if execution_lane is not None:
        if isinstance(execution_lane, str):
            if _lane_value(execution_lane) in QA_LANES:
                return "qa_only", "explicit_qa_lane"
        elif isinstance(execution_lane, dict):
            for key in ("lane_id", "focus", "work_type"):
                value = execution_lane.get(key)
                if value is not None and not isinstance(value, str):
                    return "invalid", "invalid_lane_metadata"
                if _lane_value(value) in QA_LANES:
                    return "qa_only", "explicit_qa_lane"
        else:
            return "invalid", "invalid_lane_metadata"
    title = item.get("title")
    criteria = item.get("completion_criteria")
    if not isinstance(title, str) or not title.strip() or len(title) > 2000:
        return "invalid", "invalid_title"
    if criteria is None:
        criteria = ""
    if not isinstance(criteria, str) or len(criteria) > 32768:
        return "invalid", "invalid_completion_criteria"
    title = title.strip()
    if EXPLICIT_QA_ONLY_TITLE.match(title):
        return "qa_only", "explicit_qa_title"
    if QA_RUN_ONLY_TITLE.match(title):
        return "qa_only", "qa_execution_only_title"
    if (QA_TITLE.match(title) and not _positive_delivery(title)
            and not _positive_delivery(criteria)):
        return "qa_only", "qa_title_without_delivery"
    if not _positive_delivery(title) and re.search(
        r"\b(?:test suite|regression suite|qa round|quality.validation|"
        r"test(?:en|suite)?|regressie(?:test|ronde)|"
        r"kwaliteitscontrole|validatieronde)\b", criteria,
        flags=re.IGNORECASE,
    ) and not _positive_delivery(criteria):
        return "qa_only", "qa_criteria_without_delivery"
    return "product", "product_candidate"


def product_delivery_disposition(item):
    """Backward-compatible pure yes/no classification for new admission."""
    return product_delivery_classification(item)[0]


def is_new_product_delivery(item):
    """True only for freshly queued product work, never an authz decision."""
    return product_delivery_disposition(item) == "product"


def portfolio_fair_candidate_window(ordered_rows, limit=240, per_project_reserve=8):
    """Select a bounded cross-project product window from source-ordered rows.

    This is an *observation/selection* step, never lease authorization. The
    upstream caller must filter QA/invalid rows, retain current source priority
    order and apply the usual lane, live-claim, capacity and owner gates after
    this shortlist. In a noisy queue, reserve a small share for each observed
    project so one project's first 240 tasks cannot hide independent work.
    """
    if type(limit) is not int or not 1 <= limit <= 240:
        raise ValueError("invalid candidate window limit")
    if type(per_project_reserve) is not int or not 1 <= per_project_reserve <= limit:
        raise ValueError("invalid candidate project reserve")
    rows = list(ordered_rows)
    if len(rows) <= limit:
        return rows
    # Take up to N source-ordered rows per project first; only then top up
    # remaining capacity in original priority order. Never reclassify a row,
    # invent ownership, or mutate a SQLite iterator/queue row.
    seen = {}
    reserved = []
    picked = set()
    for index, row in enumerate(rows):
        raw = row["project_id"]
        project = str(raw or "")
        if not project or seen.get(project, 0) >= per_project_reserve:
            continue
        if len(reserved) >= limit:
            break
        seen[project] = seen.get(project, 0) + 1
        reserved.append((index, row))
        picked.add(index)
    if len(reserved) < limit:
        for index, row in enumerate(rows):
            if index in picked:
                continue
            reserved.append((index, row))
            if len(reserved) >= limit:
                break
    # Keep exact source priority/creation ordering for downstream scheduling.
    return [row for _, row in sorted(reserved, key=lambda entry: entry[0])]


# Stable, intentionally path-free codes; safe for the authenticated UI.
HOLD_REASON_CODES = frozenset({
    "invalid_row", "not_queued", "invalid_metadata", "invalid_lane_metadata",
    "invalid_title", "invalid_completion_criteria", "explicit_qa_lane",
    "explicit_qa_title", "qa_execution_only_title", "qa_title_without_delivery",
    "qa_criteria_without_delivery",
})


def product_queue_snapshot(items, max_visible=8):
    """Bounded read-only queue pressure summary; never an admission receipt."""
    if type(max_visible) is not int or not 1 <= max_visible <= 20:
        raise ValueError("invalid delivery summary limit")
    visible = 0
    malformed_rows = 0
    active_owner_rows = 0
    active_missing_worker_slot = 0
    active_missing_claim_start = 0
    active_missing_claim_expiry = 0
    queued_visible = 0
    held_ineligible = 0
    held_slot_occupied = 0
    held_stale_claim_fields = 0
    skipped_qa = 0
    skipped_invalid = 0
    hold_reasons = {}
    candidate_rows = []
    # A bounded, observation-only cross-project view. Its counts never give
    # permission to reuse a lane, release a lease, or start an unclaimed task.
    project_activity = {}
    priorities = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    priority_counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "other": 0}
    for item in items:
        if not isinstance(item, dict):
            malformed_rows += 1
            continue
        visible += 1
        raw_project = item.get("project_id")
        project = (raw_project.strip()[:56]
                   if isinstance(raw_project, str) else "")
        activity = None
        if project:
            activity = project_activity.setdefault(project, {
                "project_id": project, "visible_rows": 0,
                "active_claimed_running_verifying_visible": 0,
                "active_missing_worker_slot_visible": 0,
                "active_missing_claim_start_visible": 0,
                "active_missing_claim_expiry_visible": 0,
                "queued_rows_visible": 0, "queued_product_candidates_visible": 0,
                "queued_qa_only_held": 0,
                "queued_invalid_or_unknown_held": 0,
                "queued_ineligible_held": 0,
                "queued_worker_slot_occupied": 0,
                "queued_residual_lease_fields_held": 0,
            })
            activity["visible_rows"] += 1
        status = str(item.get("status") or "").strip().casefold()
        if status in ("claimed", "running", "verifying"):
            active_owner_rows += 1
            if activity is not None:
                activity["active_claimed_running_verifying_visible"] += 1
            # Evidence gaps are operator observations, not an expired lease
            # verdict and never authority to take or release a worker claim.
            # Count independently: the same active owner can lack both fields.
            if item.get("worker_slot") is None:
                active_missing_worker_slot += 1
                if activity is not None:
                    activity["active_missing_worker_slot_visible"] += 1
            if item.get("claimed_at") in (None, ""):
                active_missing_claim_start += 1
                if activity is not None:
                    activity["active_missing_claim_start_visible"] += 1
            if item.get("claim_expires") in (None, ""):
                active_missing_claim_expiry += 1
                if activity is not None:
                    activity["active_missing_claim_expiry_visible"] += 1
        if status != "queued":
            continue
        queued_visible += 1
        if activity is not None:
            activity["queued_rows_visible"] += 1
        # These are NOT reasons to revoke, force-claim or reassign a task.
        # Match fresh SQLite admission including absence of old lease markers.
        if type(item.get("eligible")) not in (int, bool) or item["eligible"] != 1:
            held_ineligible += 1
            if activity is not None:
                activity["queued_ineligible_held"] += 1
            continue
        if item.get("worker_slot") is not None:
            held_slot_occupied += 1
            if activity is not None:
                activity["queued_worker_slot_occupied"] += 1
            continue
        if item.get("claimed_at") is not None or item.get("claim_expires") is not None:
            held_stale_claim_fields += 1
            if activity is not None:
                activity["queued_residual_lease_fields_held"] += 1
            continue
        disposition = item.get("delivery_policy_disposition")
        reason = item.get("delivery_policy_reason")
        # A source annotation is one inseparable classification + reason
        # pair. Accepting an individually valid but mismatched pair (e.g.
        # product/explicit_qa_lane) would falsely promote held tasks.
        valid_receipt = (
            (disposition == "product" and reason == "product_candidate")
            or (disposition == "qa_only" and reason in {
                "explicit_qa_lane", "explicit_qa_title",
                "qa_execution_only_title", "qa_title_without_delivery",
                "qa_criteria_without_delivery",
            })
            or (disposition == "invalid" and reason in {
                "invalid_row", "not_queued", "invalid_metadata",
                "invalid_lane_metadata", "invalid_title",
                "invalid_completion_criteria",
            })
        )
        # A direct raw SQLite/source row always gets classified from its
        # actual metadata, even when a caller supplied plausible annotations.
        # The normalized API path has already source-bound its annotation
        # before removing metadata_json in _portfolio_queue_row().
        if not valid_receipt or item.get("metadata_json") not in (None, ""):
            disposition, reason = product_delivery_classification(item)
        if disposition != "product":
            hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
        if disposition == "qa_only":
            skipped_qa += 1
            if activity is not None:
                activity["queued_qa_only_held"] += 1
        elif disposition != "product":
            skipped_invalid += 1
            if activity is not None:
                activity["queued_invalid_or_unknown_held"] += 1
        else:
            if activity is not None:
                activity["queued_product_candidates_visible"] += 1
            candidate_rows.append(item)
            priority = str(item.get("priority") or "").strip().upper()
            priority_counts[priority if priority in priorities else "other"] += 1
    candidate_rows.sort(key=lambda row: (
        priorities.get(str(row.get("priority") or "").upper(), 4),
        str(row.get("created_at") or ""),
        str(row.get("queue_id") or ""),
    ))
    # Operator-only cross-project view: the normal P0-first candidate ordering
    # remains untouched; one noisy project cannot hide all other project leads.
    # This list is NOT used to assign a worker or release a lease.
    project_leads = {}
    for item in candidate_rows:
        project = str(item.get("project_id") or "")[:56]
        if not project:
            continue
        lead = project_leads.get(project)
        if lead is None:
            project_leads[project] = {
                "project_id": project,
                "product_candidates_visible": 1,
                "first_queue_id": str(item.get("queue_id") or "")[:160],
                "highest_priority": str(item.get("priority") or "")[:4],
            }
        else:
            lead["product_candidates_visible"] += 1
    project_shortlist = list(project_leads.values())
    project_shortlist.sort(key=lambda row: (
        priorities.get(str(row["highest_priority"]).upper(), 4),
        -row["product_candidates_visible"], row["project_id"],
    ))
    project_shortlist_limit = min(16, max_visible)
    # These are observed row categories, not inferred blockers or owner identity.
    # Show pressure where an already-active task and more queued work coexist.
    pressure = list(project_activity.values())
    pressure.sort(key=lambda row: (
        -(row["active_missing_worker_slot_visible"] > 0
          or row["active_missing_claim_start_visible"] > 0
          or row["active_missing_claim_expiry_visible"] > 0),
        -(row["active_claimed_running_verifying_visible"] > 0
          and row["queued_product_candidates_visible"] > 0),
        -row["queued_product_candidates_visible"],
        -row["active_claimed_running_verifying_visible"],
        -row["queued_qa_only_held"] - row["queued_invalid_or_unknown_held"],
        row["project_id"],
    ))
    for row in pressure:
        if (row["active_missing_worker_slot_visible"]
                or row["active_missing_claim_start_visible"]
                or row["active_missing_claim_expiry_visible"]):
            next_step = "owner_reconcile_active_claim_evidence_before_any_new_work"
        elif row["active_claimed_running_verifying_visible"]:
            next_step = "check_active_claims_before_any_new_work"
        elif row["queued_product_candidates_visible"]:
            next_step = "verify_claim_scope_capacity_and_owner"
        elif (row["queued_qa_only_held"] or row["queued_invalid_or_unknown_held"]):
            next_step = "owner_rescope_required_for_held_work"
        else:
            next_step = "no_product_candidate_observed"
        row["operator_next_step"] = next_step
    return {
        "policy": "product-delivery-2026-10-17",
        "source_coverage": "visible_api_rows_only_not_full_queue",
        "visible_rows": visible,
        "malformed_nonobject_rows": malformed_rows,
        "active_claimed_running_verifying_visible": active_owner_rows,
        "active_missing_worker_slot_visible": active_missing_worker_slot,
        "active_missing_claim_start_visible": active_missing_claim_start,
        "active_missing_claim_expiry_visible": active_missing_claim_expiry,
        "active_claim_evidence_is_advisory_only": True,
        "active_claim_evidence_gap_projects_observed": sum(
            bool(row["active_missing_worker_slot_visible"]
                 or row["active_missing_claim_start_visible"]
                 or row["active_missing_claim_expiry_visible"])
            for row in project_activity.values()
        ),
        "queued_rows_visible": queued_visible,
        "queued_ineligible_held": held_ineligible,
        "queued_worker_slot_occupied": held_slot_occupied,
        "queued_residual_lease_fields_held": held_stale_claim_fields,
        "queued_qa_only_held": skipped_qa,
        "queued_invalid_or_unknown_held": skipped_invalid,
        "queued_hold_reason_counts": dict(sorted(hold_reasons.items())),
        "queued_product_candidates": len(candidate_rows),
        "candidate_priority_counts": priority_counts,
        "candidate_list_truncated": len(candidate_rows) > max_visible,
        "distinct_projects_with_visible_product_work": len(project_leads),
        "project_shortlist_truncated": len(project_shortlist) > project_shortlist_limit,
        "project_shortlist_is_advisory_only": True,
        "project_activity_source_coverage": "visible_api_rows_only_not_full_queue",
        "project_activity_is_advisory_only": True,
        "project_activity_truncated": len(pressure) > 20,
        "project_activity": pressure[:20],
        "project_shortlist": project_shortlist[:project_shortlist_limit],
        "candidates": [
            {
                "project_id": str(item.get("project_id") or "")[:56],
                "queue_id": str(item.get("queue_id") or "")[:160],
                "priority": str(item.get("priority") or "")[:4],
            }
            for item in candidate_rows[:max_visible]
        ],
        "not_claimed_or_authorized": True,
        "admission_scope_lease_and_capacity_not_verified": True,
    }
