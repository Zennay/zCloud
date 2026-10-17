# zCloud deploy-ops: read-only pull-request collision inventory

This runbook describes the **advisory source check** proposed in [PR #1460](https://github.com/Zennay/zCloud/pull/1460). It is not a deployment gate, merge authority, branch-protection substitute, or VPS diagnostic. Do not use an unmerged draft workflow as evidence of a live feature.

## Operator workflow (after independent review and merge only)

1. In **Zennay/zCloud → Actions**, select **zCloud PR ownership collision inventory (read-only)**. Manually dispatch against the exact canonical `main`, as owner Zennay, on the first run attempt.
2. The job runs on **GitHub-hosted ubuntu-latest**, with only `contents:read` and `pull-requests:read`. It verifies the checkout SHA against `main`, reads repository PR metadata and complete changed-file pages, and rechecks `main` again before the job ends. No VPS, Firefox, systemd, runner repair, queue, SQLite, merge or deployment code runs.
3. Read the job summary: it reports current-main snapshot SHA, **unverified** SHA-256 snapshot fingerprint, PRs/files inspected, stale-base count, exact-path conflicts, file-versus-directory conflicts and transitive review groups. The fingerprint helps compare identical snapshots; it is **not** a cryptographic signature of GitHub's authority.
4. A conflict (exit 1) means file ownership overlaps need human coordination. It must **not** trigger runner recovery, automatically close either PR or override existing owners. Invalid, partial, foreign or changing input (exit 2) means **no completeness claim**. A zero-conflict exit (0) means only that this frozen, read-only **open-PR file-path sample** contained no detected file conflict; it is **not a merge approval**.
5. Independently check all currently open PRs and **PR-less branches**, their actual heads, active owners, dependencies and semantic collisions **immediately before any integration decision**. Never promote live production based on this summary. Release remains exclusively governed by #580/PWQ-41, #1089, #1447 and independent review.

## Limits and failure boundaries

| Evidence | Meaning | Not proven |
| --- | --- | --- |
| Exact-path overlap | Two or more PRs touch the same literal Git path, including both old and new paths on renames. | Semantic compatibility or correct eventual merge. |
| File/directory overlap | A PR-owned file occupies an ancestor of a path owned by another PR (e.g., `docs/ops` and `docs/ops/report.md`). | Whether one PR's own rename/restructure can be applied cleanly. |
| Transitive conflict group | PR A conflicts with B, while B conflicts with C; group A+B+C needs coordinated review. | All three directly conflict, or any preferred merge order. |
| File-disjoint PR count | No detected literal/ancestry conflicts within the verified open-PR snapshot. | Approval, dependency safety, PR-less branches, reviewer sign-off. |
| Stale base | PR's reported base SHA differs from the sampled canonical `main`. It remains in the conflict graph. | That the PR can be merged or its existing CI is current. |
| SHA-256 snapshot fingerprint | Digest of normalized keys and bounded JSON evidence. | A GitHub-signed, immutable, durable or externally attested snapshot. |

**Fail closed** on incomplete PR-list/file pagination, duplicate IDs/filenames, invalid change fields, moving head/main, changed PR-list order, rejected GitHub redirect/foreign response/MIME, non-regular input, symlink in **any ancestor**, FIFO, overlarge/changed source bytes, and other ambiguity.

The scanner currently bounds open PRs to 1,000 and changed files to 300 per PR. A cap overflow is an **incomplete inventory**, never evidence of no collision. It does not enumerate un-PR'd branches (#870/#875/#1310), nor provide the complete reconciliation required by #1204 or authorization to manipulate production. Fork-origin PRs are conservatively refused rather than declared harmless.

## Offline, read-only reproduction

For a complete *trusted and independently collected* snapshot file (not an arbitrary PR description), run:

```bash
python3 scripts/zcloud_deployops_pr_owner_collision_20261015.py --snapshot /path/to/snapshot.json
```

Exit codes: **0** path-disjoint, **1** conflict(s), **2** refused input. All return `release_authorized=false` and `mutation_performed=false`. The analyzer reads data only; it does not fetch GitHub itself. Its snapshot schema is defined in `scripts/zcloud_deployops_pr_owner_snapshot_20261015.py`, and its output must not be passed directly to a release workflow as trusted approval.

Focused source-only regressions:

```bash
python3 -m unittest -v \
  tests.test_zcloud_deployops_pr_owner_collision_20261015 \
  tests.test_zcloud_deployops_pr_owner_snapshot_20261015
```

No credentials are needed for the offline tests. Do not rerun a production deploy or restart the self-hosted runner to make a source-only audit go green.

## Compare two full frozen inventories offline (review aid, not permission)

The paired snapshot diff is useful when coordinating #1204: it makes new,
removed and updated PR identities visible **even when the total number of
conflicting files is unchanged**. Both files must contain complete collector
JSON and describe the **same canonical main SHA**. An old snapshot has no
special authority just because a later snapshot shares its SHA; neither is
signed or a lock on future GitHub changes.

```bash
python3 -m scripts.zcloud_deployops_pr_owner_delta_20261015 \\
  --baseline /safe/path/baseline.json \\
  --snapshot /safe/path/new-snapshot.json
python3 -m unittest -v tests.test_zcloud_deployops_pr_owner_delta_20261015
```

Exit **0** means byte-normalized evidence is identical in the two supplied
snapshots (not that live PR ownership is safe); **1** means any reported
difference requires operator review; **2** refuses invalid, incomplete,
symlinked or unequal-main evidence. The JSON report contains bounded PR-ID
lists, changed file-owner paths, new or resolved exact-path collisions,
changed file-vs-directory conflict edges, before/after counts and both SHA-256
snapshot fingerprints. Truncation flags preserve honest full counts. There
is **no PR-less branch coverage, semantic merge proof, approval or write path**.
