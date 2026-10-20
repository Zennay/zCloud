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
    r"mergen|fixen|oplossen|uitrollen)\b",
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


def _lane_value(value):
    """Normalize only known work-type labels; never infer ownership."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s_/]+", "-", value.strip().casefold())


def product_delivery_disposition(item):
    """Classify new work as product, qa_only or invalid, without claiming it.

    This is a policy filter, not a GitHub/Notion lease or worker admission
    receipt. Unknown structure must not be silently relabelled as product.
    """
    if not isinstance(item, dict):
        return "invalid"
    if str(item.get("status") or "queued").strip().casefold() != "queued":
        return "invalid"
    try:
        metadata = _metadata(item)
    except (ValueError, TypeError, UnicodeError):
        return "invalid"
    for key in ("lane", "lane_id", "focus", "work_type", "lane_focus",
                "workstream"):
        value = metadata.get(key)
        if value is not None and not isinstance(value, str):
            return "invalid"
        if _lane_value(value) in QA_LANES:
            return "qa_only"
    execution_lane = metadata.get("execution_lane")
    if execution_lane is not None:
        if isinstance(execution_lane, str):
            if _lane_value(execution_lane) in QA_LANES:
                return "qa_only"
        elif isinstance(execution_lane, dict):
            for key in ("lane_id", "focus", "work_type"):
                value = execution_lane.get(key)
                if value is not None and not isinstance(value, str):
                    return "invalid"
                if _lane_value(value) in QA_LANES:
                    return "qa_only"
        else:
            return "invalid"
    title = item.get("title")
    criteria = item.get("completion_criteria")
    if not isinstance(title, str) or not title.strip() or len(title) > 2000:
        return "invalid"
    if criteria is None:
        criteria = ""
    if not isinstance(criteria, str) or len(criteria) > 32768:
        return "invalid"
    title = title.strip()
    if EXPLICIT_QA_ONLY_TITLE.match(title):
        return "qa_only"
    # Keep an actual code repair, including Dutch wording, even if a legacy
    # ticket title begins with "Test". Explicit QA metadata always wins.
    if (QA_TITLE.match(title) and not _positive_delivery(title)
            and not _positive_delivery(criteria)):
        return "qa_only"
    # Legacy neutral-title QA work is held when nothing asks to deliver
    # product functionality. Negated release phrases cannot override this.
    if not _positive_delivery(title) and re.search(
        r"\b(?:test suite|regression suite|qa round|quality.validation|"
        r"test(?:en|suite)?|regressie(?:test|ronde)|"
        r"kwaliteitscontrole|validatieronde)\b", criteria,
        flags=re.IGNORECASE,
    ) and not _positive_delivery(criteria):
        return "qa_only"
    return "product"


def is_new_product_delivery(item):
    """True only for freshly queued product work, never an authz decision."""
    return product_delivery_disposition(item) == "product"


def product_queue_snapshot(items, max_visible=8):
    """An advisory, bounded GET-only summary, never an admission receipt."""
    if type(max_visible) is not int or not 1 <= max_visible <= 20:
        raise ValueError("invalid delivery summary limit")
    skipped_qa = 0
    skipped_invalid = 0
    candidate_rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (str(item.get("status") or "").lower() != "queued"
                or not item.get("eligible")
                or item.get("worker_slot") is not None):
            continue
        disposition = product_delivery_disposition(item)
        if disposition != "product":
            if disposition == "qa_only":
                skipped_qa += 1
            else:
                skipped_invalid += 1
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
        "queued_invalid_or_unknown_held": skipped_invalid,
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
