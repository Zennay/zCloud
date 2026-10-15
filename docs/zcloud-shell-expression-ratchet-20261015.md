# zCloud shell-expression no-new-debt tripwire (deploy-ops #633)

Standalone GitHub-hosted **advisory source check**; no release or production authority.
The guard compares immutable committed Git trees (main SHA -> candidate SHA) and
refuses **new** direct GitHub event, head-ref, ref-name and workflow input expressions
interpolated into a workflow run-shell command. It treats new YAML run aliases/tags
and incomplete expression fragments as suspicious. Existing risks are inventoried
but not grandfathered if replaced with a *different* unsafe expression; the
comparison uses path, normalized expression and whole run-scalar SHA-256 fingerprints, not
just counts. Moving a legacy expression into another shell command is new risk.
The deliberately conservative flow-mapping tripwire fingerprints every
inline YAML `{run: ...}` form using the **whole workflow Git blob**, rejecting
any edit to a workflow that still contains a flow-style `run:` key. Previous
line-only evidence could miss a multiline continuation edit, an anchor rewrite,
or relocation of an identical flow command into another job. This strict
fail-closed rule can flag harmless adjacent comment changes; use block-style
steps instead. An entirely unchanged legacy flow workflow remains grandfathered. Quoted run keys using YAML's `\\u0072`, `\\x72`, or eight-digit
`\\U00000072` escaping (also in the `u` and `n` letters) are scanned, so re-encoding the key
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

The source ratchet also treats shell interpolation of `github.ref`,
`github.base_ref`, `github.workflow_ref`, and `github.action_ref` as potentially
untrusted branch/ref text: workflow and action refs may contain branch or tag
names, not only an immutable SHA. Direct `github.workflow_sha` remains permitted
because it is a commit identifier, not the branch text. Full GitHub
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

## YAML double-quoted run escape ambiguity

YAML double-quoted scalar syntax decodes `\xHH`, `\uHHHH` and
`\UHHHHHHHH` before GitHub evaluates a workflow expression. A raw-source
search for `${{ ... }}` can therefore miss `run: "echo \u0024{{ github.event.issue.title }}"`
or an escaped property spelling such as `github.\u0065vent` inside a shell
expression. The ratchet conservatively classifies **new** double-quoted
`run:` scalars with hex/Unicode escapes as ambiguous. This is *not* YAML
semantic decoding, so a harmless Unicode escape in a new quoted shell
command may need rewriting as a block scalar (`run: |`) or an `env:` binding.
The fingerprint includes the raw command and enclosing job-key context, so
moving an existing escaped run scalar does not silently inherit a waiver.
Single-quoted YAML strings and block-style scripts do not decode these escapes
and are not caught by this specific additional ambiguity rule.

## Derived step and matrix output shell trust

Not all attacker-controlled shell input is spelled `github.event` or
`inputs`. Workflow outputs can carry downloaded text, PR metadata or other
untrusted data, and dynamic matrix values can originate from those outputs.
The ratchet therefore refuses **new** direct shell interpolations of
`steps.<step-id>.outputs.*`, `needs.<job-id>.outputs.*` and `matrix.*`,
including bracket syntax for named jobs/steps and hyphenated names.
This is intentionally conservative: some outputs or matrix elements are
immutable and harmless, but the source-only tripwire cannot prove their
upstream origin. Existing historical expressions remain scoped to their
exact original run scalar, job ancestry and step metadata; changing a
command cannot reuse an old waiver.

Boolean/status metadata (`steps.check.outcome`, `needs.check.result`) is
not marked as an output payload by this specific rule. A direct `github.sha`
shell interpolation and passing dynamically derived inputs through a
step `env:` assignment rather than directly interpolating shell syntax
remain outside the new direct-shell rule. Environment values must still
be validated and quoted by the consumer: this is not a general guarantee
of safety for arbitrary shell scripts.

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
The guard now also binds each unsafe `run:` scalar fingerprint to a hash of its
best-effort YAML ancestor mapping keys. Relocating an unchanged unsafe command
from one named job to another creates new debt, while adding an unrelated job
does not. Indentation-derived ancestry is intentionally **not a full YAML
semantic model**: permissions, inherited defaults, matrix routing, and every
possible change to the effective trust context are not proven safe by this test.
The existing JSON field `candidate_risky_workflows` may now include local
Action metadata paths for backwards compatibility; it is not a list of deployed
or proven-exploitable workflows.

## Ordered step scope for risky legacy runs

An unsafe `run:` snippet can become executable without its text changing:
for example, an operator may remove a step's `if: false`, replace its `shell:`,
change the step metadata, or move it later in the list. The guard now binds
each risky scalar to a hash of its enclosing YAML **step item and ordinal**
as well as the ancestor job keys. This blocks reusing a historical waiver
after a same-text move to a different step or materially altered step.
Appending an unrelated safe step *after* the risky step retains the
old proof. This intentionally follows a conservative, whitespace-indented
step-boundary heuristic, not a complete Actions YAML semantic parser.
Changing unrelated metadata **within the risky step** can therefore
produce false positives and requires an independent source review.

## Whole-job YAML aliases and copied shell risk

GitHub Actions can use YAML anchors and aliases to reuse an entire job, not
just environment variables. Adding `copied: *template` can therefore create
another instance of a risky shell command without editing its original
`run:` scalar. The source checker now fingerprints the **entire workflow**
whenever a mapping or list uses a YAML alias value. New, retargeted or
relocated aliases are rejected as structural ambiguity. Existing files
with unchanged alias bindings retain their baseline. This source-level
refusal intentionally does not attempt to decide which alias points at
an exploitable job; even harmless alias edits can require manual review.
Only YAML-like mapping/list alias values are examined by this rule, not
literal asterisks embedded in shell command strings.

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
