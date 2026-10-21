# zSentinel · four-lane recovery admission

This is source guidance for the `portfolio-recovery` profile in
`lane_generator.py`. The zSentinel role remains cross-project recovery within
zCloud, not a separate product or permission boundary.

## What is implemented

| Lane | Work chosen from the existing backlog |
| --- | --- |
| `release-integration` | Finish a previously opened product PR on main, when existing exact-head checks, reviews and authorization permit. |
| `product-remediation` | Resolve unowned product-impact issues and real bugs; do not invent QA work. |
| `worker-recovery` | Investigate genuine queue, claim, browser-worker or scheduler blockage. |
| `ci-deploy-recovery` | Investigate CI, runner and deployment problems without bypassing release authority. |

The classifier treats generic ticket words (`issue`, `fix`, `bug`,
`product`) as weak signals in this profile. Specific terms such as
`runner`, `queue` and `merge` determine the lane when present.
This is routing, not proof of ownership or permission to take a task.

## Reservations and collision visibility

- All in-flight queue entries (`claimed`, `running`, `verifying`) reserve
  their own scope, even if multiple entries were accidentally assigned to
  the same lane. Only one owner is *displayed* per lane; hidden active work
  must never cease to occupy its files or capabilities.
- `active_backlog_count` reports how many in-flight entries were observed
  for that lane. `parallel_active_warning=true` means there are two or more;
  it is a **reconciliation signal**, not permission to cancel, reassign,
  duplicate, merge, resume or steal their work.
- Serialized `conflict_scope.capabilities` and `conflict_scope.files` may
  be a string or an array. A single filename is always one filename, not
  an iterable of characters. Existing path/capability overlap checks still
  determine whether a *queued* candidate may use the lane.
- Missing local claims, unseen Notion claims, absent external leases and an
  idle lane are **not evidence of free global ownership**. Independently
  recheck current claims before writing to any PR, queue or VPS runtime.

## Runtime boundaries

The project contract declares a *maximum* of four AI workers, not a
guarantee of four simultaneously executing browser workers. Existing
resource-pool admission, physical RAM/swap/CPU pressure, claim leases and
runner availability can legitimately reduce actual concurrency. Four
configured lanes do not imply four resumed jobs.

No source-only result authorizes production mutations or GitHub merges:
a PR is delivered on `main` only when GitHub confirms `merged=true`
for the exact verified head and the new default-branch SHA is observed.
An approved deploy is a distinct event; a live, recovered worker must be
observed separately. Do not start QA suites, force CI, restart services,
modify broker/security/research state, or reassign protected owner claims
to make these labels turn green.

## Private four-lane operator desk

The concrete read-only product is `scripts/zsentinel_four_lane_desk.py`.
Use the zCloud SQLite source and the **current branch's matching**
`lane_generator.py` when working from a checkout of this PR:

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db
python3 scripts/zsentinel_four_lane_desk.py --db history.db --format json
python3 scripts/zsentinel_four_lane_desk.py --db history.db --format html --output /PRIVATE-OWNER-DIRECTORY/new-report.html
```

JSON/text output intentionally contains no claim keys, owner identities,
queue identifiers, raw titles, file paths or capabilities. The report is an
offline, single-file, Dutch four-card interface with restrictive CSP and no
external assets. HTML export refuses existing files, symlinks and non-private
or other-user parent directories; an output is created 0600 only on explicit
request. All other modes perform **no file writes**.

The SQLite connection is `mode=ro` with `PRAGMA query_only=ON` and a
single transactional snapshot. Both queue entries and local claim rows are
bounded independently to a configurable maximum of 500. Above that bound,
coverage becomes partial: **never** interpret missing rows as idle workers.
Unparseable lease records remain ambiguous. The desk records observed
queue `claimed/running/verifying` expiry as either expired or unknown,
but does **not** reclaim or alter either category. It keeps queue occupancy
distinct from the active `task_claims` table. Absence of local rows says
nothing about Notion and other external owners.

Only up to 64 in-flight queue records receive an explicit pairwise
cross-lane capability/file overlap inspection in the private desk. More than
64 produces an *incomplete* comparison rather than a false zero. The
underlying lane generator continues to reserve **every** active task scope
when assessing new candidates, independently from this optional view.
Existing in-flight collisions trigger an advisory warning, never a forced
reallocation.

### Actual direct VPS observation (20 October)

Against a read-only current `history.db` snapshot, source pinned to
`bed05d016965e1e539afad5159384b0aba6ee517`, the desk observed
one claimed PR-integration item, two claimed worker-recovery items and one
queued CI/deploy item; product remediation was idle in this **local** view.
No zSentinel rows existed in the observed local `task_claims` table.
The view correctly warned about multiple worker-recovery queue entries
and about claimed work without locally observed leases. These findings
are **not** a release/worker recovery claim, global source completeness,
permission to take a Notion claim, or evidence that an external owner
is absent. Source syntax and this bounded, real-data functional invocation
were inspected without starting any QA suite.

### Delivery boundaries

Current code in a GitHub PR is not `main`. Exact-head mandatory CI,
independent reviews, branch protection and release authority remain
required. An operator must separately verify GitHub `merged=true`,
the newly observed default-branch SHA and authorized live activation
before describing this feature as delivered or live.

## Portfolio-wide urgency, without assuming ownership

The same private desk now includes an explicit cross-project urgency section
for 13 configured *real* projects. This is a **local portfolio queue census**,
not a remote GitHub issue crawl, a new zSentinel project or an automated
scheduler. The zSentinel recovery role is shown only in its four-lane cards
and deliberately excluded from the external-product tally.

- Only explicit P0/P1 `queued/claimed/running/verifying` rows are counted.
- `queued_p0/queued_p1` show waiting items; `in_flight_*` are kept separate.
- `eligible_queued_*` counts only rows with the local `eligible=1`
  column, and is an *admission hint*, not proof that any owner/claim,
  resource pool, external Notion task or GitHub branch is free.
- Counts are independently grouped within the **same read-only SQLite
  transaction** as the four lane/lease evidence. The SQL aggregate is
  capped at 128 returned groups plus one to flag incomplete source
  coverage. Unrecognized project IDs are not printed; they flag partial
  coverage rather than being silently dropped.
- The Dutch offline HTML has a responsive urgency table with these
  counts, including local eligibility, alongside the four lane cards.
  Raw queue titles, actor identities, credentials and source URLs are
  never embedded in exported JSON/text/HTML.

Direct read-only VPS observation from exact source
`21115ef97bec6fb1a95ab27dc62aa6a611031afb`:
LightUp has six locally eligible waiting P0 queue items and one P0
in flight; zCloud has two locally eligible waiting P0 queue items;
FTMO has one P0 plus two P1 locally eligible waiting items; HaxLab
has one waiting P0 with **eligible=0**; zSSH has 31 locally eligible
waiting P1 entries; Supa has one locally eligible waiting P1 and
another P1 in flight. These are queue observations, not real-time
GitHub issue closures, active independent claims, completion forecasts
or runner recovery proof. Even a locally eligible item must pass
existing external gates before another worker can take it.

The source was directly parsed and its bounded read-only text view
invoked using an exact pinned GitHub commit on the connected VPS.
No QA, regression suite, GitHub workflow retrigger, queue mutation,
runner action, service change or release deploy was performed.

### Per-row limits for busy VPS environments

The desk also bounds individual SQLite TEXT fields, not only its row counts.
Task titles are sampled to at most 512 characters, completion criteria to
3,072 characters and queue/claim metadata to 8,192 characters per row.
Any oversize queue cell marks the local source *incomplete* and requires
owner reconciliation before interpreting any lane. Oversize claim
metadata is treated as ambiguous rather than silently admitted as a free
claim. This is an operator observation guard, not a change to runtime
SQLite rows or the portfolio dispatch policy.

The exact `89f5d63046e125d5467c2794d9bfac1bf28c66bf` source was
directly parsed and its real read-only VPS CLI path inspected; no suites,
services, SQLite writes, runners or release gates were touched.

## Owner-safe recovery cards and single-page handoff

The desk now imports the **pure, no-side-effect** source
`scripts/zsentinel_recovery_actions.py`. It derives at most seven bounded
three-line action cards from the *same* read-only queue/claim snapshot:

1. Problem: an observed, source-grounded missing lease, missing write scope,
   parallel ownership risk, or local P0 waiting candidate.
2. Next step: owner verification or independent issue/PR inspection.
3. Status: a review/hold state such as `HOLD_PARALLEL_ADMISSION`,
   **never** permission to mutate or override existing claims.

Cards appear in the text, JSON and offline Dutch HTML desk. A separate
owner-private Markdown handoff is created only by an explicit command:

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db \
  --format markdown --output /EXISTING-PRIVATE-DIRECTORY/NEW-handoff.md
```

