"""Pure, fail-closed control-plane action idempotency-key primitives.

This module does not dispatch actions or authorize worker, browser, or VPS writes.
Callers must still enforce their own authentication and authorization policies.
"""
from __future__ import annotations

import hashlib
import json
import re

_ACTIONS = frozenset({"start", "push", "pause", "drain", "resume"})
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.ASCII)
_REQUEST = re.compile(r"^[A-Za-z0-9_-]{16,128}$", re.ASCII)


class InvalidActionIdentity(ValueError):
    """Untrusted or non-canonical action identity."""


def action_idempotency_key(*, project: str, worker: str, action: str, request_id: str) -> str:
    """Return a stable opaque key scoped to one *explicit* request identity.

    Never derive a key from action type alone: subsequent user-initiated pushes
    must remain distinct. The caller must supply a fresh request_id for each
    new intent and reuse it only for a retry of that same intent.
    """
    if not isinstance(project, str) or not _ID.fullmatch(project):
        raise InvalidActionIdentity("invalid project")
    if not isinstance(worker, str) or not _ID.fullmatch(worker):
        raise InvalidActionIdentity("invalid worker")
    if not isinstance(action, str) or action not in _ACTIONS:
        raise InvalidActionIdentity("invalid action")
    if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
        raise InvalidActionIdentity("invalid request id")
    payload = json.dumps(
        [1, project, worker, action, request_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "zca1_" + hashlib.sha256(payload).hexdigest()
