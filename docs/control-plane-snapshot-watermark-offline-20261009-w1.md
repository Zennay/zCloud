# Offline control-plane snapshot watermark contract (2026-10-09)

Owner: isolated offline validation; production integration remains owned by #580/PWQ-41 and #1089.

## Purpose
Classify whether a *supplied* control-plane observation is a replay, stale update, conflicting record, or a plausible forward movement. Source: `scripts/control_plane_snapshot_watermark_offline_20261009_w1.py`.

This function does **not** verify signatures, provenance, authenticated worker identity, claim ownership, freshness against a trusted clock, exclusive leases, or actual GitHub head state. It must not be used as a runtime authorization decision.

## Decision behavior
- Invalid shape, booleans masquerading as counters, out-of-range counters, malformed lowercase commit SHA, or malformed or noncanonical UTC timestamp (strict `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, 1–6 fractional digits): `invalid`.
- Lower generation: `stale_generation`.
- Same generation with different head: `conflicting_head`.
- Same generation with lower sequence: `stale_sequence`.
- Equal generation and sequence, byte-equivalent record: `duplicate`.
- Equal generation and sequence, different receipt: `conflicting_receipt`.
- Same generation and head with larger sequence but earlier parsed UTC observation time: `regressed_observation_time`.
- Same generation and head with larger sequence and nondecreasing parsed UTC time: `forward` (offline comparison only).
- Higher generation and nonzero sequence: `unanchored_generation`.
- Higher generation and zero sequence: `new_generation_unverified` (never an authorization).

## Integration gates
Before any production use, the serialized owner must independently establish: (1) authenticated and scoped emitter identity; (2) immutable payload binding to project, worker, head and assignment; (3) atomic monotonic CAS/fencing in persistent state, including restart/replay races; (4) trusted time semantics and expiration; (5) worker-generation handoff authorization; (6) explicit fail-closed decisions with no implicit allow on offline `forward` or `new_generation_unverified` classifications; (7) adversarial concurrent testing and exact-head CI acceptance.

This addition contains no live integration and does not touch the VPS, worker queue, SQLite, browser, workflows, service or deployment. No merge/deploy authorization is conveyed by passing its tests.

Run isolated tests:
```sh
python3 -m unittest discover -s tests -p 'test_control_plane_snapshot_watermark_offline_20261009_w1.py' -v
```
