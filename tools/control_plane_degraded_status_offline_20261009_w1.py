"""Offline, non-authorizing degraded-status classifier; NOT wired to production."""
from datetime import datetime, timezone

UNKNOWN = "Status unknown"

def classify(observation, *, now, expected_worker_id=None, expected_assignment_id=None):
    """Return a display-only state. Fail closed for invalid or uncorrelated evidence."""
    result = {"label": UNKNOWN, "authorizes_action": False}
    if type(observation) is not dict or type(now) is not datetime or now.tzinfo is None:
        return result
    if now.utcoffset() != timezone.utc.utcoffset(now):
        return result
    # Reject attacker-controlled extra authority or lifecycle fields.
    allowed = {"kind", "source", "observed_at", "worker_id", "assignment_id", "correlated"}
    if set(observation) - allowed:
        return result
    if any(type(k) is not str for k in observation):
        return result
    # Bound envelope fields and prohibit mixing lifecycle identity with unrelated events.
    if any(type(v) is str and len(v) > 256 for v in observation.values()):
        return result
    kind = observation.get("kind")
    base_fields = {"kind", "source", "observed_at"}
    identity_fields = {"worker_id", "assignment_id", "correlated"}
    if kind == "generation_started":
        if set(observation) != base_fields | identity_fields or type(observation["correlated"]) is not bool:
            return result
    elif set(observation) != base_fields:
        return result
    if type(kind) is not str or type(observation.get("source")) is not str or not observation["source"].strip() or observation["source"] != observation["source"].strip():
        return result
    stamp = observation.get("observed_at")
    if type(stamp) is not str:
        return result
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return result
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed > now or (now - parsed).total_seconds() > 120:
        return result
    # Source and identity values must avoid Unicode format/invisible ambiguity.
    from unicodedata import category
    if any(category(ch) == "Cf" for ch in observation["source"]):
        return result
    # A valid event source must not contain C0/C1 control characters.
    if any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in observation["source"]):
        return result
    # Reject invisible control characters in event identity as well as source.
    for name in ("worker_id", "assignment_id"):
        value = observation.get(name)
        if type(value) is str and any(ord(ch) < 32 or 127 <= ord(ch) <= 159 or category(ch) == "Cf" for ch in value):
            return result
    # Reject whitespace normalization ambiguity in event identity.
    for name in ("worker_id", "assignment_id"):
        value = observation.get(name)
        if type(value) is str and value != value.strip():
            return result
    if kind == "api_unavailable":
        return {"label": "Service temporarily unavailable", "authorizes_action": False}
    if kind == "conflict":
        return {"label": "Conflicting observations", "authorizes_action": False}
    if kind == "claim_get":
        return {"label": "Claim state unverified", "authorizes_action": False}
    if kind == "prompt_sent":
        return {"label": "Prompt submitted", "authorizes_action": False}
    if kind == "api_responding":
        return {"label": "API responding", "authorizes_action": False}
    if kind == "generation_started":
        if (type(expected_worker_id) is str and bool(expected_worker_id.strip())
                and type(expected_assignment_id) is str and bool(expected_assignment_id.strip())
                and expected_worker_id == expected_worker_id.strip()
                and expected_assignment_id == expected_assignment_id.strip()
                and type(observation.get("worker_id")) is str
                and type(observation.get("assignment_id")) is str
                and observation["worker_id"] == expected_worker_id
                and observation["assignment_id"] == expected_assignment_id
                and observation.get("correlated") is True):
            return {"label": "Generation observed", "authorizes_action": False}
    return result
