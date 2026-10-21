# zCloud self-improvement change-set policy

Autonomous zCloud improvement must be reviewable as a distinct change-set. The
control-plane therefore treats "main" as the protected integration target and
expects every candidate to arrive from another Git ref through a pull request.

The executable guard lives in
"scripts/zcloud_self_improvement_changeset_guard.py" and has two independent
checks:

1. **PR candidate check** — the exact PR head must differ from the base revision,
   the head ref may not be "main"/"master", and the base-to-head diff must be a
   non-empty bounded change-set.
2. **Main push provenance check** — every exact commit observed by this workflow
   on "refs/heads/main" must be associated by GitHub with at least one merged
   pull request whose base is "main".

The workflow is read-only. It never edits repository settings, Git refs, VPS
state, SQLite, browser state, or services. The permanent-VPS job exists only to
prove the same guard and exact-head checkout on the canonical zCloud runner.

## Fail-closed codes

The guard emits bounded machine-readable reason codes instead of arbitrary diff
or PR payloads. Important failures include "head_ref_is_protected_base",
"empty_change_set", "git_diff_failed", and "main_push_without_merged_pr".

## Prevention boundary

This gate detects a direct "main" push immediately after GitHub publishes it; it
cannot retroactively prevent that Git write. Preventive enforcement still
belongs in GitHub branch protection or a repository ruleset requiring pull
requests. The current integration can read repository rulesets but cannot
administer branch-protection settings, so the roadmap item remains open until a
preventive repository rule is proven active. This guard is the executable,
file-disjunct control-plane foundation for that final gate.
