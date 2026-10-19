# zSentinel · Runtime incident router (product-only)

This is an **executable, local recovery/routing decision engine**. It consumes a trusted, fresh canonical zSentinel diagnosis snapshot alongside current queued work and live-verified active claim scopes; emits a compact three-line-per-incident operator view or machine-readable JSON, and optionally persists a private deduplicated incident ledger. It does not pretend to own the control-plane writer. This is a deliverable for [PWQ-433](https://app.notion.com/p/3fe9e19ac95581aab21ceae956954b08), guided by policy [PR #1562](https://github.com/Zennay/zCloud/pull/1562).

## Start (source-only, no production writes)

```bash
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --format text
python3 scripts/zsentinel_recovery_router.py /secure/operator-observations.json --state /secure/zsentinel-ledger.json --format json
```

For a browser-ready view without contacting a server, choose `--format html` and redirect stdout to a **private operator-owned local file** (e.g. `> /secure/zsentinel-status.html`). The cockpit shows incidents, P0/P1 GitHub issue/PR links, queue suggestions, source freshness, incomplete claims and exact-job reconciliation. It contains no JavaScript, tracking, network requests or privileged buttons. File handling/privacy of the redirected output belongs to the operator.

The optional `--state` ledger stores incident fingerprints and observed timestamps locally with permissions `0600`, atomic replacement and strict size/schema limits. Use a **trusted private directory**, never a live queue/SQLite, checked-in repo path, shared mount or an untrusted symlink. It does not call GitHub, Notion or the VPS, start any timer, enqueue work, acquire a task, rerun CI, alter any worker, or deploy. Feed it new complete observations only after upstream independent collectors/owners have gathered them. One invocation provides one decision snapshot. Actual activation as a production service awaits the serialized runtime owner.

## Recente PR-loze branches · integratie van bestaand bronproduct #1554

Deze operatorintegratie **hergebruikt** de reeds op zCloud-main gemergede
`scripts/zcloud_recent_prless_ownership.py` ([PR #1554](https://github.com/Zennay/zCloud/pull/1554)),
die op zijn beurt de canonieke dubbele GitHub branch/PR-inventaris
`zcloud_deploy_prless_inventory_20261013_w1.py` (#1164) gebruikt.
`scripts/zsentinel_recent_branch_ownership.py` is nu alleen een **smalle
UI-adapter**, geen tweede crawler of nieuwe eigendomsbron. De bestaande
open-PR-overlapintegratie [#866](https://github.com/Zennay/zCloud/issues/866)
blijft een zelfstandige, te respecteren gate.

```bash
# Eén bestandsscope vergelijken tegen recente PR-loze branches:
python3 scripts/zsentinel_recent_branch_ownership.py \
  --project cloud --current-path scripts/new_module.py

# Fail-closed advies: exit 2 bij onvolledig bronbereik, overlap of
# ontbrekende --current-path. Zelfs exit 0 verleent GEEN work-claim.
python3 scripts/zsentinel_recent_branch_ownership.py \
  --project cloud --current-path scripts/new_module.py --require-clear-slice

# Toon exact dezelfde geverifieerde bron in het portfolio-overzicht.
# Kies een venster dat 'cloud' bevat (standaard --window 0):
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 \
  --inspect-unpr-branches cloud --current-path scripts/new_module.py \
  --format html > /private/operator-only/zsentinel-branches.html

# Zelfde actuele portfolio-CLI, maar exit nonzero als de volledige
# canonieke PR-loze slice geen betrouwbare overlapvrije dekking kan tonen:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 \
  --inspect-unpr-branches cloud --current-path scripts/new_module.py \
  --require-clear-unpr-slice --format json
```

Deze adapter is voorlopig uitsluitend voor het canonieke `Zennay/zCloud`
repository waar de bestaande bron voor gebouwd is. Een projectnaam of
namespacefilter kan de onderliggende complete inventaris **niet** veranderen
in een globale veiligheidsverklaring. Het adaptercommand pinnt eerst de
huidige exacte main-SHA; de bestaande collector leest de volledige GitHub
branch/PR-lijst, inspecteert maximaal twaalf branches per bronvenster,
vergelijkt immutable commit-SHA's en voert de tweede stabiele inventaris-
controle uit. De adapter verifieert vervolgens opnieuw dat main niet is
verplaatst. Alle requests zijn read-only GitHub GETs zonder onbeperkte polling.

Het canonieke bewijs onderscheidt branches ouder dan **zeven dagen** als
historisch, niet als actieve eigenaren. Recent maar niet meer ahead-of-main
wordt evenmin automatisch als actieve eigenaar geïnterpreteerd.
Herkomst/afwijking in een dubbele momentopname, incomplete branchpaging,
ongeldige exacte commit of afgekapt vergelijkingsbewijs leidt tot weigering.
De volledige overlapberekening wordt hergebruikt. De HTML-view kan voor
leesbaarheid maximaal honderd padnamen laten zien, met een expliciete
truncatiemelding en het volledige brongebaseerde overlapsignaal.

De JSON/HTML bevatten alleen veilige branchref, exacte SHA, afgeronde
commitleeftijd, ahead-telling, begrensde paden en overlap. Geen commitberichten,
PR-teksten, geheimen, branchwrites, runnerstarts, serviceacties of claims.
De bestaande onafhankelijke CI/review en trusted `[self-hosted,zcloud,vps]`
gates blijven vereist; deze integratie schrijft of draait geen QA-suite.

## GitHub-runnerroute: geen false-positive P0 bij approval of hosted queue

Bij de actuele P0-runnerroute (#1326) is een queue zonder toegewezen
`runner_id` **niet voldoende** om een offline VPS-listener te bewijzen.
De bestaande GitHub-bron leest nu de labels van de exact gepinde job. De
nieuwe bron `scripts/zsentinel_runner_route.py` classificeert maximaal
zestien begrensde labels read-only; de refresh behoudt uitsluitend de
canonieke route `self_hosted`, `hosted_or_custom` of `unknown`.

In de live Nederlandse herstelcockpit verschijnt per daadwerkelijk
opgevraagde job een link naar de exacte `Zennay/<repo>/actions/runs/<run>/job/<job>`,
de vereiste runnerlabels en de bronstatus. Het portfoliobrede overzicht
vermeldt bij ieder project of de uitgelezen job **self-hosted**, **hosted
/custom**, **onbekend** of **niet gescand** was. Geen extra GitHub-calls
nodig buiten de bestaande discovery + pinned exact-run/job GETs.

**Belangrijke incidentcorrectie:** de brongebonden `github_job_unassigned`
P0 wordt uitsluitend gemaakt wanneer een jobstatus exact `queued` is,
de job langer dan dertig minuten geen runner toegewezen kreeg **én** exacte
joblabels `self-hosted` bevestigen. Status `waiting` en `pending`
zijn mogelijk approval- of concurrency-gates; die vormen op zichzelf
geen runneruitval. Hosted/custom/onbekende runnerlabels bij een oningedeelde
queue worden `github_queue_route_unverified`: eerst de GitHub-route,
betaling, concurrency en toegangsrechten onderzoeken. Geen ongefundeerde
self-hosted-runnerherstart, workflowdispatch, billingswijziging of merge.

Een ontbrekende joblabelbron of verlopen GitHub-GET leidt nooit tot een
impliciete `self_hosted`-claim. Deze aanpassing respecteert de bestaande
exact-job-success-reconciliatie, waarbij één geslaagde job **geen bewijs**
van volledig runnerherstel of productie-deployment is.

**Bestaande foutmeldingen worden ook brongebonden heringedeeld.** Bij een
oude `github_job_unassigned`-ledgerregel vergelijkt de router de
`project_id`, `run_id`, `job_id`, exacte `head_sha` en een recente
`github_exact_run_job`-observatie. Alleen wanneer **diezelfde** job nu
`waiting/pending` blijkt te zijn, of exact `queued` is met bewezen
`hosted_or_custom`-labels, krijgt die specifieke regel de permanente
status `exact_job_route_reclassified_not_runner_recovered`. Alleen de
historische onjuiste `self_hosted`-gate op die regel wordt dan vrijgemaakt.
Andere P0's en hun gates blijven staan; een ontbrekend bewijs geeft
`not_observed_needs_reconciliation` en houdt blokkades vast.
De operator ziet `reclassified_exact_jobs` expliciet als **classificatie-
correctie, GEEN listener-/worker-/deployherstel**.

Ook de oorspronkelijke `zsentinel_recovery_router.py` en
`zsentinel_recovery_refresh.py` bouwen eerst hun volledige HTML/tekst/JSON
op en bewaren pas daarna het optionele private incidentenregister.
Een kapotte uitvoer kan daardoor niet onbedoeld het dedup-register
vooruitzetten. De lokale state-write blijft expliciet optioneel.

## Volledig portfolio in één offline cockpit · begrensde GitHub-vensters

De nieuwe operatorinterface `scripts/zsentinel_portfolio_overview.py` toont
alle projecten uit het canonieke `projects.json` en hun werkelijk op één
SQLite-transactie gebaseerde wachtrijdruk. Het is nadrukkelijk geen nieuwe
zelfstandig draaiende service of onbegrensde portfolio-poller.

```bash
# Eenmalig op een geautoriseerde machine met toegang tot de bestaande database:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --format html > /private/operator-only/portfolio.html

# Een specifiek GitHub-observatievenster (max. drie repo's), bv. index 0:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --window 0 --format text

# Gecombineerd met de twee bestaande opt-in read-only observatiebronnen:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --observe-vps-capacity --read-notion-claims --format json \
  > /private/operator-only/portfolio.json
```

Het standaardvenster is `--window auto`: bij **iedere aanroep**
wordt deterministisch een groep van maximaal drie projectrepositories
gekozen op basis van UTC-uur. Bij opeenvolgende uurlijkse, vooraf
geautoriseerde aanroepen rouleert het venster. Dit command **installeert
zelf geen timer, scheduler, webhook, PR-merge of notificatie**. Via
`--window 0`, `--window 1`, enzovoort kan de operator gericht inspecteren.
Een geselecteerd project is nog **niet** automatisch succesvol gescand:
GitHub-issue- en CI-bronnen worden per project afzonderlijk geclassificeerd
als gedeeltelijk uitgelezen, slechts één bron beschikbaar, geweigerd of
niet opgevraagd. Provideruitval, 401/403, rate limiting en samplinglimieten
zijn nooit "0 fouten" of bewijs van herstel. Alle andere GitHub-projecten
blijven expliciet **NIET GESCAND** in de huidige momentopname; GitHub-green
bij een geselecteerde job is evenmin bewijs van herstelde worker-, VPS-
of deploymentstatus.

De portfolio-overzichtrijen onderscheiden `queued`, reeds `owned`
(`claimed/running/verifying`), hoge claimpogingen, mogelijk verlopen
ownerleases, **uit de actuele bronsteekproef bewezen minimumaantallen**
P0/P1-issues en actieve incidenten, en een **concrete niet-muterende
vervolgstap**. Een getoond `≥0` betekent uitsluitend dat in de beperkte
steekproef niets is gevonden: het is nadrukkelijk geen volledig nulrisico.
Wanneer een GitHub-bron niet daadwerkelijk beschikbaar was, staat
`ONBEKEND` in plaats van `0`. Met `--read-notion-claims` toont dezelfde
portfolio-tabel bovendien per project `Notion actief (min.)`: alle bounded
Notion pagina's tellen mee (ook als het detailpaneel slechts 30 voorbeelden
toont). Zonder ingeschakelde/toegankelijke Notion-bron staat er
`ONBEKEND`; een getoond `≥N` betekent alléén zoveel gevonden
`Claimed/Running`-statussen uit de bron. Het is nooit bewijs van een
correcte lease, complete pad-eigendom, vrij baan of veilige task-dispatch. Claim attempts
betekenen niet automatisch mislukkingen, en een mogelijk verlopen lease is
niet vrijgegeven. Onbekende repositoryprojecten worden niet stilzwijgend
gelijkgesteld met een geregistreerd project; hun aantal kan zonder hun
privé-identiteit worden getoond. Als één record misvormd is, is de
wachtrij-inventaris onvolledig: alle per-project getallen worden
`ONBEKEND` in plaats van misleidende nullen. Eén zelfde read-only
database-observatie wordt gebruikt voor de projectsamenvatting en de
bestaande zSentinel-detailcockpit; er wordt geen tweede transactie gestart.

De begrenzing blijft maximaal drie GitHub-projecten per aanroep, dezelfde
bestaande CI-discovery/issue-collector en dezelfde optionele private
dedup-ledger. Actuele complete padscope-/claimbewijzen ontbreken nog;
dit instrument blijft **operatoradvies zonder taken te claimen**.
Private output blijft bij de operator; het HTML-bestand gebruikt dezelfde
strikte scriptloze offline CSP als de reeds bestaande cockpit.

## Direct live cockpit: no hand-created evidence bundle

The new `scripts/zsentinel_live_cockpit.py` is a complete, **one-shot** CLI product. It now also **automatically samples recent GitHub CI runs and relevant queued/failed jobs**, so a specific job ID is not needed to obtain initial GitHub blocker observations. It reads the real existing SQLite `portfolio_queue` using `mode=ro` / `PRAGMA query_only=ON`, merges a bounded public GitHub issue/PR inbox and optional pinned GitHub jobs, then renders a responsive **Dutch offline dashboard** in one command:

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --project supa \
  --format html > /private/operator-only/zsentinel.html

# Optional exact existing workflow job to diagnose (not dispatch):
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud \
  --job cloud:39000012345:117000123456:0123456789abcdef0123456789abcdef01234567 \
  --format json

# Skip all automatic GitHub issue and CI discovery entirely:
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --skip-github-issues --skip-ci-discovery --format text
```

### Actuele Notion-werkstatus zichtbaar, zonder eigendomsclaim (opt-in)

De cockpit kan nu de bestaande Notion Portfolio Work Queue via de officiële
Notion **read/query** data-source API raadplegen. Dit is een **read-only POST**,
geen taakmutatie. Opt-in, omdat deze integratie nooit stilzwijgend gebruikers-
of workerstatus mag aannemen:

```bash
# Set these privately in the operator session; never commit or echo tokens:
export NOTION_TOKEN=<private-read-capable-integration-token>
export NOTION_QUEUE_DATA_SOURCE_ID=<authorized-portfolio-queue-data-source-uuid>

python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --project supa \
  --read-notion-claims --format html > /private/operator-only/zsentinel.html

# May be combined with --observe-vps-capacity only on the intended Linux VPS.
```

De adapter filtert aan de bron op de geverifieerde Portfolio Work Queue-`Status`-selecties `Claimed` en `Running`, zodat oude `Done`/`Dropped`-regels de actieve scopes niet achter de pagineringslimiet duwen. De adapter `scripts/zsentinel_notion_claim_observer.py` leest maximaal vier
Notion-pagina's van elk honderd records met begrensde body en acht seconden
timeout per pagina, zonder redirect. Alleen `Claimed`/`Running` worden
in de privé-cockpit weergegeven (maximaal dertig zichtbare regels met totaal
en pagineringswaarschuwing). Alleen Project-relaties die exact overeenkomen
met de canonieke `projects.json` Notion-project-URL krijgen een leesbaar
projectlabel; onbekende/afgeschermde relaties blijven expliciet onbekend.
Projectlabels vormen **geen** bestandscope of toestemming tot eigendomsovername. Er verschijnen **geen** Notion-pagina-ID's,
relatiedoelen, bearer tokens of provider-response dumps. Notion 401/403,
verbroken netwerk, ongeldige configuratie, onvolledige paginering of gewijzigde
API-schema's zijn niet bewijs van een lege work queue.

**Autoriteitsgrens:** zelfs een volledig verse Notion-werkstatuslijst vertelt
niet welke concrete repositorypaden, live worker lease-epochs of filesystem
locks een andere eigenaar vandaag heeft. Daarom blijven
`claim_inventory_complete=false`, `task_inventory_complete=false` en alle
automatische onafhankelijke werkvoorstellen geblokkeerd. Een Notion-werkitem
met de status `Done` bewijst evenmin dat een PR door verplichte gates is
gekomen of daadwerkelijk gedeployed is. De bronadapter mag nooit claims
vrijgeven, taken dispatchen, reviewers passeren of lopende writers overnemen.

### Host capacity in the same incident cockpit (opt-in)

On the **actual intended Linux VPS**, operators can add a fresh local capacity
observation to the same one-shot dashboard without SSH, service access, an API
token, a privileged runner action or a separate hand-written evidence JSON:

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --project supa \
  --observe-vps-capacity --format html > /private/operator-only/zsentinel.html

# Also available as a standalone read-only JSON source:
python3 scripts/zsentinel_vps_resource_observer.py \
  --database /path/to/existing/zcloud.sqlite3
```

**Source identity is host-local.** The flag intentionally defaults to OFF.
It samples only the host on which the Python process actually executes, never
a remote host named by the database path. Enable the flag only from an
authorized session already running on the canonical VPS. The local observer
reads 5-minute load/CPU affinity and kernel `/proc/meminfo`, and measures
free bytes on the **same filesystem as the existing database** via
`statvfs`. It rejects a missing/symlink database, invalid kernel metrics
or non-Linux host, and exposes only bounded aggregates (no raw `/proc`,
process list, paths, tokens or private file content). It writes nothing.
A user redirecting stdout remains responsible for placing HTML or JSON in
a private directory. This is intentionally *not* a remote host authenticator,
inventory-completeness proof, or trusted authorization to provision capacity.

A successful explicit sample is marked
`live_sources.vps_capacity=same_host_readonly_opt_in`; the dashboard exposes
that provenance, including the warning that it is VPS data **only when run on
the intended VPS**. With the flag OFF the capacity remains unknown rather than
being fabricated from the workstation. The router's conservative overload
thresholds still apply; resource headroom alone cannot claim a work item,
restart a runner, approve a PR, enable paid Actions, or deploy.

**These are examples, not claims that the example IDs/database exist.** The default live command uses a bounded read-only GitHub CI discovery source (`scripts/zsentinel_github_discovery.py`): two separate bounded GitHub Actions windows (queued jobs first, failed workflows second) across all workflow event types, each listing at most five runs and inspecting at most two of them with ten jobs per run. Only a *specific* queued unassigned job older than 30 minutes or a completed failed/timed-out/action-required job becomes a candidate for the source-bound router. Job/run/commit identities must match GitHub. Incomplete/permission-limited sampling is **not** interpreted as zero CI incidents; successful completed jobs are not treated as evidence of live VPS recovery. Use `--skip-ci-discovery` to disable this GET source; `--job` pins an exact run/job to fetch independently. Discovery does not start, rerun, cancel, label, assign or restart anything.

 Use a private, existing operator-owned output directory and existing readable DB file, without changing VPS services. `--project` is repeated up to three times, must exist in canonical `projects.json`. Optional `--job` is limited to one pinned existing run/job/commit per project; GitHub details must match exactly. The issue collector is read-only, credential-safe, no-redirect, capped at three API calls per selected project. The CI discovery reader uses up to six GitHub GETs per project (two status-filtered run windows plus at most two job pages for each). The exact pinned-job collector uses up to two additional GETs per pinned or discovered job. The default combined bound for three projects is at most 33 GitHub GETs per invocation (18 discovery + 9 issue/PR + 6 exact-job reads), with no background polling. Credentials are never printed. The command does **not poll, daemonize, create a watch notification, or dispatch GitHub Actions**.

The queue observer in `scripts/zsentinel_queue_observer.py` also runs standalone:

```bash
python3 scripts/zsentinel_queue_observer.py --database /path/to/existing/zcloud.sqlite3
```

It reads one bounded SQLite transaction (up to 2,500 active rows), groups queued/claimed/running/verifying items by project and priority, and surfaces up to 20 of the worst claim-churn/possibly expired owner-lease observations with opaque hashed references. It never returns raw queue identifiers, credentials, lease tokens or task criteria, and it never changes `attempts` or an active lease. **Claim attempts count claims, not failures.** Any malformed or oversized active inventory is incomplete, never an all-clear or retry permit. The HTML cockpit shows queue pressure as a separate section; **stale (>5 minutes) or incomplete coverage suppresses individual queue risk cards**. The source is not sufficient to prove which repository paths a current worker owns.

The direct command deliberately leaves Notion, live worker heartbeats, complete file-level claims, and actionable queue eligibility as **unknown** rather than inventing them. Therefore it can display real GitHub and queue evidence but it cannot propose new owned work or assert worker/service recovery. Add independently obtained canonical fresh observations via `zsentinel_recovery_router.py` when a privileged scheduler owner has approved the reader and supplied complete reliable provenance. The offline output is not a deployment.

## Optional automatic GitHub read-only refresh

A second executable entrypoint now refreshes the **exact already-known GitHub run/job IDs** before generating the same recovery decisions:

```bash
python3 scripts/zsentinel_recovery_refresh.py /secure/operator-observations.json \
  --registry projects.json --state /secure/zsentinel-ledger.json \
  --read-product-issues --issue-project cloud --format html \
  > /secure/zsentinel-status.html
```

It reuses zCloud's existing `scripts/zsentinel_product_issue_inbox.py` canonical registry loader and hardened GitHub GET helper: HTTPS to `api.github.com` only, no redirects, bounded response bodies, 8-second timeouts, optional `GITHUB_TOKEN` from the environment and **no job reruns or billing changes**. Maximum eight explicitly selected jobs/two GETs each per invocation. With `--read-product-issues`, the same command also uses the existing canonical read-only GitHub issue/PR inbox for **up to three distinct canonical projects** selected with repeatable `--issue-project` (or the first three evidence projects). At most three additional GitHub GETs per selected project; missing/denied sources are degraded, never reported as zero open issues. A request flag never claims any issue. The received repository, run ID, job ID and pinned commit SHA must match the trusted input; otherwise that project's GitHub evidence becomes **degraded/unknown**, never an inferred runner outage. Job-created timestamps and actual runner assignment are required. An earlier billing attribution is deliberately cleared upon refresh: no-steps by itself does not prove failed payment. A separate independently verified billing annotation is necessary.

This is an **on-demand bounded live GitHub read**, not a daemon and not a complete Notion/queue/VPS data collector. Existing Notion/lease/runner observations are never re-timestamped by a successful GitHub GET, and a stale overall snapshot remains inadmissible. Do not schedule unlimited polling or run this command with an untrusted token. Explicitly selected jobs can be private; access denial or rate limits are reported as degraded rather than implying zero incidents.

## Input contract (JSON)

```json
{
  "schema_version": 1,
  "evidence": {
    "schema_version": 1,
    "observed_at": "2026-10-19T10:30:00Z",
    "projects": [{
      "id": "zcloud",
      "notion": {"posture": "incubate", "owner": "zCloud runtime owner"},
      "github": {
        "job_status": "queued",
        "queued_at": "2026-10-19T09:30:00Z",
        "observed_at": "2026-10-19T10:29:00Z",
        "runner_assigned": false,
        "run_id": 39000012345,
        "job_id": 117000123456,
        "head_sha": "0123456789abcdef0123456789abcdef01234567"
      },
      "runtime": {}
    }]
  },
  "claim_inventory_complete": true,
  "task_inventory_complete": true,
  "claim_observed_at": "2026-10-19T10:29:00Z",
  "task_observed_at": "2026-10-19T10:29:00Z",
  "resources": {
    "source": "trusted_readonly_vps_snapshot",
    "observed_at": "2026-10-19T10:29:00Z",
    "vps": {
      "cores": 6,
      "load_5m": 1.2,
      "available_memory_mib": 4096,
      "available_swap_mib": 1024,
      "free_disk_gib": 20
    }
  },
  "issue_inbox": null,
  "active_claims": [{"project_id": "supa", "active": true, "paths": ["src/prices"]}],
  "tasks": [{
    "id": "supa-recipe-import",
    "project_id": "supa",
    "priority": "P1",
    "state": "queued",
    "eligible": true,
    "qa_only": false,
    "human_gate": false,
    "active_owner": false,
    "requires_vps": false,
    "required_gates": ["none"],
    "depends_on_incidents": [],
    "paths": ["src/recipes"]
  }]
}
```

All dates must be timezone-aware. Underlying `zsentinel_diagnose.py` already rejects stale (>30 min) snapshots and unproven/future timestamps. The router further requires distinct positive `run_id` and optional `job_id` for GitHub incident identity; multiple jobs on a single SHA cannot be accidentally deduplicated into one incident. `waiting` / `pending` is **not** interpreted as a runner outage, even if an upstream legacy classifier thinks a job is unassigned.

A task suggestion requires **separate fresh (within 5 minutes)** `claim_observed_at` and `task_observed_at` timestamps plus explicitly true `claim_inventory_complete` and `task_inventory_complete`. Missing or stale timestamps suppress all suggestions, even when GitHub jobs are newly refreshed. A task suggestion requires every positive field: `queued`, `eligible=true`, `qa_only=false`, `human_gate=false`, `active_owner=false`, an explicit bounded file scope and an explicit set of required gates. Claim inventory must be declared complete and every active claim must have a known scope. A missing/unknown active claim scope suppresses **all** suggestions. Independently eligible suggested tasks must also have **non-overlapping scopes with one another**: the highest-priority candidate wins a collision; the operator must refresh claims when choosing actual work. Overlapping ancestor/descendant paths are not routed. Dependencies referencing currently observed incident IDs prevent routing. A runner outage prevents suggestions requiring `self_hosted`. Never infer a claim is free from a missing PR alone; proof of source completeness and fresh claims is the upstream operator/collector's responsibility.

## Product behavior and limitations

- **Exact-job lifecycle:** Re-observation of a previously blocked **same GitHub run + job + SHA** with a fresh canonical GET, actual non-empty executed steps, assigned runner and completed/success conclusion marks only **that specific job** `exact_job_completed_source_reconciled` in the private ledger. It never clears project-level approvals or proves a globally healthy listener, release or service. Unknown/vanished incidents remain blocked. Old incident entries without sufficient exact job identity cannot be automatically reconciled.
- **Runtime claim integrity:** Missing runtime heartbeat incidents require both `runtime.claim_identity_verified=true`, a bounded `runtime.claim_id` and an integer `runtime.claim_epoch`. Without them, the incident is counted as unattributed; no cross-claim dedup, worker ownership assumption or automated recovery is permitted.
- **Incident identity:** Stable local SHA-256 key over project, kind, exact commit and GitHub run/job identity when present. A job that disappears from a later snapshot is marked `not_observed_needs_reconciliation`, **never automatically resolved**. Incoming optional prior state is schema-checked; ledger saturation refuses truncation.
- **Prioritization:** Verified GitHub-hosted billing admission (including explicit no-steps evidence) is separately P0/human-only and blocks suggestions requiring `github_hosted`. Verified PR owner-approval waits are human-only, never retried. Explicit `github.verified_approval_wait=true` on a fresh `waiting` or `pending` job creates a distinct human-approval card **without** misclassifying it as an unassigned runner; absent independent verification, a waiting/pending job remains a normal wait. P0 unassigned GitHub queued runner jobs receive a *human-action-required* review target 2 hours from initial observation, not an unapproved runner restart. P1 workflow failure or missing runtime progress receives a read-only diagnosis target 12 hours from first observation. Targets are advisory, not Notion deadlines or automatic claims.
- **Real SQLite pressure observation:** Existing database schema and active work are read in one read-only query transaction. The result explicitly separates claim counters from failures, possibly expired leases from authorized cleanup, and partial/stale source coverage from a zero count. This includes operational evidence for open [zCloud issue #677](https://github.com/Zennay/zCloud/issues/677) without taking its writer scope.
- **Issue-first inspection:** Pass a complete JSON result from the existing `scripts/zsentinel_product_issue_inbox.py` collector as optional `issue_inbox` (or omit/null to disable). Up to eight P0/P1 non-QA issues appear with canonical GitHub links and existing PR candidates to inspect *first*. The original collector's partial/degraded/unavailable/truncation/denial flags and observation age remain visible; no GitHub assignee/PR is treated as a live claim. This is an inspection list, not an automatic eligible queue.
- **Capacity governance:** Every self-hosted/VPS-dependent candidate requires a trusted **at most 3 minutes old** read-only VPS snapshot including cores, 5-minute load, available memory, swap and disk. It is withheld when 5-minute load exceeds 1.5× cores, available memory falls below 1 GiB, swap below 256 MiB, free disk below 2 GiB, or evidence is missing/stale. `vps_resource_admission` explicitly distinguishes unknown, stale, overloaded and *advisory* headroom. Suggestions not requiring self-hosted compute must explicitly set `requires_vps=false` instead of relying on unknown destination. Resource headroom never authorizes a worker claim, runner restart or deploy.
- **Independent delivery:** At most five P0 → P3 file-disjoint candidate tasks are proposed, without modifying queue ownership. Required CI/review/merge/privacy/device/security/finance gates remain independent. A recommendation is **never** an authorization to edit an owner's files or dispatch a job.
- **Wear OS escalation feed:** Machine JSON also includes at most five P0-first `watch_alert_candidates` **only** for incidents with `human_escalation=true` (e.g. verified billing admission, genuine unassigned runner, verified owner approval). Stable incident-derived `alert_id` supports downstream idempotency. `delivered=false` and `watch_delivery_performed=false` are deliberate: this CLI never contacts a watch, schedules a notification or marks alerts delivered. Only the separately authorized watch integration may perform and acknowledge push.
- **Operator view:** Up to three bounded lines per active incident: *problem*, *action/owner*, *status and target review*. Machine output explicitly states no mutations/claims/CI retries/deployment. Historical absence and CI-green alone are never recovered.
- **Bounded work:** One 1 MiB securely opened, inode-bound UTF-8 JSON input with duplicate-key rejection, up to 250 tasks/claims, 150 sourced inbox issues, 200 incident ledger entries; no background job, automatic CI loop or runaway retry. Refuses malformed gates/paths, duplicate work IDs, unauthorized inputs and invalid/symlink local state.

### Release boundary

The script is useful offline immediately after a source checkout. It is **not running autonomously in production** until an authorized integration owner connects a complete read-only source collector, controls private state storage, coordinates conflict-free live claim acquisition, and promotes the reviewed, exact-head CI-approved change through the existing serialized zCloud release gate. Self-hosted runner [P0 #1326](https://github.com/Zennay/zCloud/issues/1326) and private hosted account admission [#1523](https://github.com/Zennay/zCloud/issues/1523) remain separate human-controlled incidents; neither is fixed or bypassed by this script. No QA suite was created or manually executed for this change.
