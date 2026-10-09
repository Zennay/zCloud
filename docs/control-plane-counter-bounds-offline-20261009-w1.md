# Offline bounded counter evidence — 2026-10-09

This is a deliberately isolated observation contract for monotonic numeric fields (for example, hypothetical sequence numbers in snapshots). It **does not** establish that any production source currently uses a 64-bit signed counter, nor does it infer trust, authenticity, freshness, authorization, persistence or exclusivity from a counter comparison.

## Rules

- Accept only Python native `int` in the inclusive range 0 through 2^63-1; explicitly reject booleans, even though they subclass `int`.
- Counter decreases are rejected as rollback observations.
- Equality can be reported as observational stability but can be rejected when a consumer requires progress.
- Exceeding signed 64-bit bound is rejected, never wrapped, clamped, or silently cast.
- All failures return finite bounded reason codes without echoing source data.

## Integration boundary

Before considering actual integration, inventory the producer's real numeric type and maximum, transport JSON parsing semantics (including precision loss in JavaScript above 2^53-1), and counter reset/epoch changes. Consumers must prove provenance, source identity, epoch, trusted clock/fencing and atomic durable read-write behavior independently. A counter alone grants **no** right to perform restart, deployment, queue mutation or task claiming.

Run offline tests with:

```bash
python3 -m unittest discover -s tests -p 'test_control_plane_counter_bounds_offline_20261009_w1.py' -v
```

This adds no production import, API, browser, worker, database, queue, workflow, VPS or deploy modification. Serialized control-plane integration belongs to #580/PWQ-41 and #1089. Do not merge pending exact-head CI and independent owner review.
