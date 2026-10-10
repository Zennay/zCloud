# Numeric host observation boundary (offline reference)

This file documents the isolated contract tested in `tests/test_control_plane_numeric_observation_boundary_20261009.py`.

Host CPU, memory and disk readings are untrusted observations, **not permission** to restart workers, alter queue state, run commands or deploy. The standalone reference requires an exact built-in dictionary containing only four fields: `cpu_percent`, `memory_percent`, `disk_percent` (all between 0 and 100 inclusive), and `sample_age_seconds` (0 to 300 inclusive). All readings must be finite exact built-in numbers; booleans, subclasses, spoofed mappings, malformed and missing/extra fields fail closed.

Even complete fresh evidence outputs `authorizes_action=false`. This is an offline reference, **not** authenticated telemetry, a live resource policy, or permission to act. The 300-second illustration is a reference limit, not a production freshness guarantee; integration must be independently reviewed and tested.

Focused local test command: `python3 -m unittest discover -s tests -p 'test_control_plane_numeric_observation_boundary_20261009.py' -v`.

Ownership: this branch touches only the two new paths named above. No modifications to existing control-plane runtime, #580/PWQ-41, #1089, dashboard recovery or worker/scheduler ownership. No CI, deployment or VPS success is asserted by this document.