Output must be a previously nonexistent `.md` file in an existing private
directory owned by the caller. The same create-only, symlink-refusing 0600
writer is used for HTML and Markdown. Nothing is written by the default
read-only text or JSON modes. The handoff does not export queue IDs, actor
identities, raw task titles or privileged metadata. It includes the sampled
local observation time and explicitly states that GitHub/Notion claims and
release authority were not independently verified.

### Critical four-lane admission boundary: missing actual file scopes

The live 20 October 2026 SQLite read-only observation found **three** active
zSentinel queue items with **no declared conflict_scope metadata**, despite
a separate local task_claims table containing zero zSentinel leases. A
synthetic lane capability cannot prove that two workers will not edit the
same file in different lanes. Previously the four-lane generator might
label a separate queued lane free because there was no file overlap to
compute. This was not safe evidence of non-conflict.

Source commit [748a89a](https://github.com/Zennay/zCloud/commit/748a89a6d120abe5824f294ab2907d30bb54646b)
now **blocks new queued admission for the zSentinel portfolio-recovery
profile only**, whenever active queue tasks have no explicit
file/capability scope. Existing task owners, current worker activity and
the other 13 project lane profiles are unchanged. The hold is
`active_write_scope_not_declared`. It is not an automatic release,
queue mutation or demand that the existing worker be interrupted.

Source commit [cd70620](https://github.com/Zennay/zCloud/commit/cd706209fa9fc8c9674fcb7bb4741b75055d5e83)
and the bounded action-card code explicitly flag incomplete active scope
coverage rather than displaying an unsupported zero-collision conclusion.
The only way to resolve this dependency is for the actual current task
owners to provide authorized, verified write scopes or complete their
owned tasks. Do not infer free ownership from missing local leases.

Direct pinned functional source inspection on the connected VPS at
[4533253](https://github.com/Zennay/zCloud/commit/45332538ed56d277e643c94af7009444ec239049)
read the existing SQLite source with `query_only=ON`, produced source-only
HTML/Markdown in memory and reported the CI/deploy lane as **blocked**
by an unknown active write scope. Source parse succeeded; no QA suite,
claim, CI rerun, runner, systemd, DB writer, service or production release
was executed.

**Integration truth:** all these additions are committed to a still-open
PR branch. Exact-head required CI, independent review, branch protection
and release ownership remain mandatory. PR source is not main or live
deployment, and live worker recovery remains unverified.

### CLI import fix for normal operator execution

At commit [c652aa6](https://github.com/Zennay/zCloud/commit/c652aa6768dfed3db288f345e2ddcba1bcea3595),
the direct script entrypoint resolves the canonical repository root before
importing its shared lane generator, and imports the source action adapter
from the adjacent `scripts` package. This is necessary because
`python3 scripts/zsentinel_four_lane_desk.py` ordinarily puts the script
directory, rather than the repository root, first on Python's module path.
It supports the documented operator command when the **matching PR
sources are available together**. Running an older main checkout is not
proof this branch's functionality is deployed.

Do not copy the scripts individually to a VPS production root: use a
source-matched, reviewed checkout after existing branch/CI/release gates
permit integration. Until then, exact-source read-only inspection is
distinct from installed/live product behavior.

## 20 October · Complete owner-aware write-admission boundary

The lane generator protects **two different sources of active ownership**:
in-flight `portfolio_queue` rows and current `task_claims` rows. A queue
row with an absent/invalid `conflict_scope` can no longer be ignored, and a
current task claim with no usable file/capability scope is no longer silently
dropped from collision admission either. Both require the existing owner to
reconcile scope before any *new* parallel writer can be selected.

The **new queued-task boundary** is intentionally less disruptive than a
total shutdown. A queued zSentinel task lacking a declared file/capability
scope can be selected only as the **single serialized candidate** if the
observed project has no other active task, no current scoped or unscoped
claim, and no already selected writer. Subsequent candidates, whether
scoped or unscoped, cannot share that wave of admission. Existing owners
remain untouched. Explicitly scoped tasks may proceed on independent lanes
when their overlap check is clear. The feature only changes the zSentinel
`portfolio-recovery` lane profile; other 13 real projects are unchanged.

This is a **routing limit**, not an authorization token. The existing
server's queue eligibility, external Notion leases, writer reservations,
resource pool admission, GitHub exact-head checks, independent reviews,
branch protection and release/production gates remain authoritative.
Do not infer a free owner from an `idle` lane card: the dashboard now
separately exposes `new_write_admission_held`, and the action router
suppresses independent-work suggestions whenever source completeness,
scope declarations or ownership reconciliation are unresolved.

On the latest direct read-only VPS observation (exact source
[5cb008e](https://github.com/Zennay/zCloud/commit/5cb008e9ef6906299ef58d152fea0b20e14bfc31)),
three zSentinel in-flight tasks lacked explicit write scopes, while the
sampled local task_claims table had zero zSentinel leases. The CI/deploy
lane remained blocked, and the product lane was explicitly shown as idle
**with new writer admission held**. No task was restarted, stopped,
claimed, dispatched or deployed. Source only, not live-installed.

### Reconciliation and follow-on implementation

The actual runtime owner should separately reconcile current queue and
claim epochs against Notion before changing an existing scope. Do not
silently edit ownership metadata to make warnings vanish. In particular,
the current `server.py::_portfolio_lane_snapshot_locked` queries only
`eligible=1` rows in its own lane input. If an in-flight row becomes
`eligible=0` while still `claimed/running/verifying`, the planner could
miss that active writer. The live read-only snapshot during this run had
**zero** such active-but-ineligible rows, so this is a latent source
integration risk, not a claimed live outage. This server path overlaps
the separately owned runtime PR #1571 and has **not** been modified or
taken over in this branch; reconcile with its current owner when ready.

### Same-source in-flight eligibility coverage

The read-only desk now samples `eligible` on every observed
`claimed/running/verifying` zSentinel queue row and records
`runtime_active_filtered_by_eligibility` when it is not exactly integer
`1`. This is separate from whether a task is queued, whether its lease
is known, and whether its file scope is declared. When nonzero, the
offline HTML, text, JSON, Markdown action cards and completeness flags
surface an explicit `HOLD_RUNTIME_SOURCE_GAP`, because the separately
owned `server.py` planner currently applies `eligible=1` even to active
tasks. No automatic queue mutation or task reinstatement is authorized
by this observation.

Read-only current VPS direct-source inspection at exact
[`b2c13aa`](https://github.com/Zennay/zCloud/commit/b2c13aaebb4b62f27163581fc63ea6f9dd54a253)
observed **zero** in-flight zSentinel rows with `eligible!=1`, and
**three** in-flight zSentinel rows without declared write scope.
Accordingly the source-gap warning did **not** fabricate a live outage,
while the existing scope-ownership HOLD continued to prevent new parallel
admission. The server implementation path remains under the independent
owner's [PR #1571](https://github.com/Zennay/zCloud/pull/1571),
with a specific owner handoff; no conflicting server source files were
modified by this PR.

## Optional local GitHub runner presence desk

Source addition: `scripts/zsentinel_runner_presence.py`, loaded only when
the operator explicitly selects `--runner-processes`. The operator command
from a source-matched repository checkout is:

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db --runner-processes
python3 scripts/zsentinel_four_lane_desk.py --db history.db --runner-processes --format json
python3 scripts/zsentinel_four_lane_desk.py --db history.db --runner-processes \
  --format html --output /OWNER-PRIVATE-DIRECTORY/NEW-runner-handoff.html
```

This is a **read-only** limited traversal of local `/proc`, not an
Actions API query, SSH control path, status-changing script or authenticated
runner endpoint. Only recognized `Runner.Listener` and `Runner.Worker`
processes under known installation folders are counted. It never emits
PID, raw paths, CLI arguments, env variables, tokens or credentials. No
running jobs are interrupted and no service is restarted.

Some VPS containers deny `/proc/PID/exe` even to an unprivileged
operator. The scanner therefore offers a bounded fallback: inspect the
kernel process name **first**, then only the executable argument zero
of the matching process. This produces the visibly weaker evidence label
`PROCESS_ARGV0_SEEN_NOT_VERIFIED`. Missing processes in an incomplete or
permission-limited procfs scan must never be reported as an established
runner outage. Raw command lines and executable paths are never exported.

The runner panel appears in opt-in CLI text, private offline HTML,
Markdown handoff and JSON. A companion read-only action card explains
that finding a listener **cannot prove** that GitHub assigns jobs. In
particular, an existing `Runner.Worker` protects in-flight work from
blanket service restarts or queue drains. The scanner neither queries nor
claims to verify runner registration, permissions, labels, GH Actions
`runner_id`, exact-head CI or successful deployment.

### Direct bounded VPS observation · 20 October

Exact source
[`ab88f887`](https://github.com/Zennay/zCloud/commit/ab88f8871f973ec339110aefb8b11438fc67c5bc)
was fetched at its fixed SHA, parsed, then run **in memory** through the
existing connected zSSH observation channel against read-only
`history.db` and procfs. The private desk observed zCloud, Supa, FTMO,
Fuckball, RaiseAI, zSSH and Elmo listener processes with argv0-only
evidence. At the sampled instant it also observed live local
`Runner.Worker` processes in zCloud and Supa; a subsequent observation
may differ. Procfs coverage remained partial due OS permissions.
GitHub Actions registration and assignment remained
`NOT_OBSERVED`, `restart_authorized=false`. No system mutation,
runner restart, GH Actions dispatch, QA or release occurred.

An **independent GitHub Actions read-only observation** on this branch's
then-current commit found hosted jobs still queued (including the
CPU queue storm offline verification and regression smoke jobs). The
owner must use existing GitHub Jobs receipts to establish actual
`runner_id>0` and compare mandatory exact-head workflow outcomes; a
local listener process is not that proof. The account billing/spending
platform gate described in zCloud issue #1523 and the separately tracked
runner-route issue #1326 remain distinct and cannot be bypassed here.

### Worker-only waarneming · 20 oktober

Een Runner.Worker kan in dezelfde lokale processnapshot zichtbaar zijn
terwijl geen Runner.Listener waargenomen is. Dat betekent **niet** dat er
helemaal geen GitHub-runnerproces is gevonden. De optionele procesbron maakt
nu dit onderscheid expliciet als `WORKER_SEEN_LISTENER_NOT_OBSERVED`.
Wanneer een worker alleen via kernel-comm plus argv0 wordt herkend, telt
dit niet ten onrechte als een zwak geverifieerde listener: de scanner houdt
beide betrouwbaarheidsaantallen apart.

De herstelagenda toont dan
`ACTIVE_WORKER_PROTECTED_LISTENER_UNKNOWN` in plaats van een generiek
afwezigheidsbericht. De eigenaar moet de listener en GitHub-toewijzing
onafhankelijk inspecteren **zonder de waargenomen job te onderbreken**.
Ook bij volledige lokale procfs-dekking zijn GitHub-registratie,
runner-labels, claimrechten en CI-status niet bewezen. Het is een read-only
productcorrectie op deze bestaande branch; geen herstelactie, merge of
productie-activatie.

### Veilige prioriteit in de begrensde herstelagenda

De privé herstelbalie laat maximaal negen advieskaarten tegelijk zien.
Voorheen konden vroeg toegevoegde, minder urgente kaarten de plaats van
een later waargenomen actieve Runner.Worker innemen. Bovendien stopte de
P0-issuekaartgenerator wanneer de lijst al negen kaarten telde, nog vóór
een betekenisvolle prioriteitsselectie.

De balie bepaalt nu eerst de kleine begrensde kandidatenlijst en
selecteert pas daarna maximaal negen kaarten in vaste volgorde:
**bestaande jobs beschermen**, echte urgenties en bron-/claim-HOLDs,
brongebonden lokale P0-productissues, overige menselijke reviews, en
pas daarna informatie over een vrije lane. De onderliggende aparte
queue- en runner-tabellen blijven zichtbaar, ook wanneer niet elke
advieskaart in de shortlist past.

Dit wijzigt alleen het getoonde advies. Geen kaart verleent
claimrechten, toestemming voor runnerherstart, CI-dispatch, merge of
deploy en geen job, runtimeproces of lokale SQLite-rij is gewijzigd.

### In-flight queueclaims: brongebonden start én verval

De read-only vier-lane-herstelbalie controleert nu ook de **onderlinge
volgorde** van de bestaande `claimed_at` en `claim_expires` bij lopende
(`claimed/running/verifying`) zSentinel-taken. Een vervaldatum in de
toekomst is niet voldoende bewijs van een betrouwbare actieve lease wanneer
de claimstart ontbreekt, ongeldig is, in de toekomst ligt, of wanneer het
vervalmoment niet ná de start ligt.

De balie telt dit afzonderlijk als
`inconsistent_in_flight_queue_claim_windows_observed`; de globale
`source_complete` wordt onwaar, `ownership_reconciliation_required`
waar en een urgente `HOLD_CLAIM_TIME_RECONCILIATION`-kaart vraagt
de actuele claimeigenaar om beide tijdstippen te verifiëren. Het aantal
verschijnt in de Nederlandse console, privé offline HTML, JSON en privé
Markdown-overdracht. Bestaande claims blijven intact: geen vervaltijd,
queue-row, actieve worker, Notion-claim, GitHub job of runtimeconfiguratie
wordt gewijzigd. De integratie is alleen broncode in de bestaande PR.

### Bestaande writer beschermen bij een onbetrouwbare actieve lease

De werkelijk aangeroepen pure `lane_generator.py` voor profiel
`portfolio-recovery` bewaart nu alle bestaande `claimed/running/verifying`
taken als huidig bezet werk, maar **laat geen nieuwe queued schrijver toe**
wanneer een actieve taak zijn begin- en vervaltijd niet kan bewijzen:
ontbrekend/onjuist tijdformaat, toekomstige start, verval vóór of gelijk aan
start, of een inmiddels verlopen vervaltermijn. Het resultaat krijgt
`active_queue_lease_untrusted` en
`untrusted_active_lease_count`; geen bestaande taak wordt geannuleerd
of teruggezet. Overige projectprofielen behouden hun bestaande toelating.

De read-only privé herstelbalie neemt dit concrete schedulerbesluit over,
inclusief `new_write_admission_held` op betrokken lanes,
`untrusted_active_leases_observed` in JSON en menselijke HTML/tekst/
Markdown, plus de urgente kaart `HOLD_NEW_RECOVERY_WRITERS`.
De claimeigenaar moet zijn werk en de werkelijke lease repareren voordat
een tweede schrijver wordt toegelaten. De afwezigheid van een lokale claim
bewijst niet dat Notion of een andere worker vrij is.

Directe, alleen-lezen SQLite-observatie via de verbonden VPS op 20 oktober
vond bij de drie geobserveerde huidige in-flight zSentinel-taken **drie
betrouwbare, niet-verlopen bronvensters**, nul verlopen en nul foutieve
datums. Dit is een bronmoment, geen release- of claimtoestemming.
De aangepaste schedulerbron is uitsluitend op deze open PR beschikbaar;
de VPS-database, lopende jobs, service en main-branch blijven onaangeraakt.

### Remaining release gate

The feature is source-only in the open existing PR #1572. Do not call it
merged, deployed, live-installed or a resumed worker without the actual
GitHub merge receipt, exact new default-branch SHA, authorized release
action and live-state confirmation. The current source may be inspected
read-only in memory without changing the VPS working tree.


## 20 October product-only admission · no new QA assignments

The `portfolio-recovery` planner now **refuses to admit queued,
standalone QA, regression-test, test-suite and quality-validation packages**
as new zSentinel recovery work. Explicit QA lane metadata and common
QA-leading titles are both recognized; a real implementation/bug-fix ticket
is not excluded just because its acceptance description mentions tests.
The filter applies only to `queued` zSentinel work, not to other projects.

Already `claimed`, `running` or `verifying` entries keep their existing
reservation and identity even if their title describes QA. Neither the
filter nor the four-lane desk cancels, releases, reassigns, edits or
supersedes those owners. Mandatory independent GitHub CI and protected-main
review remain unchanged.

An otherwise empty lane containing only forbidden QA work is reported as
`blocked`, never `idle` or `queued`. Every lane now provides an aggregate
`deferred_qa_packages` count (no raw task names/IDs), shown in JSON, text,
Markdown handoff and the private offline HTML cards. This is an operator
exclusion signal, **not** authority to mutate the legacy backlog. A human
queue owner decides whether to archive or reroute any pre-existing QA item.

Source is delivered only on PR #1572 until GitHub confirms protected-main
merge at the exact checked head. This change writes no production SQLite,
runner state, scheduler, browser or GitHub Actions workflows.

The bounded advisory recovery-action feed also emits `NO_NEW_QA_WORK_ADMISSION` when the desk observes deferred QA packages. The action explicitly returns these legacy items to their existing queue owner and keeps attention on real product issues and PR integration. It does not start, remove, relabel, reprioritize or claim any queued item. If higher-severity owner/runner risks fill the existing nine-card ceiling, the per-lane exclusion count remains independently visible.


## 20 October · operator admission decision per lane

The same local SQLite snapshot now supplies each of the four recovery
lanes with an explicit **owner-safe decision**, a Dutch reason and next
operator step in JSON, text, offline HTML and private Markdown. Examples:

| Code | Meaning |
| --- | --- |
| `ACTIVE_OWNER_PROTECTED` | Already in flight; retain the owner. |
| `HOLD_EXPIRED_LOCAL_CLAIM` | At least one expired local claim lease: reconcile with its owner before any new writer. |
| `HOLD_SOURCE_RECONCILIATION` | Queue, claim or scope source incomplete. |
| `HOLD_ACTIVE_SCOPE_CONFLICT` | In-flight writers have overlapping explicit scopes. |
| `HOLD_EXISTING_CLAIM_OR_SCOPE` | Another existing scope or serial lane blocks new work. |
| `HOLD_QA_PRODUCT_POLICY` | Queued QA tasks were excluded from new recovery admission. |
| `CANDIDATE_EXTERNAL_GATES` | One local queued candidate is observed but still requires external GitHub, Notion, owner and resource gates. |
| `NO_LOCAL_CANDIDATE` | No candidate is observed, which never implies global ownership is free. |

The decision is **advice, not an authorization token**. Active work is
protected even when its claim clock cannot be independently confirmed.
An expired local `task_claims` lease is not silently interpreted as an
unowned queue task; the desk and action feed surface a human
`HOLD_EXPIRED_LOCAL_CLAIM` follow-up. This also applies to otherwise
empty lanes. The desk never renews, releases, restarts, reassigns or
mutates those records. Operator cards continue to hide task IDs, raw titles,
credentials, actor identities and file paths. GitHub and Notion live sources
are not observed by this local SQLite snapshot, so even a clean card cannot
authorize merge or dispatch.

This implementation remains on existing product PR #1572 pending
independent exact-head CI and review, protected-main merge and separate
authorized live activation. No QA suite was added or run.


### Integrated operator summary and missing active owner evidence

A further production-source fix covers the situation where active
`portfolio_queue` work exists but no corresponding local
`task_claims` lease is observed. The existing `claimed_without_local_lease_observation`
signal now also prevents unrelated lanes from advertising an admissible
candidate: `HOLD_ACTIVE_OWNER_UNVERIFIED` requires a reconciliation with
the current worker and Notion/queue owner. An empty lane does **not**
override that uncertainty.

All four cards provide a stable `admission.code`, Dutch label and
`next_step`, plus an aggregate `admission_summary` in the same source
packet. The status line in HTML, text and private Markdown distinguishes
active owners protected, owners/source on hold, locally identified but
externally unapproved candidates, and absence of a local candidate. This
is purposefully not permission to claim, dispatch, merge or deploy.
The current read-only source preserves existing queue and claim records.

Updated source commit: [8a77d3a](https://github.com/Zennay/zCloud/commit/8a77d3aac00710bdfdbbd7b36a5bd78366d78bd6).
Still not released until exact-head protected-main review/CI and
separate authorized deploy proof.


## 20 October · actual scope declaration is mandatory for parallel writers

A real product-integrity risk was fixed in the existing planner: ordinary
scope normalization converts arbitrary JSON values to strings. With
`portfolio-recovery`, a malformed entry such as `null`, a wildcard
(`scripts/*.py`) or an absolute/path-traversal string previously risked
being treated as a valid explicit scope. A single syntactically valid
capability alongside an invalid file could also hide the missing file
boundary. The zSentinel-only strict parser now rejects the **entire**
declaration unless every value is a bounded, explicit canonical string.

For **cross-project recovery code writers** an owner declaration must
include at least one concrete **relative repository file/directory path**;
capability-only labels (including generated `execution_lane.scope`) are
never accepted as proof that unrelated lanes can edit safely. Files cannot
use wildcards, absolute paths, `..`, backslashes or empty segments.
Invalid declarations count as ambiguous, exactly like missing ones: active
work retains its original owner and blocks new parallel admission. A
queued task without an acceptable declaration stays restricted to the
existing serial-only pathway; there is no automatic owner-claim repair.

An example of an *owner-provided* declaration, **not** a queue patch:

```json
{
  "conflict_scope": {
    "files": ["scripts/zsentinel_four_lane_desk.py"],
    "capabilities": ["zsentinel:operator-desk"]
  }
}
```

The real owner must replace this illustrative file with **every actual
file/directory intended for writes**, check overlap with other open PRs
and record it through the existing authorized queue process. No script
may synthesize that declaration from a lane label or push it into the
production SQLite. The connected live queue currently has three
zSentinel tasks with generated `execution_lane.scope` but without such
explicit `conflict_scope` declarations; those tasks are **not** assumed
free or safe to parallelize.

The read-only operator desk now reports aggregate missing versus malformed
or empty scope declarations, and a human `HOLD_PARALLEL_ADMISSION` action
specifying what the current owners need to supply. It never returns raw
queue identities, owner IDs or file names. The strict policy is limited
to zSentinel's `portfolio-recovery` profile and leaves other project's
preexisting lane logic unchanged.

Source: [aab2ecfa](https://github.com/Zennay/zCloud/commit/aab2ecfa88fa9688d4cfb399777d9482a47617d5)
on existing PR #1572. It is **not** merged or deployed until ordinary
independent review, exact-head mandatory CI, protected-main merge and
authorized live verification complete.


### Current read-only observation and correct owner counts

The exact pinned source was evaluated against an existing `mode=ro` /
`query_only=ON` `history.db` snapshot using the connected zSSH channel.
At that moment, the local queue contained **three claimed and one queued**
zSentinel tasks, and all three active tasks had **missing real file-scope
declarations**. The new source conservatively classified two derived lanes
as protecting active work and two as held pending owner verification.
That is **three active task reservations in two lanes**, *not two owners*.

The operator decision summary now carries `active_task_reservations`
and `parallel_active_reservations` separately from the number of
`active_owner_protected` lanes. Text, private HTML, Markdown and JSON
no longer equate a displayed lane card with an individual owner's
reservation. No task was released, changed, reassigned, merged or started.

This evidence is a bounded **local** observation only. It is not a
Notion lease reconciliation or GitHub source-of-truth update; it cannot
authorize new parallel workers. Mandatory exact-head CI and independent
reviews are still external release gates.


## 20 October · legacy profile migration cannot grant new parallel workers

A current **read-only** inspection of the connected VPS checkout found
`project-contracts.json` declaring `zsentinel.lane_profile=platform`
and `ai_worker_cap=2`; the separate `worker-modes.json` entry had no
explicit lane profile. All three in-flight SQLite queue items carry
`execution_lane.profile=product` rather than the **proposed**
`portfolio-recovery` profile in this GitHub PR. Those two local sources
do **not** prove which exact configuration the running service has
loaded; no live deployed four-lane admission is asserted.

Before this fix, the proposed four-lane planner reclassified those
legacy active tasks from their titles. Even if their missing file
scopes were later repaired, their original profile/claim provenance
still would **not** be evidence that the active workers had safely
migrated into the new lane model. The planner now separately tracks
`active_profile_mismatch_count` for legacy or missing active
`execution_lane.profile` and prevents **new queued admission** while
such claims exist. Existing task slots, owners and active work are not
reclaimed, restarted, relabeled or changed.

The SQLite-only operator desk exposes
`active_legacy_profile_claims_observed` in its bounded aggregate
packet. Unoccupied lanes show `HOLD_LEGACY_PROFILE_MIGRATION`
rather than treating the proposed profile as deployed. The prioritized
human action explains that the authorized runtime owner must reconcile
actual loaded configuration, original worker claims and file ownership
*before* any profile migration. `deployed_four_lane_profile` remains
`NOT_VERIFIED` until separately established by the runtime owner.

This is not a new process gate: it preserves an existing ownership
boundary that the in-flight legacy claims already occupy. Reconciliation
requires existing independent review, mandatory exact-head CI,
protected-main merge and an authorized release. The code here has
no authority to edit live contracts, worker modes, claim leases,
queue rows, browser sessions or services.


### Local contract read model, without assuming deployment

The existing four-lane desk can also inspect the local
`project-contracts.json` beside the selected read-only SQLite file, or
an explicitly selected `--contracts-file`. The new read model accepts only
a regular, non-symlink UTF-8 JSON file up to 128 KiB. It exports **only**
a whitelisted zSentinel profile, bounded worker cap, local observation
state and match/mismatch status; no credentials, full file content or
filesystem path enters operator output. Missing, invalid or inaccessible
files remain `NOT_OBSERVED` or `UNTRUSTED_SOURCE`.

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db --format json
python3 scripts/zsentinel_four_lane_desk.py --db history.db --contracts-file /AUTHORIZED/READ-ONLY/project-contracts.json
```

If that verified **local file** disagrees with the proposed
`portfolio-recovery` profile, prospective four-lane writer admission is
held with `HOLD_LOCAL_CONTRACT_PROFILE_MISMATCH`; the current owner
must compare the loaded runtime configuration, active queue leases and
existing file claims. The read-only file is **not** evidence of which
configuration the service has actually loaded. The four-lane
`deployed_four_lane_profile` claim remains `NOT_VERIFIED`.
An owner-action card makes the discrepancy visible without touching
production server, SQLite, Notion, browser, runner or billing state.


## 21 oktober · P0 én P1 als brongebonden herstel-inbox

De vierbaan-herstelbalie ondersteunt nu een echte **productgerichte
P0/P1-vervolgkeuze** in de bestaande operatoractiekaarten. Eerder werden
uitsluitend projecten met wachtende P0-items opgenomen; als er alleen
P1-issues wachtten, bleef het productwerk onzichtbaar in de korte
herstelactielijst. De bestaande gescheiden portfolio-aggregaten zijn
ongewijzigd.

De adapter leest maximaal dertien canonieke lokale projectaggregaten,
ordent ze stabiel op waargenomen aantallen en gebruikt lokale eligibility
uitsluitend als *sorteerhint*. Twee P0-projecten gaan voor; er is daarnaast
ruimte voor een apart P1-project. Zonder P0 kunnen maximaal drie
P1-projecten verschijnen. Elke kaart toont wachtend, lokaal eligible
en al in behandeling **afzonderlijk**, plus de expliciete opdracht om
eerst bestaande product-PRs, GitHub-issues en actuele claims te bekijken.
Een P1-project op dezelfde naam als een reeds gekozen P0-project maakt
geen dubbele projectkaart. De afzonderlijke kaart wordt niet gebruikt om
een nieuwe claim toe te wijzen.

Deze kaarten blijven menselijke suggesties met
`PR_AND_OWNER_GATE_REQUIRED`; ze staan *onder* urgente bron-,
eigenaars- en lease-HOLDs in het bestaande maximum van negen actiekaarten.
Bij hoge druk mogen veiligheidswaarschuwingen dus voorrang hebben.
Dezelfde productinformatie is beschikbaar in tekst, private Markdown,
JSON en offline HTML via de bestaande uitvoerfuncties; geen nieuwe
endpoints, providerverzoeken of background jobs.

Code: commits
[b489837](https://github.com/Zennay/zCloud/commit/b489837983e0274c53054c924d9c41bf1eaa4a21)
en
[b68a744](https://github.com/Zennay/zCloud/commit/b68a744f27aea9b0b3d2bbc53cd7bb239a0cdef9)
op dezelfde open [PR #1572](https://github.com/Zennay/zCloud/pull/1572).
Geen QA-suite of handmatige CI gestart en geen queue, claim, runner,
dienst, notificatie of andere productiebron gewijzigd. De bronwijziging
is **niet** op main of live zolang exacte-head verplichte CI,
onafhankelijke review, branchbescherming en eigenaarsautorisatie niet
zijn afgerond.


### Eén klik naar bestaande GitHub-bronnen, zonder eigenaarschap aan te nemen

De private offline HTML-tabel en Markdown-overdracht bieden nu per
geobserveerd portfolio-project **Open PRs** en **Open issues**.
Een vaste allowlist bevat uitsluitend de dertien op GitHub bevestigde
bestaande repositories; namen of URLs uit wachtrijtitels en metadata
worden nooit als linkdoel gebruikt. Een niet-herkend project blijft
gewone tekst zonder link. Links openen de huidige GitHub-inbox in een
apart tabblad met `noopener noreferrer`, zonder browser- of
serverbackground-verzoeken vanuit het offline rapport.

Deze links **halen geen actuele GitHub-status op**. De aantallen naast
de links blijven alleen de read-only SQLite-waarneming; de gebruiker
moet in GitHub zelf de exacte open PR, SHA, CI, review, branch protection
en eigenaar vaststellen. De kopieerbare Markdown geeft dezelfde twee
navigatie-ingangen. Dit is een direct bruikbare merge-first
operatorfunctie, geen toestemming om een PR te claimen, mergen of
deployen.

Broncommit:
[cf2db868](https://github.com/Zennay/zCloud/commit/cf2db86886d355d3048536078596840cfa90b2f0).
Nog steeds onderdeel van **dezelfde open PR #1572**; geen live
installatie of onafhankelijke release-acceptatie verondersteld.


## 21 oktober · Lokale CPU-/RAM-/swapdruk zonder ongeautoriseerde workeractie

Het vierbanenbureau kan nu optioneel een **read-only
Linux-hostcapaciteitsmoment** toevoegen aan hetzelfde operatorrapport:

```sh
python3 scripts/zsentinel_four_lane_desk.py --db history.db --host-pressure
python3 scripts/zsentinel_four_lane_desk.py --db history.db --host-pressure --format json
python3 scripts/zsentinel_four_lane_desk.py --db history.db --host-pressure --format html --output /PRIVATE/NEW-report.html
```

`scripts/zsentinel_host_pressure.py` leest uitsluitend de lokale
`/proc/meminfo` en `/proc/loadavg` (elk begrensd tot 4096 bytes,
zonder symlink-following of filesystemwrite) en toont afgevlakte
MemAvailable, swapgebruik en 1-minuut-load per CPU. Als bronvelden
ontbreken of ongeldig zijn, blijft de status **UNKNOWN**; nul of
"gezond" wordt niet verzonnen. Het resource-inzicht is expliciet
**opt-in** met `--host-pressure`, zodat bestaande CLI-operaties geen
nieuw /proc-werk hoeven te doen.

Alle bestaande tekst-, JSON-, private Markdown- en offline HTML-weergaven
tonen dezelfde bronbeperking. Bij lokale hoge/verhoogde druk ontstaat een
drie-regelige menselijke eigenaaractie met
`HOLD_CAPACITY_OWNER_REVIEW`. Geen signaal verleent nieuwe
worker-admission; ook een lage drukmeting kan geen voldoende cgroup-,
container-, resource-pool-, Notion-claim-, GitHub-job- of
runnerbewijzen opleveren. Dit is **geen** nieuwe harde systeemgate.
De bestaande planner, releasevoorwaarden en live-capaciteitsregeling
worden niet gewijzigd.

Code: [1f2e4d8](https://github.com/Zennay/zCloud/commit/1f2e4d8c02264e503734a9a02de4fa0b5056496d),
[4920607](https://github.com/Zennay/zCloud/commit/4920607f1cfd1ef7a64419ad117a69be40bc431a),
[a28530f](https://github.com/Zennay/zCloud/commit/a28530fd4fbe9ec67c22119956a5fcca6def21de)
en [3238615](https://github.com/Zennay/zCloud/commit/3238615bcee5b89688c0806394e619955eac3405).
Alle wijzigingen staan op dezelfde bestaande [product-PR #1572](https://github.com/Zennay/zCloud/pull/1572).
Geen handmatige QA/regressie, workflow-retry, claimovername,
onafhankelijke reviewbypass, VPS-writer, runnerrestart of deployment.
Nog niet op main of live zolang bestaande exacte-head CI en releasegates
openstaan.


### Rechtstreekse read-only hostinspectie en zichtbare ernst

De exacte [broncommit 93a755d](https://github.com/Zennay/zCloud/commit/93a755daac4250f97f1f5350622d7fa2af1768a0)
van de nieuwe Linux-hostmodule is via de **verbonden zSSH** alleen
in memory opgehaald en direct tegen host-`/proc` uitgevoerd
(geen uitgepakte bestanden, SQLite-writes, herstarts of deploy).
Waarneming op 21 oktober: **4.072 MiB MemAvailable (36,5%),
swap 100% gebruikt, éénminuut-load/CPU 8,21**;
`HIGH_LOCAL_PRESSURE`, `new_worker_authorized=false`.
Dit is een echte lokale hostobservatie, geen bewijs voor een
defecte GitHub-runner of het geladen workerpool-contract.

De bron is daarop productmatig verder uitgewerkt:
[684dda7](https://github.com/Zennay/zCloud/commit/684dda731ebf839af13391814b5c1e3757d834c2)
houdt **hoge** hostdruk zichtbaar bij de maximaal negen
herstelactiekaarten, zelfs met veel generieke queue-waarschuwingen;
[270a930](https://github.com/Zennay/zCloud/commit/270a9302050ab5b30b8c8b944038ae385a7c65ce)
maakt dezelfde brongebonden waarschuwing in de bovenkant van de
offline HTML zichtbaar. Verhoogde druk geeft alleen adviserend
eigenaaronderzoek; bij lage druk ontstaat geen vrijgave.

De drie gewijzigde Python-productmodules zijn als exact gepinde
GitHub-bron via zSSH **alleen op AST-syntax** gecontroleerd; er zijn
geen QA-/regressiesuites geschreven of gestart. Deze bronwijzigingen
zijn **OPEN/UNMERGED/NOT LIVE** op dezelfde PR totdat bestaande
verplichte exacte-head CI, review, branchbescherming en release-eigenaar
integratie toestaan.


### 21 oktober · Schijfruimte opgenomen in hetzelfde herstelbeeld

De resourcebalie signaleert nu óók hostschijfruimte als potentiële
blokkade van bestaande CI-/runnerwerkstromen. Nieuwe bronmodule
`scripts/zsentinel_disk_pressure.py` gebruikt alleen
`os.statvfs("/")` (geen directoryscan of bestandsinhoud) en retourneert
afgeronde GiB beschikbaar voor de uitvoerende gebruiker plus het aandeel
bestandssysteemblokken dat voor **dat proces niet beschikbaar** is.
Dat percentage is uitdrukkelijk **niet** gelijkgesteld aan bewezen
gebruik door projecten: gereserveerde blokken kunnen inbegrepen zijn.

Bij minder dan 2 GiB beschikbaar óf minstens 95% niet beschikbaar
verschijnt `HIGH`; bij minder dan 5 GiB óf vanaf 90% verschijnt
`ELEVATED`. Onbetrouwbare/missende statvfs-bron wordt `UNKNOWN`,
niet `0` of `geen druk`. De bestaande optionele
`--host-pressure` weergave combineert deze lokale metadata met de
al bestaande begrensde RAM-, swap- en loadmeting. JSON, tekst,
offline HTML en private Markdown gebruiken automatisch dezelfde bron;
een ernstige situatie komt bovenaan de herstelbalie en in de
eigenaaractie-inbox terecht. Geen automatische opslagopschoning,
archiefmigratie, cgroup-mutatie, runner-restart of extra worker-toelating.

Broncommits: [7e63e1b](https://github.com/Zennay/zCloud/commit/7e63e1b30634b31e5412181f90e396ad3a0d4bce)
(reguliere bestandssysteemmetadata),
[956ab62](https://github.com/Zennay/zCloud/commit/956ab62f37087b0f596f6ec29500c983ba5d11b2)
(gecombineerde resourceclassificatie),
[1fc311e](https://github.com/Zennay/zCloud/commit/1fc311eafdca16801615b69818c910fdc2cc17db)
(eigenaaradvies). Dit blijft op bestaande PR #1572; geen
main- of live-claim voordat GitHub de exacte vereiste gates bevestigt.


### Brongebonden P0 runner/capacity-overdracht (geen automatische reparatie)

Een hoge hostdrukkaart kan nu, **alleen als de operator tevens
`--runner-processes` selecteert**, de lokale zCloud-listener-/worker-
waarneming in de *zelfde* menselijke vervolgregel opnemen.
Voorbeelden: listener **gezien maar GitHub-toewijzing onbewezen**;
worker gezien terwijl lokale listener niet is waargenomen (behoud job);
of procesbron incompleet. Geen van deze gevallen zegt dat een
remote workflow werkelijk stuck/geslaagd is. Een geobserveerde
zCloud-listener is niet hetzelfde als GitHub `runner_id`, labels,
toewijzing of daadwerkelijke CI.

`python3 scripts/zsentinel_four_lane_desk.py --db history.db --host-pressure --runner-processes`
levert het samengevoegde, read-only operatorbeeld. Het blijft een
menselijke HOLD/volgactie, zonder service-, CPUQuota-, runner-,
claim-, opslag- of GitHub-writes. Broncommit
[4766ff2](https://github.com/Zennay/zCloud/commit/4766ff26514bd7719875e50a19b5ed4a037e96e1)
staat op bestaande PR #1572. Issues als
[zCloud #1326](https://github.com/Zennay/zCloud/issues/1326)
worden **niet** automatisch gesloten of als hersteld bestempeld.
