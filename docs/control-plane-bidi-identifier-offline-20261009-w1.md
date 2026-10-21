# Offline identifier ambiguity reference (2026-10-09)

This isolated, non-authorizing reference intentionally limits displayable
control-plane identifiers to bounded ASCII. It rejects bidirectional override,
zero-width characters, visually confusable Unicode and normalization variants.
It is suitable for reviewer discussion, **not production validation**.

Run `python3 -m unittest discover -s tests -p 'test_control_plane_bidi_identifier_offline_20261009_w1.py' -v`.

No live API integration, database migration, existing identifier change, worker
control or deployment is included. Any production adoption needs an inventory
of existing identifiers and backward-compatibility decisions, plus serialized
owner approval (#580/PWQ-41 and #576). No claim is made that current production
accepts these identifiers or is vulnerable to spoofing.
