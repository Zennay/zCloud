# zCloud control-plane: SQLite resource-lease capacity race (isolated RED contract)

Date: 2026-10-09. Base: `main@f115d70ad03d80314be6a11adcc43430c15dbaa9`.

## Scope and observation

`project_runtime.acquire_resource` cleans expired leases, counts holders in a resource pool, then inserts a lease. With the standard zCloud SQLite callers, the cleanup `DELETE` begins a transaction and serializes competing writers. A caller explicitly opening a SQLite connection in **autocommit mode** (`isolation_level=None`) does not retain that transaction across the count-and-insert boundary. Two distinct owners can therefore count zero holders for a one-slot pool and each receive an accepted lease. This is an admission invariant, not a request to alter priority or allocation policy.

## Reproducer

`python -m unittest -v tests.test_project_runtime_resource_lease_autocommit_race_20261009_w1`

The add-only test suite uses two independent connections and a temporary local SQLite file and project contracts. The autocommit test pauses both threads **after** fetching the real `SELECT COUNT(*)` result, making the relevant interleaving deterministic rather than depending on timing. It requires the one-slot pool never to admit two leases. This test is **expected RED on current main** and must remain red until an actual upstream fix enforces atomic read/admit/insert even under autocommit (or the API explicitly rejects unsupported connection transaction modes fail-closed). The normal transactional-mode and disjoint control-pool tests should stay GREEN.

## Remediation acceptance (for the serialized owner)

1. Agree and document whether `acquire_resource` accepts autocommit connections. If yes, acquire write exclusivity **before** capacity evaluation; ensure the transaction, rollback, and subsequent commit semantics are safe across multiple independent callers, including ordinary zCloud scripts and the governed executor. If no, reject autocommit before any mutation and confirm the expected-red test is updated to assert explicit denial rather than accidental overadmission.
2. Keep one heavy-pool slot exclusive across independent processes; legitimate renewal by its owner must not consume an extra slot and must not bypass capacity after a contract/pool change. Preserve control-plane pool isolation.
3. Run targeted tests plus the complete canonical regression at an exact reviewed head. Treat any PR-triggered privileged dashboard recovery as an independent authorization gate; it is **not** justified by passing unit tests.
4. Require owner agreement before touching `project_runtime.py`: active PR #1270 owns that file; serialized #580/PWQ-41 and #1089 control integration. Do not merge this RED carrier alone.

## Ownership and safety

Only this document and its unique isolated test path are owned by the current worker. No existing source, shared suite, workflow, SQLite/runtime database, service, VPS, runner, queue, browser, or production file is changed. Tests manipulate only temporary local files. This reference provides no admission, deploy, merge, or mutation permission.
