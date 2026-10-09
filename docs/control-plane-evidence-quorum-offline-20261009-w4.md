# Offline observation disagreement boundary

This isolated **reference contract** treats multiple worker-state reports as untrusted observations, never as a quorum that authorizes restarting, generation claims, task release, merge or deploy.

| Input | Classification |
| --- | --- |
| All reporting running | unverified_running |
| All reporting stopped | unverified_stopped |
| Both running and stopped | contradictory |
| Unknown present without contradiction | incomplete |
| Malformed, duplicate, missing, excessive sources | invalid |

A source label is **not authenticated**: unique source strings do not establish independence or provenance. Production must bind identity, trusted timestamps, generation, claim leases, exact-head and runtime evidence, and use an atomic decision boundary before any action. No live integration is proposed here.

Focused check: `python3 -m unittest discover -s tests -p 'test_control_plane_evidence_quorum_offline_20261009_w4.py' -v`.

Ownership: add-only offline files; serialized #580/PWQ-41 and #1089 remain the sole runtime integration owners. Do not deploy or merge based on this classifier.
