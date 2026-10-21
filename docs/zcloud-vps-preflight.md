# Read-only zCloud VPS deploy preflight


## 21 oktober · CPU-/PSI-trend in bestaande private hersteloverdracht

De reeds bestaande `--previous-report` kan nu twee expliciet opgeslagen
`--host-pressure`-metingen (hoogstens 60 minuten uiteen) vergelijken:

```bash
python3 scripts/zcloud_vps_preflight.py --host-pressure --previous-report /secure/private/eerder.json --format html > /secure/private/drukverloop.html
```

Het nieuwe `host_pressure_trend`-object vergelijkt CPU-vraag per core,
CPU-some-PSI en geheugen-/I/O-full-PSI. Alleen eindige, begrensde cijfers uit
twee expliciete `local_procfs`-bronrecords met hetzelfde aantal CPU's worden
gebruikt; ontbrekende waarden blijven onbekend. Een stijgende, dalende, stabiele
of gemengde trend is uitsluitend **menselijke observatie**. Een oude
momentopname, CPU-configuratiewissel, ontbrekend meetmoment of ontbrekende
bronvelden produceren een duidelijke coverage-status, **nooit een impliciete
herstelscore**. Dit maakt de bestaande private HTML-werkbak direct geschikt om
belasting over twee eigenaar-gecontroleerde snapshots te vergelijken. De bron
is operator-aangeleverd, niet cryptografisch hostgebonden of een toestemming
om een training te stoppen, runner te herstarten of te deployen.

## 21 oktober · optionele gedeelde VPS CPU-/I/O-druk vóór eigenaarvrijgave

Voeg `--host-pressure` toe aan de bestaande **read-only** preflight:

```bash
python3 scripts/zcloud_vps_preflight.py --host-pressure --worker-safety --format html > /secure/private/preflight-pressure.html
```

Het nieuwe zelfstandige `scripts/zcloud_vps_host_pressure.py` leest enkel
één 1-minuut host-loadmeting en kernel-PSI `avg10` uit CPU, geheugen en I/O.
De JSON bevat `host_pressure` met logische CPU-telling, genormaliseerde
1-minuut-vraag, percentages `cpu.some`, `memory.full`, `io.full`,
bronstatus en expliciet `release_authorized=false`. Het mobiele, scriptvrije
HTML-rapport toont alle drie bronnen naast de bestaande eigenaar- en releasekaart.

- Een load/CPU-verhouding vanaf **1,0** meldt druk, vanaf **2,0** een blocker
  voor aanvullende eigenaarbeoordeling. Dit is **runnable/I/O-vraag**,
  niet werkelijk CPU-gebruik of een bewezen individuele schuldige worker.
- `memory.full` of `io.full` van minstens **10% van de laatste 10 seconden**
  is een blokkadesignaal voor gelijktijdige zware taken. Ontbrekende of
  ongeldige CPU-/PSI-bronnen worden **onbekend/partieel**, nooit nul druk.
- Het gecombineerde `host_capacity`-beslisbord en het bestaande
  `CAPACITY_FIRST`-eigenaarsadvies gebruiken de nieuwe waarnemingen
  **uitsluitend wanneer de optie is opgegeven**. Zonder de optie blijven de
  bestaande preflight-observaties en resultaten ongewijzigd.
- Geen CPU-kernreservering, procesbeëindiging, loop, runner-/serviceherstart,
  workflowtrigger, claim-/databasewrite of wijziging aan training. Alleen de
  bevoegde release-eigenaar beslist ná de oorspronkelijke exact-HEAD CI,
  onafhankelijke review, bescherming van main en seriële productie-gates.

Deze productaanvulling zit op bestaande PR #1540 en is niet op main of
productie zolang GitHub de verplichte gates niet heeft vrijgegeven.


Run from the connected, already-authorized zSSH channel:

```bash
python3 scripts/zcloud_vps_preflight.py --json --expected-main-sha "$VERIFIED_MAIN_SHA"
```

Set `VERIFIED_MAIN_SHA` to an independently obtained *current* 40-character GitHub main SHA before invoking the command; never infer it from the dirty VPS checkout. This optional binding reports a blocked local-vs-main drift. Without the SHA the output is partial even if all local checks pass, never release authorized. Git identity comes from the checkout with inherited Git override environment variables excluded.

