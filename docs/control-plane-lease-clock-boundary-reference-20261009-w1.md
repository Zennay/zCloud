# Control-plane lease-clock boundary reference (2026-10-09)

This is an **offline reference model**, not the deployed lease implementation and not admission evidence. Its tests cover timezone-awareness, equivalent UTC instants, strict expiration at the deadline, crossing calendar boundaries, bounded integer lease lengths and malformed input denial.

## Integration-owner handoff

- Owner of actual claim/lease contract: PR #1219; serialized runtime writer: #580 / PWQ-41, and #1089. This new branch must not replace, modify, or preempt those owners.
- For production comparison, obtain exact-head server/API traces for claim, heartbeat, release, and reclaim; verify database atomicity separately. No clock-only test can prove atomic ownership fencing.
- Check that backend clock acquisition is consistent for claim/heartbeat/expiry and that stored expiry values have an explicit UTC interpretation. A naive wall-clock timestamp must never silently inherit server-local timezone.
- At equality with the deadline, the reference considers the lease expired. Confirm production contract owners adopt or explicitly reject this boundary before any integration.
- Timezone conversion is not a substitute for a monotonic elapsed-time clock on a single process; reboot and cross-process persistence require a distinct design review.
- A green result only validates this small reference model; it does not validate production API semantics, SQLite claim races, or an authorized deploy.

Run: `python3 -m unittest discover -s tests -p 'test_control_plane_lease_clock_boundary_reference_w1.py' -v`.

No modifications to existing files, queues, runners, deployments, workflows or services. Review before merge; keep separate from #1219.
