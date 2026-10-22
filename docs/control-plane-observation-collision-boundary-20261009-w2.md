# Offline observation identity collision contract

This isolated reference rejects duplicate worker IDs within a bounded observational batch, including identical and contradictory reports. It accepts only exact ASCII lowercase IDs and explicitly enumerated states; it never treats a positive diagnostic as proof of live workers.

**Not integrated:** Worker IDs are untrusted labels, not authenticated identity. This helper cannot verify provenance, replay resistance, atomic state, task ownership, freshness, or generation. All outcomes set `authorizes_action=false`.

**Ownership:** #580/PWQ-41 and #1089 exclusively own serialized production integration. Do not wire this reference into worker, queue, scheduler, SQLite, browser, VPS, recovery, deployment, or runner mutations without their review. Run `python3 -m unittest discover -s tests -p 'test_control_plane_observation_collisions_offline_20261009_w2.py' -v` on the exact PR head; require full regression and independent review before merge. No production correctness claim follows from these fixtures.
