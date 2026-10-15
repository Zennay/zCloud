# zCloud shell-expression no-new-debt tripwire (deploy-ops #633)

Standalone GitHub-hosted **advisory source check**; no release or production authority.
The guard compares immutable committed Git trees (main SHA -> candidate SHA) and
refuses **new** direct GitHub event, head-ref, ref-name and workflow input expressions
interpolated into a workflow run-shell command. It treats new YAML run aliases/tags
and incomplete expression fragments as suspicious. Existing risks are inventoried
but not grandfathered if replaced with a *different* unsafe expression; the
comparison uses path, normalized expression and whole run-scalar SHA-256 fingerprints, not
just counts. Moving a legacy expression into another shell command is new risk.
The deliberately conservative flow-mapping tripwire fingerprints inline YAML
`{run: ...}` forms by line and **rejects new flow-style run mappings even when
they look harmless**, because the syntax is not fully parsed. Prefer block-style
steps instead. Quoted run keys using YAML's `\\u0072` or `\\x72`
spelling for the initial `r` are also scanned, so re-encoding the key
does not silently skip the shell-command check. A second unfinished `${{`
after a valid closed expression is flagged; an earlier valid `}}` no
longer hides the incomplete token.

YAML's explicit mapping-key syntax (`- ? run` followed by its value on a
separate `:` line) is also treated as ambiguous and bound to the complete
workflow source hash. This conservative refusal covers run-shell content that
a simple line-oriented scanner cannot parse reliably. Use ordinary
`- run: ...` steps to avoid the review burden.

For legacy run aliases, the guard binds the ambiguity fingerprint to the
entire workflow Git blob. Changing an anchor referenced by `run: *alias`
is therefore not silently grandfathered. This is deliberately conservative:
even a harmless edit elsewhere in a workflow containing such an alias may
require replacing the alias with an explicit shell command.

The source ratchet also treats shell interpolation of `github.ref` and
`github.base_ref` as potentially untrusted branch/ref text. Full GitHub
context serialization such as `${{ toJSON(github) }}` is conservatively denied:
the context can contain event-derived values even if the expression does not
spell out `github.event`. Bracket variants (`github['ref']`) and spaces in the
serialization function are covered. Direct `github.sha` is not marked as
event-derived and remains allowed by this limited tripwire.

Safer patterns put untrusted values into a step env binding and reference
quoted shell variables; still validate the value according to its meaning.
The scanner reads bounded regular UTF-8 Git blobs only, checks sizes before
buffering and refuses symlinked workflow files, drifted/non-ancestor source,
invalid SHAs, missing or incomplete source evidence. CLI exit codes:
0 no new risk, 1 new risk, 2 missing/inconsistent evidence.

## Local composite Action shell coverage

The guard now also scans committed `action.yml` and `action.yaml` files **at
any tree depth** for run-shell interpolations. This closes a source-inventory blind
spot: a workflow can invoke a repository-local composite Action whose `runs.steps`
execute shell code even though the invoking workflow has no `run:` entry.
The check remains conservative and syntactic: it also inspects Action metadata
for non-composite Actions and does not claim execution reachability.

The GitHub-hosted workflow watches Action metadata additions and edits, including
nested `.github/actions/**` and `packages/**` metadata. Ordinary YAML files not
named `action.yml` or `action.yaml` are excluded. The inventory is from immutable
Git tree blobs, rejecting symlinked metadata; it is bounded to 16,384 total tree
entries, 512 selected workflow/Action sources, 1 MiB per source, and 32 MiB
across selected sources. A missing or oversized inventory fails closed.
Legacy composite Action risks are counted as baseline, and changing an unsafe
shell command creates new debt even when the expression itself is unchanged.
Step-level `env:` indirection remains outside this direct run-shell check.
The existing JSON field `candidate_risky_workflows` may now include local
Action metadata paths for backwards compatibility; it is not a list of deployed
or proven-exploitable workflows.

## Read-only workflow risk inventory

A successful or rejected **complete** source audit contains
`baseline_risks`, `candidate_risks`, `new_risks`, and two bounded lists:
`baseline_risky_workflows` and `candidate_risky_workflows`. Each item has
only `path` and `risk_count`; entries are sorted by Git path. Neither
expression source nor shell command text is copied into the machine report.
The inventory is a **lead for independent source review**, not confirmation
that a historical interpolation is exploitable or approved.

The detector additionally handles YAML's explicit `? "\\u0072un"`
run mapping: it conservatively fingerprints the whole workflow whenever an
escaped explicit key might hide the command. Keep shell script steps as
ordinary `- run: ...` fields where possible.

## Limitations and safety boundary

- The hosted workflow separately checks the exact remote `main` and branch
  commit before and after the source scan. Two matching reads detect observed
  drift but **do not constitute an atomic repository snapshot**; no automatic
  release or merge authority results from these checks.
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