This command inspects local Git worktree identity and tracked/untracked changes, the dedicated zCloud self-hosted Runner.Listener, its unit state where readable, memory/swap and free disk. It checks both system and user systemd managers, ignoring the misleading `ActiveState=inactive` response of a **unit that is not loaded**. If **both** managers have a loaded unit of the same identity, the result is **ambiguous / blocked** even when both currently say `active`: duplicate listeners can compete for one GitHub runner registration. An `activating` or `deactivating` unit also blocks release admission until a stable status is established. If the unit cannot be verified, the release board says `unit_unverified`, even if a listener process was seen. The JSON, console and HTML operator views now also expose the loaded systemd manager (`system`, `user`, `both` or `unverified`) so the recovery owner can identify the correct scope without guessing or restarting the wrong service; conflicting scopes remain blocked. It never executes Git writes, restarts a service, calls an API, contacts GitHub, accesses SQLite, modifies workers or authorizes a release. No raw process arguments, file contents or credentials are returned. Exit codes: 2 = blocked, 1 = partial visibility, 0 = local checks clear (not permission to deploy).

**Critical limitation:** a clean local report is not evidence of protected-main approval, exact-head CI, active GitHub runner registration, a healthy web UI, owner leases or production status. Existing #1326 runner recovery, #1447 writer serialization and #580/#1089 release ownership are still required. Do not act on a failed/inactive runner merely from this report. Staged isolated execution in a scratch location can be used to inspect the live host without changing its dirty production checkout.

Use this as a short preflight for a human release owner, not an auto-healing agent.

## Owner-bound recovery priorities

The JSON report now exposes ordered `recovery_priorities` with codes, accountable owner, reason and safe next action. Human output prints the same order. Capacity is handled first (especially a full swap or critically low disk), followed by dedicated runner ownership, dirty checkout reconciliation and exact-source provenance. These are **suggestions only**; no programmatic recovery, cleaning, `git pull`, restart, database mutation or runner reassignment takes place. The final item always reminds the operator that successful local inspection does not grant deploy permission.

This workflow is intentionally not wired into existing production GitHub Actions jobs, owned by #580/#1089 and #1447. It does not depend on a GitHub token or make any network request.

## Optional offline HTML operator view

```sh
python3 scripts/zcloud_vps_preflight.py --expected-main-sha "$VERIFIED_MAIN_SHA" --format html > preflight-report.html
```

This optional output is a **standalone HTML snapshot**, responsive on a phone or desktop and printable. It embeds no JavaScript, external dependencies, credentials or detailed process arguments. All derived text is HTML-escaped. Display contains release blockers, Git/ref context, resource conditions and human owner-bound recovery steps. By default only stdout is written; redirection is an explicit **operator** decision and should target a private location, never a public web root. The process still returns a nonzero code on blocked or incomplete inspections even if an HTML page was emitted.

## Compare progress across owner-controlled recovery windows

Save a JSON snapshot only in a **private operator-controlled** location (standard output is never automatically stored), and use `--previous-report /private/path/to/previous.json` on a later observation with either text, JSON or HTML format. Output then separates **new**, **resolved** and **still-present** finding codes, allowing operators to focus on genuinely changed blockers instead of repeatedly triaging the same issue. The older file is read without mutation; filenames, notes and free-text from older snapshots are not surfaced. Oversized, malformed, symlinked or untrusted snapshot data is refused with a safe fixed error. This comparison is not an independent CI, main provenance or release authorization mechanism.

Severity changes such as a warning becoming a hard blocker are reported separately rather than falsely labelled unchanged. An earlier report timestamp must be well-formed and no later than the current observation; future-dated or naive history is rejected.

## Bounded disk triage (optional)

Add `--disk-hotspots` to include disk sizes for **five specifically named categories** only: checkout, zCloud runner installation, user cache, Gradle cache and RaiseAI build directory alongside the checkout's parent. All `du` operations are read-only, use argv rather than shell expansion, and have a three-second deadline each. Missing, symlinked, unreadable or timed-out categories are shown as **incomplete**, never as zero. Default runs incur **no recursive disk scan**. The operator can inspect candidates before any separately authorized cleanup; the preflight cannot remove caches, log files, runner builds or other workers' files.


