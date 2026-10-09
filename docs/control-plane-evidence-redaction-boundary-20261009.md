# Control-plane diagnostic handoff redaction (offline reference)

This add-only slice demonstrates bounded diagnostic *data minimization* for handoff payloads. It does not edit zCloud server, workers, scheduler, queue, SQLite, Actions, secrets handling or deployment paths. The classifier is **not integrated** with production logging and MUST NOT be described as production protection.

- Nested keys containing token, cookie, authorization, password, secret, private key or API key are redacted; common inline bearer/GitHub/OpenAI token formats are masked.
- Non-finite floats (`NaN`, positive/negative infinity) are replaced with `[REDACTED]` to avoid invalid JSON-compatible evidence. Finite numbers remain unchanged.
- Unknown Python objects are never stringified. Collection size, string length and nesting are bounded.
- This is a best-effort display sanitizer, **not** a complete data-loss-prevention boundary. Unrecognized credential formats, URL query values, embedded structured blobs, maliciously chosen keys, and paths can still leak. Never feed untrusted raw logs into it as if they were safe.
- All evidence remains unauthenticated and incapable of authorizing restart, queue writes, merge or deployment.
- Independent review and expanded adversarial coverage would be required before any separately owned integration; existing serialized #580/PWQ-41 and #1089 boundaries remain in force.

Focused command: `python3 -m unittest discover -s tests -p 'test_control_plane_evidence_redaction_offline_20261009.py' -v`.
