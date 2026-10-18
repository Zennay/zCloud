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
})
QA_TITLE = re.compile(
    r"^(?:(?:p[0-3])\s*[:.·/-]\s*)?"
    r"(?:qa(?:\b|[-_/])|quality[ -]validation\b|"
    r"regression[ -](?:test|suite|validation)\b|"
    r"(?:test|tests|testing|verify|validate|validation|"
    r"audit|inspect|review)[ :·/-])",
    re.IGNORECASE,
)
DELIVERY_VERBS = re.compile(
    r"\b(?:build|implement|integrate|ship|deliver|deploy|fix|repair|"
    r"migrate|restore|unblock|enable|release|merge|refactor)\b",
    re.IGNORECASE,
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate delivery metadata key")
        result[key] = value
    return result


def _metadata(item):
    data = item.get("metadata")
    if data is not None:
        if not isinstance(data, dict):
            raise ValueError("invalid delivery metadata object")
        return data
    raw = item.get("metadata_json")
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str) or len(raw) > 16384:
        raise ValueError("unbounded delivery metadata")
    parsed = json.loads(raw, object_pairs_hook=_unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            ValueError("invalid nonfinite metadata")))
    if not isinstance(parsed, dict):
        raise ValueError("invalid delivery metadata")
    return parsed


def is_new_product_delivery(item):
    """True only for queued work that is not an isolated QA/review package.

    Never use this as a claim/lease/authz check. The caller must keep every
    existing permission, scope, priority and runner admission rule intact.
    """
    if not isinstance(item, dict):
        return False
    if str(item.get("status") or "queued").lower() != "queued":
        return False
    # Ambiguous/corrupt metadata cannot be used to disguise a QA-only lane.
    # Deny only *new* admission; never mutate an existing claim or queue row.
    try:
        metadata = _metadata(item)
    except (ValueError, TypeError, UnicodeError):
        return False
    for key in ("lane", "lane_id", "focus", "work_type", "lane_focus", "workstream"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip().lower() in QA_LANES:
            return False
    lane = metadata.get("execution_lane")
    if isinstance(lane, dict) and str(lane.get("lane_id") or "").lower() in QA_LANES:
        return False
    title = str(item.get("title") or "").strip()
    if not title or len(title) > 2000:
        return False
    criteria = str(item.get("completion_criteria") or "")
    if len(criteria) > 32768:
        return False
    # A test-prefixed *title* does not cancel an urgent real fix. Existing
    # P0 work such as "Test urgent planner flow" with a concrete "Fix ..."
    # completion criterion remains product work and keeps normal priority.
    # Only reject a QA-prefixed item when neither title nor requirements
    # describe a material build/repair/delivery action.
    if (QA_TITLE.match(title) and not DELIVERY_VERBS.search(title)
            and not DELIVERY_VERBS.search(criteria)):
        return False
    # Some legacy tickets have a neutral title but contain only an instruction
    # to execute tests. Do not auto-admit them until re-scoped by their owner.
    if not DELIVERY_VERBS.search(title) and re.search(
        r"\b(?:test suite|regression suite|qa round|quality.validation)\b", criteria,
        flags=re.IGNORECASE,
    ) and not DELIVERY_VERBS.search(criteria):
        return False
    return True


def product_queue_snapshot(items, max_visible=8):
    """An advisory, bounded GET-only summary, never an admission receipt."""
    if type(max_visible) is not int or not 1 <= max_visible <= 20:
        raise ValueError("invalid delivery summary limit")
    skipped_qa = 0
    candidate_rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (str(item.get("status") or "").lower() != "queued"
                or not item.get("eligible")
                or item.get("worker_slot") is not None):
            continue
        if not is_new_product_delivery(item):
            skipped_qa += 1
            continue
        candidate_rows.append(item)
    priorities = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    candidate_rows.sort(key=lambda row: (
        priorities.get(str(row.get("priority") or "").upper(), 4),
        str(row.get("created_at") or ""),
        str(row.get("queue_id") or ""),
    ))
    return {
        "policy": "product-delivery-2026-10-17",
        "queued_qa_only_held": skipped_qa,
        "queued_product_candidates": len(candidate_rows),
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