## Inspect external archive availability without moving data

Use `--archive-readiness` to inspect the configured `/home/ubuntu/VPS-Archief` mount as part of the same read-only report. This checks the *exact* mountpoint in `/proc/self/mountinfo`; an unmounted directory is not mistaken for an archive. It also parses NUL-separated Linux `/proc/<pid>/cmdline` arguments to detect an existing rclone client explicitly started with `--read-only`, without printing arguments, secrets or environment variables. An unreadable process remains unknown, **not** proof of writability.

```sh
python3 scripts/zcloud_vps_preflight.py --archive-readiness --format html > private-preflight.html
```

The resulting report distinguishes `not_mounted`, `mount_declares_read_only`, `rclone_client_read_only`, and `mounted_write_unverified`. **Even a mounted read/write FUSE filesystem is not proof of permission to copy or purge data.** Archive readiness is a warning and an owner-directed next step, never an authorization to transfer, mount differently, overwrite, delete source files or bypass archive encryption/checksums. No write probes are attempted. On 20 October the connected VPS was observed running an explicitly `--read-only` rclone archive mount while local disk usage was around 91%; this flag is not a safe migration destination. Confirm separately that a legitimate encrypted writable destination and permissions exist, verify remote hashes, and authorize source removal only after reliable independent proof.

Git subprocesses in the preflight now drop *all* inherited `GIT_*` variables (not just three common index/worktree overrides), while using `GIT_OPTIONAL_LOCKS=0`, so inherited Git namespace, configuration and alternate-object overrides cannot redirect its checkout inspection. This is local hardening, not an assertion that the production checkout has been reconciled.

The preflight is deliberately **not** a deployment gate, an auto-cleanup script or a full runner-registration probe: the live listener process can exist without an assigned successful GitHub job. Exact-head protected-main CI, independent review and the serialized release owner remain mandatory.


## Protect ongoing worker generations during a release decision

The optional `--worker-safety` mode reads `history.db` with a **new SQLite `mode=ro` connection** and `PRAGMA query_only`. It never calls the mutating worker control-plane HTTP GET routes. This mode is strictly bounded to aggregate counters and recent (at most 301) browser events. It never extracts prompt text, conversation URLs, chat content, private claim owners, worker IDs or queue payloads.

```bash
python3 scripts/zcloud_vps_preflight.py \
  --worker-safety --archive-readiness --expected-main-sha "$VERIFIED_MAIN_SHA" \
  --format html > private-zcloud-release-preflight.html
```

The snapshot reports:
- `queued_claims`: number of currently **claimed** queue rows, conservatively counting stale claims rather than assuming it is safe to interrupt them. This is not the total number of runnable workers.
- `durable_claims`: outstanding entries in the separate `task_claims` table, again counted conservatively.
- `fresh_generating_slots`: distinct project+slot keys whose latest event among a bounded read-only snapshot is no older than 300 seconds and whose browser-declared generating flag is true.
- `events_sample_complete`: whether the bounded sample covers the recent observation window; when the source is unavailable, malformed, in the future, or sampling is incomplete, the report **blocks instead of guessing idle**.

When live claims or recently reported generation exist, the preflight returns `ACTIVE_WORK_REQUIRES_DRAIN` and points the operator to the existing serialized worker/release owner. This does not touch the queue or request a drain. The initial direct connected zSSH inspection on 20 October observed `queued_claims=5`, `fresh_generating_slots=5`, `safe_idle_verified=false`. These are browser/queue observations; they do not prove generated text, agent billing, actual tab visibility or whether individual threads should continue.

**No observation can grant safe-idle:** even zero claims and zero generating events returns `no_active_evidence_unverified` and `safe_idle_verified=false`. The original production safe-idle gate, claim ownership, exact-main provenance, review, CI, device/security gates and serialized human release authorization remain mandatory. An operator must not kill workers, reset queue claims, restart runners, unmount archives, or promote source using this read-only report.

Current production `history.db` can be large and active; each invocation uses a bounded read-only transaction, a short SQLite busy timeout, and only recent event identities. Do not run it in a high-frequency polling loop or infer the absence of activity when SQLite access fails.


## Combined release decision board — all ownership gates in one view

