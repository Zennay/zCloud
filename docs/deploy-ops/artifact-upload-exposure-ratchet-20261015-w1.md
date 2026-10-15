# Source-only CI artifact upload exposure gate — 15 October 2026

Issue: #633 (self-hosted workflow trust debt). Owner: separate deploy-ops file-disjoint source lane.

This new GitHub-hosted pull-request check rejects changed or new workflow YAML that advertises an upload-artifact action with a broad or secret-bearing path: repository root, recursive glob, shell/dynamic expression, traversal, .env, .ssh, credentials, secrets and token files. It also rejects a true or ambiguous include-hidden-files value; a missing path; duplicate upload parameters; YAML aliases; symlinked workflow blobs; malformed source identities; and non-ASCII workflow path ambiguity. Path inventory and source blobs are bounded. Existing unchanged workflows are grandfathered; they are **not** certified safe.

The checker uses exact SHA-bound Git source ancestry, an immutable checkout, read-only GitHub token permissions, independent GitHub-hosted runner, and origin/main/head source identity checked before and after regressions. The tests include a real temporary Git history and a symlink fixture. Only the four new files in this lane are owned. No existing runtime/control-plane/VPS/release workflow changes are permitted.

Limitations: deliberately conservative lexical YAML screening, not a complete YAML or GitHub Actions evaluator. Dynamically imported actions, repository contents inside a nominally narrow artifact path, cross-step dataflow, renamed aliases with no literal upload-artifact token, and unchanged legacy workflow files are out of scope. False positives for ambiguous upload syntax are intentional; explicit review remains required. A PASS is source evidence only, never production safety, independence of approval, permission to merge, or privilege to release.

Manual local check (read-only): python3 -m unittest discover -s tests -p 'test_zcloud_artifact_upload_exposure_ratchet_20261015_w1.py'. The GitHub-hosted PR workflow calls the exact source checker with immutable base/head SHA, and rechecks the remote references after the tests.

Release boundary: protected-main #922, canonical runner proof #1326, existing writer locks #1447 and serialized #580/PWQ-41/#1089 remain separate independent gates. No runner/VPS/Firefox/SQLite/worker/queue modification, privileged workflow dispatch, merge, or production promotion is authorized here.
