# Emergency-stop admission — offline reference only

This branch introduces a deterministic deny-first test reference. It **never** stops a worker and must not be wired directly to an executable control endpoint.

## Runtime integration requirements

1. Verify the authenticated principal against current operator permissions; the provided boolean flags are untrusted reference inputs.
2. Persist a unique request ID with an atomic exclusive worker/generation lease. Never accept a copied `fence_held` boolean as proof of ownership.
3. Compare generation and pending-stop status within the same transaction immediately before any state transition. Reject stale/replayed requests, including concurrent duplicate submissions.
4. Make emergency stop idempotent with an auditable terminal receipt; do not acknowledge a no-op or missing worker as successful execution.
5. Ensure any in-flight task is fenced before dispatching a replacement and record the prior owner/generation for incident review.
6. Run negative HTTP/SQLite concurrency tests, exact-final-head regression, and obtain the #580/PWQ-41 + #1089 serialized control-plane owner approval before considering merge/deployment.

## Local offline verification

`python3 -m unittest discover -s tests -p 'test_control_plane_emergency_stop_offline_20261009_w1.py' -v`

The positive decision string `admitted_offline_only` is **not** operator authorization, an executable lease, successful stop confirmation, or production release evidence. No production state or runtime files are modified.