In addition to the individual detailed sections, every text, JSON and HTML report now has an ordered `release_gates` decision board. It distinguishes **local observations** from gates that this utility cannot decide:

| Gate | Evidence scope | Release authority |
| --- | --- | --- |
| `exact_main_checkout` | checked-out SHA compared with independently supplied GitHub main SHA | Never |
| `preserve_checkout` | tracked/untracked checkout modifications | Never |
| `runner_identity` | listener process / local service observation | Never; GitHub assigned-job evidence required |
| `host_capacity` | live RAM, swap and disk headroom | Never; capacity isn't reserved |
| `worker_safe_idle` | opt-in read-only queue claims and recent browser reports | Never; **incomplete evidence is blocked**, actual independent idle/lease confirmation required |
| `external_archive` | optional FUSE mount presence / known explicit read-only modes | Never; encrypted remote transfer authorizations separate |
| `required_exact_head_ci` | always `external_gate_required` | Existing exact-HEAD GitHub checks |
| `independent_review` | always `external_gate_required` | Existing human independent approval |
| `serialized_release` | always `external_gate_required` | Existing protected-main, writer lock and human-approved runtime release |

The board does not fetch GitHub secrets or run any mutating command. No single green local row means the release is approved; **every row** has `authorizes_release=false` and the top-level `release_authorized=false`. The HTML version stays self-contained and script-free; the JSON can be handed off privately to an authorized deployment owner. Active workers and a dirty checkout should be reconciled through their original owners, not forcibly stopped or reset.

The board ships as part of the already-open PR #1540 and remains inactive on production until normal exact-head required checks, independent review and protected-main merge. This source update is not a claim of deployment.


## Shared-VPS training headroom — operational release blocker (20 October)

A percentage-only disk check can miss a concrete shortage for co-located training workloads. The preflight therefore observes the filesystem's **actual free bytes**, always compares them with the existing **10 GiB operational training reserve** and emits `TRAINING_DISK_RESERVE_BELOW` when the reserve is not met—even if the percentage of free space happens to be above the old 10% threshold. The report includes `disk_free_bytes`, `training_reserve_bytes`, and `training_reserve_gap_bytes` in JSON, text, and the offline mobile HTML board. This is a safe readiness signal, never permission to stop a worker or delete anything.

