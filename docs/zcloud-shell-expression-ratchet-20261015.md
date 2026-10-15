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
Wildcard aggregation (`needs.*.outputs.value` or `steps.*.outputs.value`)
can likewise propagate untrusted downstream outputs, even through
functions such as `join(...)`. This syntax is covered; status-only
wildcards like `needs.*.result` remain outside the new rule.
Serializing the complete `steps` or `needs` context via `toJSON(steps)`
or `toJSON(needs)` can also expose the same derived payloads. The
scanner refuses **new** direct shell interpolation of these serialized
objects. It also treats computed `steps[...]` or `needs[...]` property
selection as ambiguous: a computed selector can resolve to an output
that is not evident from the source. Fixed `steps.audit.outcome` and
`needs.audit.result` metadata are not categorized as untrusted output
payloads under this specific extension.
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

## Workflow `env` context as a shell injection source

A job or workflow `env` mapping may inherit content from external events,
downloaded artifacts or prior output values. Although GitHub expression
interpolation happens before shell execution, directly inserting
`${{ env.PR_TITLE }}` into `run:` can turn untrusted content into executable
shell syntax. The no-new-debt scanner now treats direct `env.*`
interpolation as potentially unsafe, including bracket notation, whitespace
around dot access, computed selectors, and `toJSON(env)` serialization.
This is a conservative trust-boundary rule, **not** proof that an existing
`env` assignment is attacker-controlled. Existing expressions remain
accepted only when their scalar, step metadata and job context are unchanged.

The recommended form uses `env:` to assign an expression and refers to a
properly quoted **shell variable** instead:

```yaml
- name: Process PR title
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: printf '%s\\n' "$PR_TITLE"
```

The check does not reject that safe indirection or ordinary shell expansions.
This rule does **not** make arbitrary use of a shell environment safe:
downstream consumers must quote variables and validate their semantics.
No change here authorizes a production deployment or remediates the
self-hosted runner.

## Configuration variables and secrets in direct shell expressions

The ratchet also rejects **new** direct `vars.*` and `secrets.*`
expressions in `run:` script text. GitHub configuration variables and
secrets are generally managed by privileged operators, but their **values**
may still contain quotes, substitution syntax or shell metacharacters; an
inline expression can therefore change the structure of the script, as
opposed to being passed as data. Directly serializing either entire context
(`toJSON(vars)` / `toJSON(secrets)`) is also classified as suspicious.
The same rule covers bracketed fields and ambiguous computed selectors.

Place such values in a step-level `env:` mapping and reference only
properly quoted shell variables; mask and limit secret exposure, validate
derived values, and never print secret contents as diagnostics. That is
an encoding boundary, **not** automatic authorization for deployments or
arbitrary execution. Existing legacy expressions are reported without being
treated as newly introduced debt unless their shell/body context changes.

## Nested step/job JSON shell interpolation

A direct `toJSON(steps.download)` or `toJSON(needs.build)` may serialize
the object's `outputs` without spelling `.outputs` in the expression.
An untrusted downstream output can then become literal shell script text.
The no-new-debt checker now matches whole named children of `steps`
and `needs` when directly JSON-serialized into `run:`. Quoted bracket
fields and wildcard child selectors are covered. This also closes a gap
where `toJSON(needs['build-test'])` evaded earlier exact-root checks.

Scalar status metadata such as `toJSON(needs.build.result)` or
`toJSON(steps.lint.outcome)` remains outside the new rule. Matching is a
conservative syntax contract, not a complete GitHub Actions expression
interpreter. For potentially untrusted outputs, bind the expression to
a step `env:` property and consume it as a quoted, validated shell value.

## Mutable workflow display title and token context

Unlike the immutable `github.workflow_sha`, `github.workflow` is a
human-readable workflow title. Its spelling comes from workflow source and
may include shell-significant characters. Newly inserting it directly into
`run:` therefore crosses the same expression-to-shell trust boundary as
other non-literal strings. `github['workflow']` is equivalent. Use an
`env:` binding and quoted shell variable when a command needs the title.

