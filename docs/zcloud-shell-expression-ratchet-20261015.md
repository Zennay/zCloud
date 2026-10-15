# zCloud shell-expression no-new-debt tripwire (deploy-ops #633)

Standalone GitHub-hosted **advisory source check**; no release or production authority.
The guard compares immutable committed Git trees (main SHA -> candidate SHA) and
refuses **new** direct GitHub event, head-ref, ref-name and workflow input expressions
interpolated into a workflow run-shell command. It treats new YAML run aliases/tags
and incomplete expression fragments as suspicious. Existing risks are inventoried
but not grandfathered if replaced with a *different* unsafe expression; the
comparison uses path and normalized expression fingerprints, not just counts.

Safer patterns put untrusted values into a step env binding and reference
quoted shell variables; still validate the value according to its meaning.
The scanner reads bounded regular UTF-8 Git blobs only, checks sizes before
buffering and refuses symlinked workflow files, drifted/non-ancestor source,
invalid SHAs, missing or incomplete source evidence. CLI exit codes:
0 no new risk, 1 new risk, 2 missing/inconsistent evidence.

## Limitations and safety boundary

- This is **not a complete YAML parser**, a semantic CodeQL engine, a runner
  attestation or proof that an expression is exploitable. It is a monotone
  guard against a clearly defined subset of direct run-shell interpolation.
- The new workflow is **GitHub-hosted only**, uses contents: read and a
  credential-free immutable checkout. A PR-supplied scanner/workflow is not
  trusted policy until separately reviewed and merged under protected-main
  admission; current unprotected main cannot make this an enforcement claim.
- Do not grant authorization for merge, deployment, queue/SQLite mutation,
  Firefox intervention, VPS execution or workflow dispatch from this report.
- This add-only source slice does not edit other owners' active workflows and
  does not remediate historical incidents #1326 or #1447.

Verification: python3 -m unittest -v tests.test_zcloud_shell_expression_ratchet_20261015
plus focused hosted workflow and the repository's independent full regression.