Direct authorized connected zSSH source inspection at [eb9bc30e](https://github.com/Zennay/zCloud/commit/eb9bc30e0667b66a44d16bb5cf26eae8c283e889) reported **9,867,055,104 available bytes**, **10,737,418,240 required**, and a **870,363,136-byte shortfall**. The source returned `result=blocked`, `worker_safety.state=active_work_observed` and `release_authorized=false`; no service, runner, generation, queue, local checkout or archive was modified. These values can change every minute. Do not reuse them to authorize a future release.

Read-only worker accounting now explicitly closes its SQLite connection after its bounded SELECT transaction, avoiding lingering connection handles in long-running diagnostic use. It never reconciles, claims or updates queue state. All independent exact-head CI, review, protected-main, owner-gated safe idle and serialized production release barriers remain intact.


## Fail-closed runner cardinality and swap visibility (20 October)

A systemd `active` result is **not** enough when two dedicated `Runner.Listener` processes exist. The preflight now emits hard blocker `RUNNER_LISTENER_DUPLICATED` whenever more than one zCloud listener is observed, even if one unit appears healthy. The combined `runner_identity` gate is `blocked` unless **exactly one** dedicated listener is visible. The recovery priority points to the existing #1326 runner owner to reconcile competing registrations. It does **not** stop, restart, re-register or relabel either process, and one observed process still does not prove GitHub job assignment.

Swap visibility no longer interprets a missing `SwapFree` field as **100% usage** nor treats malformed total/free counters as a complete capacity observation. A genuinely configured zero-swap system remains distinct from missing or contradictory counters; the latter yields `SWAP_TELEMETRY_INCOMPLETE`, displays **unknown** swap utilisation in offline HTML, and sets the `host_capacity` gate to `unknown`. The resource-owner handoff prioritizes verifying those counters before deciding whether any controlled recovery is appropriate. Neither local observation nor a reported absence of blockers authorizes a protected-main merge, release or worker interruption.


### Guard an in-flight dedicated Actions worker

The same local preflight now inspects only the **dedicated zCloud** `Runner.Worker` executable identity in `/proc`, without displaying process arguments or reading their environment. It reports an aggregate `runner.worker_process_count` in the private JSON/text/HTML report. A detected worker process triggers hard finding `RUNNER_WORKER_PROCESS_OBSERVED`, marks `runner_identity=blocked`, and surfaces the existing #1326 owner's recovery route. This is conservative **process presence**, not independent proof of a GitHub job's state or an instruction to terminate the process. An unmatched/hidden process is **not proof** that the runner is idle; check authenticated GitHub job assignment and serialized worker owner gates separately. No process receives a signal and no job or service is restarted.


## 20 October · Do not label omitted runtime probes as recovered

The existing `--previous-report` incident comparison had a misleading delivery failure: when an earlier report used `--worker-safety`, `--archive-readiness` or `--disk-hotspots` but the next report omitted that optional flag, the old finding disappeared from the current findings array and could be labelled **resolved**. A missed observation is not a fix. The same ambiguity existed when a previously bad RAM/disk/runner state was hidden by an unavailable current source.

Current private JSON, text and offline HTML now distinguish `resolved_findings` (**current comparable evidence confirms the old finding absent**) from `recovery_unverified_findings` (**source not inspected or no longer sufficiently readable**). This is a *bounded read-only comparison*, not a live recovery attestation or service permission. No old snapshot is silently rewritten; existing schema-1 input and other report fields stay readable.

For reliable human comparisons, run the same optional observation flags on both snapshots and inspect the `recovery_unverified_findings` list before deciding whether a prior issue actually cleared. A healthy-looking count never permits an automatic worker interruption, queue rewrite, service restart, provider upload, main merge or deploy. Existing #1326 and #580/#1089 release ownership still applies.


A comparison cannot certify recovery of an **active worker**, independently of a later quiet browser snapshot, and it cannot certify encrypted archive write admission. If either previous warning disappears but the current report has no independent authority to prove its resolution, it stays under `recovery_unverified_findings`. The operator UI labels the same items **Recovery unverified**, distinct from **Confirmed resolved**. This is intentionally conservative; the existing serialized worker and archive owners must supply proof before any state-changing action.


## 21 October · zSentinel three-line owner handoffs (existing PR #1540)

The preflight now derives a bounded, **read-only** `owner_handoff` from its existing
finding codes, owner recovery priorities and (if provided) a validated previous
snapshot. These cards are *not* an automated recovery API and are never proof
that a claimed worker, runner, archive or release was safely recovered.

```sh
# The operator supplies a previously independently fetched main SHA.
python3 scripts/zcloud_vps_preflight.py \
  --expected-main-sha "$VERIFIED_MAIN_SHA" --worker-safety \
  --format brief

# Optional previous private JSON report; no previous report is modified.
python3 scripts/zcloud_vps_preflight.py \
  --expected-main-sha "$VERIFIED_MAIN_SHA" --worker-safety \
  --previous-report /private/previous-zcloud-preflight.json --format brief
```

Each owner card contains exactly three user-facing lines:

- **Problem** identifies an observed capacity, runner, checkout, source,
  archive or worker issue, grouped by immutable incident-code family.
- **Action** identifies the responsible existing human owner and a safe
  next step. There is no shell command, server restart, claim release,
  queue write, GitHub workflow dispatch or automated source promotion.
- **Status** distinguishes `active_blocker`, `active_warning`,
  `owner_release_gate` and `recovery_unverified`. With a previous report,
  it also distinguishes new, still-observed and escalated incidents.

This same `owner_handoff` array is included in the JSON response and
rendered inside the standalone responsive HTML report. Rendering escapes
all displayed strings. Raw worker identities, arguments, queue payloads,
browser events and old snapshot prose are never copied into the cards.
Historic incident codes which can no longer be observed remain explicitly
marked `recovery_unverified`, including future codes without a known owner
family; absence from the current probe is never auto-classified as fixed.

The source remains pending the **existing exact-head mandatory CI,
independent approval, protected-main merge and authorized serialized
deployment**. A user-facing handoff never grants permissions to stop
generation, restart a runner, delete training artifacts or spend hosted
runner minutes. `release_authorized=false` and
`recovery_performed=false` on every card.