`github.token` is a credential-bearing context. Although a token's ordinary
characters may be shell-safe, inline insertion risks needless exposure in
generated script text and command diagnostics. The guard now denies **new**
`github.token` and `github['token']` direct shell substitutions.
The change is a source-level no-new-debt policy, not a guarantee that tokens
are masked at runtime. Keep permissions narrow, avoid printing credentials,
and use an environment variable only when authenticated access is necessary.

## Safe reason categories in the advisory receipt

The JSON evidence contains `baseline_risk_kinds` and
`candidate_risk_kinds`, each a sorted list of `{"kind": "...",
"count": N}` entries. Only **four fixed labels** are ever emitted:
`direct_interpolation`, `dynamic_property_ambiguity`,
`incomplete_expression` and `yaml_structure_ambiguity`. Each category
counts findings in immutable Git blobs; it is **not** a count of confirmed
vulnerabilities or a release authorization.

Reason categories support human-readable backlog triage without copying
the source shell expressions, secret names, tokens, embedded expressions
or full command hashes into a report. The baseline and candidate lists summarize all scanned sources.
A separate `new_risk_kinds` list summarizes **only** findings newly
introduced relative to the immutable base, so a rejected PR can show
which type of debt caused the failure without publishing raw expressions.
A clean candidate emits `new_risk_kinds: []` even when baseline findings
remain. Existing per-workflow summaries still provide only path and total.
The exact input/output path and count snapshots remain available for
independent review. A missing Git object fails closed rather than emitting
an apparently complete advisory.

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

## Which workflow was blocked?

The advisory JSON includes `new_risky_workflows`: a deterministic,
lexicographically sorted list of new-debt paths with a total `risk_count`
and a per-path `risk_kinds` breakdown. It is deliberately separate
from the historical `baseline_risky_workflows` and full-candidate list.
A workflow with unchanged legacy findings contributes **zero** to this
delta list. Each finding's detailed shell expression is never included,
so operators can route remediation to the owning workflow without
putting secret identifiers or attacker-supplied expressions in logs.

A complete report with `new_risks=0` must have
`new_risky_workflows: []`; any nonempty entry is **source debt** and does
not grant release or mutation authority. The output remains advisory
until the independent PR review and protected-main admission gates.

## Tagged and anchored YAML run keys

A mapping key can be prefixed with a YAML scalar tag (`!!str run:`)
or an anchor (`&run_key run:`). A line-oriented scanner looking only
for `run:` at the start of a step can silently skip the associated
shell text. The same ambiguity exists with quoted, Unicode-escaped
and explicit mapping keys such as `? !!str run`.

The source guard now conservatively fingerprints the **entire workflow
blob** whenever a tagged or anchored key spells `run`. Any change to
such a workflow creates new review debt, including a seemingly harmless
comment. Unchanged legacy source is accepted as unchanged evidence.
Tagging unrelated keys such as `name:` does not trigger the new rule.
Rewriting these cases into ordinary `run:` syntax is preferred.

GitHub Actions does not necessarily accept every YAML tag accepted by
generic parsers. This check is intentionally a fail-closed syntax guard,
**not** proof that a tagged key is executable by Actions, nor a substitute
for YAML semantic validation. No release, merge or VPS permission is granted.

## Job metadata with nonstandard YAML indentation

YAML does not require two spaces per indentation level. A source file can
use four-space job identifiers with six-space job metadata (or other
consistent positive indentation). The conservative authority envelope
now derives the job-level indentation from the shallowest key inside the
`jobs:` section and separately derives the metadata-level indentation
from the selected job's own keys. Changes in a different job must not
invalidate an unchanged legacy shell finding.

This is still heuristic: a programmatic YAML parse, owner-reviewed scope
and protected-main checks remain necessary to treat the workflow as a
production candidate. There is **no self-hosted execution** in the
scanner or its tests.

## Quoted YAML execution authority keys

