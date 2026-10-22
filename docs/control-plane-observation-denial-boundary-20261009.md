# Offline observation denial reference — 2026-10-09

This reference asserts only that descriptive observations cannot authorize privileged actions. It is **not wired into production**. The code's `authorized` and `mutation_performed` outputs are always `false`; the input's `source`, `verified`, `approval`, timestamps, status, and run IDs are untrusted assertions.

## Verification
Run from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_zcloud_control_plane_observation_denial_20261009.py' -v
```

The scope is exactly the three add-only reference paths under `scripts/`, `tests/`, and `docs/`. No server, browser, SQLite, scheduler, worker, queue, deployment, Actions workflow or live-control code is edited. A passing test is **not** evidence of safe runtime integration.

## Ownership and release gate
#580/PWQ-41 and #1089 own serialized runtime integration. Existing worker-evidence/provenance PRs (#1250, #1251, #1253), dashboard recovery and status source owners remain separate. This reference must not be used to justify restart, force-push, workflow dispatch, merge, queue changes or deployment. Require collision review, current exact-head CI proof and explicit owner sign-off before any promotion.
