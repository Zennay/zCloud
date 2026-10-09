# Offline worker-event sequence continuity reference (2026-10-09)

## Purpose

A diagnostic consumer must not quietly interpret a partial or replayed event batch as a complete chronology. The standalone reference in `scripts/control_plane_sequence_gap_offline_20261009_w1.py` checks a bounded event batch against an independently supplied last-observed sequence number. An uninterrupted per-worker, per-generation sequence is reported as `continuous_offline_only`; **even positive results always set `authorizes_action: false`**.

## Limitations / integration boundary

This is a pure in-memory reference, not a production contract verification. A caller-supplied worker, generation, cursor or event can be forged. It neither authenticates sources nor persists a compare-and-swap cursor, acquires a lease, defends against simultaneous consumers, or proves the producer emitted every event. It does not modify the worker scheduler, SQLite, queue, browser, workflows, VPS, deployment or any existing control-plane file.

Before any runtime adoption, the serialized control-plane owner must separately implement authenticated event origin, durable atomic monotonic cursor, generation fencing, deterministic retry/idempotency, failure handling, limits, and end-to-end integration tests. Existing integration ownership (#580/PWQ-41 and #1089) remains authoritative. No live mutation is authorized by this PR.

## Focused validation

`python3 -m unittest discover -s tests -p 'test_control_plane_sequence_gap_offline_20261009_w1.py' -v`

Review requires exact-head successful focused and regression checks on an approved runner, collision/owner verification, and explicit serialized integration-owner approval. Keep draft until those gates pass.
