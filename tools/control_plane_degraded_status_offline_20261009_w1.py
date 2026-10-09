"""Offline, non-authorizing degraded-status classifier; NOT wired to production."""
from datetime import datetime, timezone

UNKNOWN = "Status unknown"

def classify(observation, *, now):
    """Return a display-only state. Fail closed for invalid or uncorrelated evidence."""
    result = {"label": UNKNOWN, "authorizes_action": False}
    if type(observation) is not dict or type(now) is not datetime or now.tzinfo is None:
        return result
    if now.utcoffset() != timezone.utc.utcoffset(now):
        return result
    kind = observation.get("kind")
    if type(kind) is not str or type(observation.get("source")) is not str or not observation["source"].strip():
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
        if all(type(observation.get(k)) is str and observation[k].strip() for k in ("worker_id", "assignment_id")) and observation.get("correlated") is True:
            return {"label": "Generation observed", "authorizes_action": False}
    return result