GitHub Actions YAML allows key names to be single- or double-quoted.
The execution-control envelope must therefore recognise `'on':`,
`"permissions":`, `'jobs':` and quoted job identifiers such as
`"deploy":` or `'deploy':`. Otherwise, changing job permissions
or event triggers could raise a legacy shell command's authority
without changing its existing fingerprint.

This control uses bounded source spelling and indentation, not a YAML
parser. A permission change in an **unrelated** quoted job is not new
debt for the untouched risky job. Exact source fingerprints still
require independent review before integration; runtime permissions
and the legitimacy of a workflow trigger must be checked separately.

## Prevent legacy shell debt from silently gaining job authority

A previously recorded unsafe direct interpolation is **not permanently
waived** when its enclosing workflow or job becomes more powerful. The
guard now binds each risky `run:` finding to a bounded, indentation-based
execution-control envelope in addition to the existing step body and
ancestor identity.

Global source controls include event triggers (`on:`), workflow
`permissions`, `env`, `defaults` and `concurrency`. Per-job controls
include `if`, `runs-on`, job `permissions`, `environment`,
`container`, `services`, `strategy`, `env`, `defaults`,
`concurrency` and `timeout-minutes`. A source transition from
read-only to write permissions, from `push` to `pull_request_target`,
or from hosted to self-hosted runner is **new risk debt** when it inherits
an existing direct expression-to-shell hazard, even if the shell text is
byte-identical.

A harmless change to a workflow's `name:` or to an unrelated job's
permissions does not invalidate the waiver for the unchanged risky step.
Metadata is recorded as source only; the scanner **does not infer**
runtime permissions, YAML merge semantics, job execution, or trust based
solely on a source field. Non-standard indentation can be ambiguous and
requires review. This is a no-new-debt guard, never deployment authority.

## Quoted expression delimiters

GitHub Actions expressions may contain the literal text `}}` inside a
single-quoted string, for example `format('}}', inputs.payload)`.
A regex that treats the **first** `}}` as the expression terminator
could ignore all following untrusted arguments and quietly accept an
unsafe direct shell substitution. A legitimate string can also escape
an apostrophe as `''` and contain the same delimiter.

The shell guard now scans expressions with explicit single-quote state,
honours doubled apostrophes and closes only on `}}` **outside** a literal.
An unmatched expression, or another unquoted `${{` before its closer,
is counted as incomplete/ambiguous debt. Literal strings containing
`}}` but **no untrusted property** remain outside the direct-expression
risk classification. This is still lexical source inspection, not
execution, GitHub expression validation, or permission to deploy.

## Human-readable GitHub Actions summary

After successful SHA-bound evidence admission, the hosted job writes a
**Dutch, source-only summary** to its native GitHub Actions step summary.
The view shows historical/candidate/new totals and, for rejected source,
up to 15 newly risky workflow paths with category counts. No extra
third-party Actions, credentials, VPS agents or filesystem writes outside
the hosted job are needed.

Names are treated as untrusted source: control characters, HTML and
Markdown table delimiters are escaped or bounded before rendering.
An oversized list is capped with an explicit omitted-file count.
No expression bodies, secret values, full shell scripts, deploy tokens or
release authorization flags are displayed as authority. A green message
means only that the candidate introduced no new syntactic debt under
this limited scanner, **not** that production or the VPS is safe.

## Exact-identity hosted admission

The hosted read-only job validates the advisory JSON after computing it.
It requires the document's schema, `base_sha` and `head_sha` to exactly
match the two immutable refs checked from GitHub. It also reconciles
baseline/candidate totals with both per-workflow and per-reason counts.
A successful no-new-debt result must contain **empty**
`new_risk_kinds`, `new_risky_workflows`, and `affected_workflows`,
in addition to `new_risks=0` and all authority/mutation flags false.

An incomplete, inconsistent, or replayed report fails the hosted job.
These are two-point freshness/source-integrity checks rather than an
atomic GitHub snapshot or independent release attestation. A green
advisory run cannot authorize merging or touching the VPS.

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
