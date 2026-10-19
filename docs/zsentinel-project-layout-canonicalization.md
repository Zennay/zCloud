# zCloud project layout canonicalization (#684)

The project dashboard's manual reorder/archive editor persists to
`project-layout.json`. Previously a duplicate known ID could enter `order`
or `archived`; `load_project_layout` repeated those duplicates, and the
configuration validator could subsequently reject the saved layout.

The source fix in `server.py` uses one first-occurrence filter for **both
load and save**, based solely on known canonical project IDs.

For example, with projects in order `ftmo, cloud, supa`, a request containing
`order = ["ftmo", "cloud", "ftmo", "ghost"]` and
`archived = ["supa", "ghost", "supa"]` is represented as
`order = ["ftmo", "cloud", "supa"]` and `archived = ["supa"]`.

Older on-disk layouts containing duplicate IDs become readable without
rewriting the file during GET. The next **authorized** layout save stores the
canonical representation through the existing temporary-file-and-replace
operation. No data migration, global project registry edit or automatic
cleanup is triggered by this source patch.

This is a branch-only product fix attached to zCloud backend PR #1549.
It changes neither live VPS files nor worker leases, requests or queue state.
Source integration still depends on exact-head mandatory CI, independent
review, protected-main admission and serialized owner-approved production
release. The old test-only PR #682 remains independent evidence; no new
or manually executed QA suite is included in this update.
