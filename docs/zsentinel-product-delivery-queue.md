# zSentinel product-only queue admission (17 October user decision)

The portfolio queue's ordinary **new** worker admission now skips isolated QA, regression-test and audit-only work packages while still admitting actual product bugs, feature implementation, integrations, merges, and recovery tasks.

* Live eligibility, priority P0/P1, resource limits, manual-start preference, independent project lane/capability conflicts, reviewer gates and exclusive lease/claim rules remain unchanged.
* Already `claimed`, `running` or `verifying` work is **never cancelled, displaced or rewritten** by this feature. Its scope is included in the lane ownership graph even when a queued QA item is skipped.
* Metadata `lane`, `lane_id`, `focus`, `workstream` etc matching known QA-only lane IDs denies **new** admission; explicit QA/test/validation title prefixes also deny **unless the ticket requirements explicitly demand a real fix/build/delivery**. A P0 item called `Test urgent planner navigation` with a `Fix ...` completion criterion remains a P0 product fix; a pure expected-RED test carrier remains excluded.
* A legacy neutral-title task containing only a QA/regression-suite instruction with no actual delivery verbs is not freshly claimed. That task remains in the queue for existing owner-authorized re-scoping; no fake `done` or de-eligibility is written to SQLite.
* Lane selection sees only admissible queued candidates, so a high-priority old QA item does **not** hide the next buildable product item in the same lane. Active work is still visible for cross-lane scope protection.
* This is a **source change** to `server.py` + one pure policy module. No VPS, worker, DB or file data has been changed. It becomes operational **only after** the regular serialized production deployment and runner #1326 are actually restored and verified. All permanent existing CI and reviewer/approval gates still apply; this change adds no QA/test suite or synthetic fixture.

If the queue contains no eligible product item, leave workers idle rather than inventing a test round or taking over another worker's claim. Queue items remain auditable for manual owner review.

Admission scans at most four ordered 240-row queue pages (960 queued rows in total) to find up to 240 viable product candidates. This prevents a large stale QA backlog in the earliest priority page from hiding a runnable product ticket; the scan is read-only and bounded, with no scan beyond 960 rows automatically assumed complete.

The existing authorized `GET /api/portfolio-queue` now also exposes `delivery_policy` with queued QA-only held count, a bounded count and up to eight P0-first advisory product candidate IDs, explicitly flagged `not_claimed_or_authorized=true` and `admission_scope_lease_and_capacity_not_verified=true`. This is read-only, additive and does not modify the existing queue `items`, lease, route authorization or allocation response contracts.

Unparseable, duplicate-key, non-object or overlong workstream metadata now fails closed for **new** admissions, preventing an ambiguous serialized `qa-only` classification from being silently converted to product work. Existing active claims and historical queue records are never rewritten.

## Multilingual admission and truthful holds (20 October)

The source policy now recognises Dutch build/repair language (including `bouw`, `implementeer`, `herstel`, `repareer`, `integreer`, `oplossen`) alongside English product tasks. Dutch QA/validation lane labels, QA title prefixes, and criteria that request only a validation cycle are held **before a new worker can be admitted**. Explicit negations such as **do not merge** and **niet deploy** are not counted as positive delivery intent. A genuine delivery action embedded in a QA-prefixed legacy title is not blocked on title alone; an explicitly QA-only workstream remains excluded. No language heuristic grants claims or merge rights.

`product_delivery_disposition` has three outcomes: `product` (eligible for the *existing* subsequent authorization path), `qa_only` (hold new QA admission), and `invalid` (unknown/corrupt metadata or malformed ticket: fail closed). Existing `is_new_product_delivery` delegates to that same decision, avoiding a second classifier.

The read-only portfolio API's `delivery_policy` now separates `queued_qa_only_held` from `queued_invalid_or_unknown_held`; malformed metadata no longer increases a count labelled as QA when its malformed state reaches the source classifier. Both counts are limited to the returned visible queue items, not a complete queue-wide census. The queue API now pins a `delivery_policy_disposition` read-only annotation to **each original queued SQLite row before legacy JSON presentation normalization**. The dashboard's `invalid` count therefore sees the same corrupt/duplicate-key raw JSON refusal as new-worker admission, even though the historical `metadata` field remains normalized for compatibility. This annotation is advisory: it is not a lease, permission, merge authorization or permission to change an active worker. Do not infer full queue coverage from any visible API counters. Active claims and owner scopes are never released or displaced by either classification. All existing review, exact-HEAD CI, main-merge, resource and release gates remain mandatory; these committed source changes are **not** a live VPS deployment.

## Read-only worker-admission workbench (20 October, subsequent source commit)

The existing authenticated `GET /api/portfolio-queue` response now includes operator-facing queue pressure categories from the **visible response items only**:

* `queued_rows_visible`, `active_claimed_running_verifying_visible`, and `malformed_nonobject_rows` establish the input's observation scope.
* `queued_ineligible_held` and `queued_worker_slot_occupied` distinguish authorization/slot constraints from policy holds. They do **not** establish why a task was marked ineligible or who owns a slot.
* `queued_qa_only_held` and `queued_invalid_or_unknown_held` distinguish intentional no-QA policy from missing/corrupt classification evidence.
* `candidate_priority_counts` reports P0/P1/P2/P3/other **visible candidate** distribution; `candidate_list_truncated` warns that the up-to-eight visible candidate list hides additional matching rows.
* `source_coverage=visible_api_rows_only_not_full_queue` prevents those partial counts from being advertised as the full durable queue.

This product workbench is additive: the queue's existing `items` contract remains unchanged, and `not_claimed_or_authorized=true` / `admission_scope_lease_and_capacity_not_verified=true` remain unconditional. No auto-merge, queue write, worker admission, rate-limit change, CI dispatch or host mutation is implied. Merging to main and deploying are distinct owner-gated next steps.

## Source-consistent metadata receipt (20 October follow-up)

The queue's original `metadata_json` is classified once with the strict duplicate-key, bounded, nonfinite-rejecting reader **before** `_portfolio_queue_row` supplies a compatibility `metadata` object. A sanitized `{}` may still be rendered for legacy consumers, but `delivery_policy_disposition=invalid` remains visible on an originally invalid queued row and is consumed by the summary. Standalone calls to the pure summary function without a source receipt continue to classify their own items. No new live queue state is written, and no extra SQL read is required.

## Actionable hold reasons (20 October continuation)

Every queued row in the authenticated `GET /api/portfolio-queue` response now carries `delivery_policy_disposition` and a fixed `delivery_policy_reason`. Both are computed from the original SQLite row **before** legacy JSON presentation normalization. Invalid source metadata cannot silently masquerade as a valid product ticket in this interface.

The `delivery_policy` summary adds `queued_hold_reason_counts`, a bounded map of stable, non-sensitive reason codes. Examples: `explicit_qa_lane` (explicitly excluded from new dispatch), `qa_title_without_delivery` (legacy QA-only title without actual implementation), `invalid_metadata` (raw parse fails), `invalid_title` (ticket requires human correction), and `invalid_lane_metadata` (malformed ownership classification). Counters are observations over **only visible eligible, slot-free queued items**; they do not imply all repository tasks were scanned.

This is a **read-only operator explanation**, not a workflow or worker action. An invalid ticket must be re-scoped by its authorized owner; do not silently drop, unclaim, rewrite or relabel it. `product_candidate` is an input to existing downstream lane/lease/capacity gates, **not** permission to dispatch, merge or deploy. Active `claimed/running/verifying` tasks remain unchanged.

## Cross-project product leads, without extra worker claims

A global P0 queue can be dominated by one project even while other projects have independent, eligible product tickets. The read-only `delivery_policy` response now includes `project_shortlist`: the highest-priority **visible product candidate per project**, each with its observed candidate count and first queue identifier. The bounded list is sorted P0 first and then by candidate pressure; `distinct_projects_with_visible_product_work` and `project_shortlist_truncated` make partial display explicit.

**Do not confuse this with assignment.** `project_shortlist_is_advisory_only=true` is unconditional. A listed project must still pass its current Notion claim, file-scope ownership, project cap, lane overlap, worker capacity and exact runtime authorization. The existing global `candidates` list and actual allocator priority are **unchanged**; no round-robin dispatch, forced preemption, queue retry, claim or service action has been introduced. This list merely prevents a single chatty project's items from hiding independent work in the human operator's view.

## Stop new QA-only queue entries at the producer

The source-protected `portfolio_queue_enqueue` path now checks the same `product_delivery_classification` before creating or updating a queue row. A new QA-only or invalid request is rejected with a fixed reason code. This prevents a manual/API enqueue, worker continuation or normal queue seeding from silently creating a fresh task that the worker allocator is already instructed not to run. No historical queue rows are deleted or migrated and no active lease is touched.

The classifier also recognizes legacy imperative QA-only titles such as **Run full regression suite**, **Draai alle regressietests** and **Voer QA controle uit** even when a metadata lane is absent; those are intentionally excluded. A genuine source repair is still allowed if it is actually product work and passes every existing downstream claim/security/release gate. Human-only or external-gated work retains its existing stricter checks. This implementation creates no new worker, test suite, CI rerun or background service.


## 20 oktober · Echte runtime-eigenaarschap- en productcontinuïteitsreparaties

De bestaande product-admission PR is uitgebreid in het werkelijke
`server.py`-pad. Dit zijn **functionele queue- en databasegrenzen**,
geen extra QA- of offline rapportagesuites.

**1. Queue-audit neemt actieve taken niet meer over.**
`portfolio_queue_audit()` behandelt bij het opruimen uitsluitend
`eligible=1 AND status='queued'`-items zonder worker-slot,
`claimed_at` of `claim_expires`. Eerder werden óók
`claimed`, `running` en `verifying` rijen onder gewijzigde
human-gate-/titelregels op `dropped` gezet, waardoor een
lopende worker zijn bronclaim kon kwijtraken. De daadwerkelijke
`UPDATE` heeft nu opnieuw dezelfde voorwaarden, zodat een
tussen SELECT en UPDATE geclaimde rij niet door de audit wordt
ingetrokken. Voor zulke gewijzigde actieve scopes is beoordeling
door de bevoegde huidige owner nodig; de audit voert géén
force-unclaim, taakafbreking of reviewovername uit.

**2. Producer-retries kunnen geen claims of historische resultaten
meer overschrijven.** `portfolio_queue_enqueue()` gebruikt een
atomaire SQLite UPSERT met `ON CONFLICT ... DO UPDATE ... WHERE`.
Alleen een bestaand, nog ongeclaimd, niet-humangeblokkeerd
`queued`-item met hetzelfde originele project is aanpasbaar.
Bestaande `claimed/running/verifying/done/dropped`,
`eligible=0`, occupied-slot en niet-lege claimvelden blijven
ongewijzigd. Een onveilige collision geeft een gewone
`ValueError`; de caller of eigenaar moet een daadwerkelijk
nieuwe geautoriseerde vervolgidentiteit kiezen. De oorspronkelijke
bestaande taak wordt niet gereset en er wordt geen actie op de
live VPS/SQLite uitgevoerd vanuit deze bronwijziging.

**3. Refill laat oude QA-only rijen geen productwerk maskeren.**
De refill-inventaris past voor vrije `queued`-rijen nu dezelfde
pure `is_new_product_delivery`-classificatie toe als de echte
nieuwe-worker-admission. Daarmee geldt een QA-only backlog niet
als voldoende productwerk voor dat project en mag de reeds
bestaande continuation-producer een normale uitvoerbare taak
aanbieden. Lopende eigenaren blijven **altijd vertegenwoordigd**,
zelfs wanneer hun historische rij `eligible=0` is of niet
door de nieuwe productpolicy komt. Geen bestaande lease wordt
geherclassificeerd, ingetrokken of verplaatst.

**4. Live queue-health telt feitelijk beschikbaar productwerk.**
De historische ruwe velden `ready`, `queued` en `projects`
blijven backward-compatible. `healthy` is nu gebaseerd op
geobserveerd product-admissible queued werk plus de bestaande
actief geclaimde/uitgevoerde taken, niet op vervuilde QA-only
rijen. De uitlezing is begrensd tot 1000 rijen, met een
1001e rij als volledigheidssignaal. Nieuwe read-only
API-velden `product_delivery_ready_observed`,
`queued_qa_only_excluded_observed`,
`queued_invalid_excluded_observed`,
`product_delivery_source_complete` en
`product_delivery_missing_tail_may_exist` maken onbekende
staartdekking expliciet. Bij onvolledige bron kan onvoldoende
waargenomen productwerk geen bewijs zijn dat er nergens meer
werk is. Er wordt geen onveilige automatische herstelactie
aan een gezondheidslabel gekoppeld.

De source changes zitten in de **bestaande product-PR #1536**
en mogen niet als `main` of live beschouwd worden voordat
alle exact-head CI-, reviewer-, protected-main- en bevoegde
VPS-releasegates daadwerkelijk zijn gepasseerd. Er zijn
geen QA-/regressiesuites geschreven of gestart, geen workflow
herstart, geen Notion-/VPS-claim gewijzigd en geen producer
op productie gedeployed.


### 20 oktober · Resterende leasevelden en veilige nieuwe werktoewijzing

Een historisch `queued`-record met `worker_slot=NULL` is nog niet
automatisch vrij wanneer `claimed_at` of `claim_expires` een
vorig, niet-afgesloten claimspoor bevat. De bestaande echte
`_portfolio_eligible_lane_candidates_locked()`-selectie eist
voortaan ook dat **beide leasevelden NULL** zijn voordat een
nieuwe browser-worker dit werk kan ontvangen.

Diezelfde vrije-claimregel is op drie bestaande paden
consequent doorgevoerd: het project-refill-overzicht (geen
schijnbare actieve productvoorraad), de private read-only
queue-health (`product_delivery_ready_observed` telt het
record niet) en het bestaande operator-overzicht
`product_queue_snapshot()` (`queued_residual_lease_fields_held`
als aparte categorie). De classificatie wordt nooit gebruikt
om oude leasevelden automatisch schoon te maken of een
eigenaar te vervangen. Een bevoegde queue-owner dient
de werkelijke claimstatus te onderzoeken en zo nodig via
het normale bestaande proces vrij te geven.

Bewijsgrenzen: deze wijziging geldt **na** reguliere merge
en bevoegde serialized productiedeploy. Het veranderen van
GitHub-broncode is geen feitelijke terugname van een claim
of live runnerreparatie. De ongewijzigde veiligheids-,
CI-, review- en eigenaargates blijven verplicht. Er is
geen QA-/regressiesuite aangemaakt of gedraaid en geen
zSSH-schrijfactie of live queue-mutatie uitgevoerd.


### 20 oktober · Producerbeleid voor werkelijk nieuw werk

Het historische `portfolio_write_continuation()` maakte nog
**nieuwe** werkopdrachten die automatische tests expliciet verplicht
stelden. Dit kon de product-only toelating in de praktijk
ondergraven: een worker ontving een gemengde producttaak met
oude QA-instructies en mocht ondanks het beleidsbesluit van
17 oktober een regressieronde beginnen.

De producent geeft voortaan voor *nieuwe* zSentinel-, zSSH-
en reguliere projectcontinuations een bouw-/integratie-/
probleemoplossingsopdracht met slechts directe functionele
inspectie of build-bewijs. De oude formuleringen die het schrijven
of draaien van tests verplicht stelden zijn geschrapt.
Bestaande verplichte CI-workflows, onafhankelijke security-/red-team-
en code-reviewgates blijven expliciet verplicht en mogen
nooit door productpolicy of de worker worden omzeild.

Dit is **geen wijziging aan reeds geclaimde of lopende
werkopdrachten**. De branche is nog geen live deploy.


## 20 oktober · Actieve workers blijven eigenaar bij herstel, geheugennood en Force Start

De bestaande allocator behandelde een `running`-worker onder kritieke
geheugendruk soms als een beschikbaar slachtoffer voor automatische
herverdeling. De Force Start route kon ook `running` en `verifying`
claims van andere projecten op `queued` zetten. Dat gaf risico op
verlies van lopend werk en op dubbele voortzetting in een nieuwe slot.
De aangepaste productcode beperkt deze twee automatische
herverdeelroutes nu **uitsluitend tot `claimed` taken die nog
niet `running` of `verifying` zijn**, en controleert nog steeds
de bekende busy-worker en pending-browser-handoff bescherming.

De laatste slotwissel is nu bovendien **transactioneel
gecontroleerd**: alleen een echt geslaagde
`UPDATE ... WHERE status='claimed' AND worker_slot=?`
maakt ruimte voor een Force Start-vervanging. Als het betrokken
claimrecord ondertussen is gewijzigd, blijft de bestaande
slotbezetting staan en wordt geen andere worker toegewezen.

### Conflicterende huidige eigenaars niet automatisch laten verdwijnen

De oude `_portfolio_requeue_conflicting_claimed_lanes_locked()`
trok een `claimed` taak al in zodra twee actieve lanes
binnen hetzelfde project qua capaciteiten of bestanden overlapten.
Dat was onveilig: een `claimed` browser-worker kan reeds een
accepteerde, nog niet voltooide overdracht hebben.
De opvolger `_portfolio_observe_conflicting_claimed_lanes_locked()`
**registreert alleen** waargenomen overlap; alle betrokken
`claimed`/`running`/`verifying` rijen behouden
hun status, slot en lease. De bevoegde actuele eigenaar moet
een echt conflict oplossen, niet de automatische scheduler.

### Direct bruikbare operatoruitvoer

Het bestaande geautoriseerde `GET /api/portfolio-queue`
bevat aanvullend `active_lane_conflict_observation`.
Daarin staan `active_lane_conflicts_observed`, maximaal twintig
project-/lane-/queue-ID-paren, een truncatie-indicator en
`owner_review_required=true` met
`reassign_or_release_authorized=false`. Dit is een
huidige, read-only SQLite-observatie, **geen toestemming**
om een worker te herstarten, scope te claimen of een PR te mergen.
Een mislukte bronuitlezing wordt als
`unavailable_not_a_zero_conflict_observation`
weergegeven; een lege lijst bij onbereikbare bron betekent
nooit dat er geen problemen zijn.

De bestaande route blijft onder zijn normale API-autorisatie.
Er wordt geen nieuwe publieke endpoint, extra runner, geplande
job of test-/QA-suite toegevoegd. De wijzigingen staan op de
**bestaande PR #1536**, niet op `main` of live VPS. De
bestaande exacte-head CI, onafhankelijke review,
branch protection, huidige eigenaarschap en serialiseerde
productie-release blijven vereist.


### 20 oktober · Actief browserwerk blijft ook tijdens normale prioriteitswissel behouden

De normale allocator kon in zijn `claimed`-tak nog vóór echte
browserstatusdetectie een prioriteit-, capaciteit- of diversiteitswissel
uitvoeren. De scheduler controleert nu vooraf de bestaande
`_busy_ai_worker_keys()`-momentopname met de bijbehorende
globale slot-/project-workerrelatie. Is de echte browserworker
bezig, dan **houdt die dezelfde bestaande taak en slot**,
wordt alleen de normale leaseverlenging uitgevoerd en volgt
geen preemptie naar een ander project. Het reeds bestaande
pending-handoff-slotbehoud blijft eveneens actief.

Dit geldt ook wanneer een geclaimde taak tijdelijk nog niet
naar `running` is gemarkeerd: de workeractiviteit, niet een
verouderd queue-label, bepaalt of preëmptie onveilig is.
Bij onbetrouwbare of ontbrekende actuele ownerinformatie
is de normale autorisatie/lease-route nog steeds leidend;
het signaal geeft geen nieuwe rechten aan de scheduler.
Een bezig zijnde browserworker krijgt **geen extra taak**;
deze wijziging behoudt uitsluitend het bestaande werk.

Er is geen nieuwe test-, QA-, CI- of VPS-actie gestart en geen
live queue-mutatie uitgevoerd. Alleen echte geautoriseerde
merge en productie-uitrol maken dit runtimegedrag actief.


### 20 oktober · Verlopen leases zonder onbedoelde werkovername

De allocator had nog één zelfstandige route die **alle**
`claimed`, `running` en `verifying`-rijen met een
verlopen `claim_expires` direct op `queued` zette,
ongeacht werkelijke voortgang. Zo kon een vertraagde
browserworker of een taak in verplichte review plotseling
zijn slot kwijtraken en meteen in dezelfde tick opnieuw
geclaimd worden. Dit gaf risico op dubbel werk, verloren
overdracht en claim-churn.

De source allocator inspecteert deze gevallen nu
afzonderlijk. `running` en `verifying` worden
**nooit op basis van alleen TTL automatisch vrijgegeven**.
Een `claimed` taak met aantoonbaar bezige browserworker
blijft ook eigenaar, inclusief bij tijdelijk ontbrekende
globale slotsynchronisatie terwijl een worker van hetzelfde
project nog bezig is. Een beschermde lease krijgt alleen
de normale allocatorslot-retentie, **geen verzonnen
bewijs van voortgang of geslaagde CI**.

Een werkelijk verlopen, niet-bezige `claimed` taak kan
via de bestaande statuswijziging worden vrijgegeven,
maar de oude queue-ID wordt direct in de reeds bestaande
`preempted`-set geplaatst. Daarmee kan de allocator
die ID **niet in dezelfde tick opnieuw claimen** voordat
de oude browsertoewijzing uit de bron is verdwenen.

De bezette-slotselectie telt ook bestaande ineligible
`claimed`/`running`/`verifying`-rijen mee:
het ongeschikt worden voor **nieuw** werk is geen
toestemming om een reeds door een owner geclaimd
browser-slot over te nemen. Voor aantoonbaar vastgelopen
actieve taken blijft een aparte, bevoegde owneractie
en echte actuele observatie nodig. Er is geen nieuw
recovery/CI/review-overridepad toegevoegd.

De source is uitsluitend op de bestaande PR #1536
gecommit, niet in live SQLite gedeployed. Alle bestaande
merge-, CI-, reviewer- en productieautorisatiegates
blijven verplicht. Geen QA- of regressierondes,
geen serviceherstart en geen live workerclaimmutaties.


### 20 oktober · Actieve claims zonder projectregistratie zichtbaar, niet als veilig beschouwd

De read-only scopewaarneming slaat nu ook
`claimed/running/verifying`-rijen op waarvan de project-ID
niet meer in `PROJECT_INDEX` voorkomt. Eerder zouden die
stilzwijgend uit een conflictinventaris verdwijnen, zodat
nul waargenomen overlaps ten onrechte als volledig overzicht
kon worden gelezen.

Het geautoriseerde `GET /api/portfolio-queue` meldt nu
naast de echte scope-overlaps expliciet
`unclassified_active_claim_count`,
`unclassified_active_claims` (maximaal twintig),
`unclassified_list_truncated` en een afwijkende
`coverage=active_project_scope_unknown_owner_review_required`
wanneer een actieve eigenaar door de actuele projectregistratie
niet classificeerbaar is. Bij bronfouten zijn beide counts
`null`, nooit een fictieve nul. De actieve records
blijven ongemoeid; dit is een menselijke herstelactie en
verleent geen vrijgave of claimrecht.


### 20 oktober · Workerlimiet veilig wijzigen zonder actieve productclaims te verliezen

De bestaande `POST /api/dynamic-workers` maakte eerder een gevaarlijke sprong:
`reconcile_dynamic_worker_limit()` zette alle `claimed`, `running` en
`verifying` taken boven een verlaagde limiet (of alle taken bij limiet nul)
terug naar `queued`. De browser kon echter nog produceren of op review
wachten. Daardoor kon hetzelfde werk opnieuw worden uitgegeven en de vorige
overdracht verloren gaan.

De bestaande product-PR bevat nu een **atomaire weigering vóór de instelling
wordt opgeslagen**. De server leest binnen `BEGIN IMMEDIATE` zowel de huidige
slotgebonden taken als de browseropdrachten die nog `pending` zijn. Een
instelling die één van die eigenaren buiten de nieuwe totale capaciteit plaatst
of diens slot van ChatGPT naar Claude (of omgekeerd) verplaatst, krijgt een
gewoon HTTP 400 met een concrete melding. Noch de opgeslagen instellingen noch
de oorspronkelijke claim, lease of browsercommand worden daardoor gewijzigd.
Ook sommen boven de daadwerkelijk ondersteunde globale poollimiet worden
geweigerd. Het dashboard meldt dezelfde werkelijke globale bovengrens.

De aansluitende reconciler bevat **geen impliciete actieve-task-requeue meer**:
hij verwijdert alleen werkelijk buitengrens-slotmappings nadat hij de actuele
SQLite-status opnieuw heeft gecontroleerd. De bestaande allocator kiest
eventueel nieuwe *vrije* producttaken via alle bestaande resource-, lane-,
lease- en eigenaarschecks. De human-only verkleinstap bij actieve workers is:
laat de bevoegde eigenaar eerst de huidige taak en browseroverdracht netjes
afronden, stel daarna pas de limiet opnieuw in. Dit is geen bevoegdheid om
een lopende taak zelf te onderbreken.

Ook de `portfolio_queue_allocation().candidates`-weergave is gecorrigeerd:
er staan alleen oorspronkelijke queued rijen in met source-gebonden
`delivery_policy_disposition=product`, zonder slot of resterende
claim-/leasevelden. QA-only en onbetrouwbare metadata blijven zichtbaar in de
geautoriseerde queue-inspectie/hold-reasons, maar staan niet langer als
uitvoerbare kandidaat in de browserworker-allocatie. Deze kandidaten zijn
**nooit** automatisch geclaimd of geautoriseerd.

**Levergrens:** code staat op de bestaande PR #1536; geen code uit deze
wijziging staat hierdoor automatisch op main of productie. Verplichte
exact-head CI, onafhankelijke review, bestaande branchregels en geautoriseerde,
geserialiseerde VPS-release blijven vereist. Geen QA-tests geschreven of
gedraaid; geen live taak/worker/runner/SQLite/service gewijzigd.

### 20 oktober · Cooldown- en intervalwijzigingen zonder reallocatie

Een beheerder kan in dezelfde geautoriseerde `/api/dynamic-workers`-route
een bestaande provider-cooldown, heartbeat-, DOM- of schedulerinterval aanpassen
zonder daarmee de producttaakverdeling te willen herschikken. De
configuratie-actie roept daarom de slotreconciler **alleen aan wanneer een
ChatGPT- of Claude-aantal echt verandert**. Bij ongewijzigde capaciteit blijft
de actuele queue/browserverdeling intact en wordt de read-only bestaande
allocatie gebruikt voor de respons. De respons rapporteert expliciet
`capacity_changed` en `reconciled`; beide zijn `false` voor een timing-only
update. Een blokkade op de resize van bezette slots hindert zo niet een
onafhankelijke veilige timingcorrectie. Het werkelijke boventotaal blijft wel
voor iedere configuratieaanvraag geldig.

Dit beschermt al begonnen productwerk; het is geen bewijs van fysieke browser-
of releasegezondheid. Echte workeropdrachten en planning blijven onder de
bestaande scheduler, eigenaarsclaims en productie-autorisatie.

## Portfolio-brede live-wachtrijactiviteit (20 oktober, broncommit 01642cdf)

De bestaande geauthenticeerde `GET /api/portfolio-queue` geeft via `delivery_policy.project_activity` nu maximaal 20 **read-only projectkaarten** op basis van alleen de zichtbare API-rijen. Per project toont de bron aantallen waargenomen actieve `claimed/running/verifying` taken, overige wachtrijdruk, mogelijke productkandidaten, QA-only/ongeldige aanvragen, niet-eligible taken, bezette slots en resterende leasevelden. Projecten met zowel actieve claims als extra productkandidaten worden voor menselijke scopecontrole bovenaan getoond.

Een vaste `operator_next_step` maakt onderscheid tussen eerst actieve claims controleren, autorisatie/capaciteit beoordelen, legacy QA/ongeldig werk door eigenaar laten herdefiniëren, en geen productkandidaat in de huidige steekproef. Er worden **geen namen van actieve claim-eigenaren afgeleid of vrijgegeven**. `project_activity_is_advisory_only=true`, `project_activity_source_coverage=visible_api_rows_only_not_full_queue` en `project_activity_truncated` voorkomen dat een gedeeltelijke weergave als compleet portfolio- of claim-bewijs wordt gelezen. Het bestaande globale allocatiepad, de productshortlist, legacy API-items en alle lease-/releasegates blijven ongewijzigd.

Deze commits implementeren bronfunctionaliteit in de bestaande open PR; er is geen productie-deploy of zelfstandige live-verificatie uitgevoerd. Exact-head CI, onafhankelijke review, branch protection en bevoegde release-acceptatie blijven afzonderlijk verplicht. Er zijn geen QA-suites toegevoegd of uitgevoerd.

## Brongetrouwe wachtrijmetadata (20 oktober, commits 3904768 en df9a207)

Bij classificatie gaat een aanwezige ruwe `metadata_json` vóór een afzonderlijke, mogelijk opgeschoonde `metadata`-presentatie. Ongeldige of dubbelgedefinieerde ruwe sleutels kunnen daardoor niet worden verstopt onder een ingelezen `{}` en alsnog als uitvoerbaar productticket verschijnen. De operator-samenvatting accepteert uitsluitend intern consistente paren van disposition en reden. Bij rechtstreekse bronrijen met `metadata_json` wordt de ruwe bron opnieuw geclassificeerd, ongeacht toegevoegde adviserende labels; bij de normale API-presentatie zijn die labels al vóór het weglaten van de ruwe bron vastgelegd. Deze bronbeveiliging schrijft **geen** queue-rij, verandert **geen** eigenaarlease en autoriseert **geen** workeractie.


## 20 oktober · Canonieke queue-identiteit, idempotente producer en eerlijk runtime-signaal

De bestaande `portfolio_queue_enqueue` weigert nu expliciet opgegeven lege,
niet-string of niet-canonieke queue-ID's; alleen een echt weggelaten ID wordt
op basis van de taakbron berekend. Dit voorkomt dat bijvoorbeeld een expliciete
lege ID stilzwijgend een nieuwe taakidentiteit oplevert. Voor overige niet-canonieke waarden geldt dezelfde weigering. Twee
verschillende externe waarden zoals `foo#1` en `foo 1` kunnen daardoor
niet langer stilzwijgend allebei als `foo-1` worden opgeslagen. Een producer-retry mag uitsluitend een bestaande,
nog ongeclaimde taak met **dezelfde project-ID, exacte titel en dezelfde parent-ID**
actualiseren. Een bezette, anders getitelde of vanuit een andere parent afkomstige
identiteit geeft een `ValueError` zonder de andere taakinformatie te overschrijven.
De bestaande SQLite UPSERT controleert bovendien atomair de ongewijzigde
status-, eligibility-, worker-slot- en leasegrenzen. Voor een werkelijk nieuwe
taak is een nieuwe canonieke identiteit vereist; actieve, afgeronde of human-held
items worden nooit teruggezet naar queued.

De source-level uitvoerbaarheidsherkenning gebruikt nu een echte regex-woordgrens
(`\\b`), in plaats van de letterlijk ge-escapete tekst `\\\\b`.
Dit herstelt herkenning van uitvoerbare acties in criteria bij bijvoorbeeld
legacy `review`-prefixen. De aparte product-only/QA-only classifier blijft
verplicht en verleent nooit een claim of vrijstelling van gatevoorwaarden.

`portfolio_queue_health` scheidt nu in het bestaande read-only API-antwoord
wat vroeger onder één `product_delivery_ready_observed`-getal viel:

- `product_delivery_unclaimed_candidates_observed`: vrije, policy-admissible
  queued rijen vóór afzonderlijke lane-, eigenaar- en capaciteitscontrole.
- `product_delivery_active_owner_rows_observed`: reeds claimed/running/verifying
  rijen die niet opnieuw mogen worden toegewezen.
- `queued_worker_slot_occupied_observed` en
  `queued_residual_lease_fields_observed`: queued rijen die niet vrij zijn door
  bestaande slotbinding respectievelijk achtergebleven claimvelden.
- `product_delivery_health_evidence`: `observed_sufficient`,
  `insufficient_complete` of `incomplete_unknown`. Die laatste waarde voorkomt
  dat een afgekapt venster van maximaal 1000 rijen voor volledig bronbewijs
  doorgaat. Bestaande `healthy`- en andere API-velden blijven compatibel;
  onvoldoende `healthy`-bewijs autoriseert géén herstelactie.

Deze commits zijn uitsluitend aan **de bestaande PR #1536** toegevoegd.
Bestaande automatische CI, onafhankelijke review, protected main, actieve claims
en productie-eigenaarschap blijven ongewijzigd. Er zijn geen QA-/regressiesuites
gecreëerd of handmatig uitgevoerd, geen VPS- of live SQLite-writes verricht en
er wordt geen merge, workerrecovery of live productherstel geclaimd.


## 20 oktober · Projecteerlijke wachtrijselectie zonder P0-verdringing

De werkelijke allocator `_portfolio_eligible_lane_candidates_locked()` las
voorheen wel maximaal vier bronpagina's, maar stopte zodra de **eerste 240**
policy-toegestane rijen waren gevonden. Daardoor kon bijvoorbeeld een project
met 240 P0-producttaken een ander vrij project vanaf bronpositie 241
verbergen. Zijn eigen hard/soft worker-cap kon vervolgens verhinderen dat
voldoende werk startte, terwijl de andere projecten niet eens als kandidaten
werden geclassificeerd.

De bestaande runtime-PR herstelt nu het samenhangende gedrag via
`portfolio_fair_candidate_window` uit de reeds gebruikte policy-module:

- Scan maximaal vier opeenvolgende vensters van 240 nog niet geclaimde,
  leasevrije bronrijen; QA-only en corrupte metadata blijven uitgesloten.
- Als die globale bronvensters een geregistreerd project helemaal niet bevatten,
  bekijkt de allocator maximaal 128 vrije rijen voor elk nog niet waargenomen
  project (maximaal 24 projecten met daadwerkelijk ongeclaimde queue-items),
  en neemt maximaal acht geldige kandidaten daarvan mee. Een enkele extra
  read-only DISTINCT-bronquery beperkt het aantal projectgerichte vervolglezingen;
  lege of uitsluitend bezette projecten worden niet onnodig opnieuw bevraagd.
  Daardoor kan een project achter meer dan 960 rijen
  van een ander project toch kandidaat worden zonder extra worker te claimen.
  Ook deze projectvensters kunnen onvolledig zijn; lange oude QA-backlogs blijven
  zichtbaar als een eigenaarshandeling, niet als bewijs dat er geen werk is.
- Binnen die **waargenomen** geldige productrijen reserveert de selectie maximaal
  acht kandidaten per onderscheiden project, en vult resterende plaatsen aan
  met de oorspronkelijk hoogst geprioriteerde overgebleven rijen.
- Het eindvenster bevat maximaal 240 rijen en behoudt exact de bestaande
  P0/P1/P2/P3- en `created_at/queue_id`-bronvolgorde. De latere planner
  blijft dus P0 eerst behandelen; het venster kan wel andere projecten meenemen
  wanneer de eerste P0-projecten hun huidige worker-cap bereikt hebben.
- De bestaande `generate_execution_lanes`, actieve scope/claim-inventaris,
  hard/soft projectcaps, beschikbare geheugenruimte, busy-browserstatus,
  Force Start, lease- en releasecontrole worden niet overgeslagen.
- Als globale én projectgerichte bronvensters geen geldig werk van een ander
  project opleveren, pretendeert de bron niet dat de volledige queue is
  gecontroleerd. Actieve claims blijven behouden; er wordt geen kunstmatige
  capaciteit gecreëerd en geen scheduler/VPS-actie uitgevoerd.

Dit is een **functionele allocatorwijziging** en niet een nieuwe audit of
QA-implementatie. De vereiste automatische CI op exact de nieuwe PR-head,
onafhankelijke review en geserialiseerde runtime-release moeten voor main/live
nog plaatsvinden. Geen testsuites geschreven, uitgebreid of handmatig gestart.


## 20 oktober · Actieve browseropdracht blijft zichtbaar tijdens leasevernieuwing

De echte `portfolio_queue_allocate` beschermde reeds `running` en
`verifying` taakclaims wanneer een lease kort voorbij zijn deadline was;
alleen een daadwerkelijk veilige `claimed`-idle taak kan volgens de bestaande
eigenaarsregels worden herverdeeld. Maar de afzonderlijke API-/slothelper
`portfolio_queue_current_for_slot` en projectbezetting-helper
`portfolio_queue_has_project_assignment` eisten voor **alle** statussen een
nog niet verlopen `claim_expires`. Daardoor kon het dashboard/browserpad een
lopende of verifiërende owner tijdelijk als ontbrekend tonen vóór de gewone
schedulervernieuwing. Dat is strijdig met het actieve-claimcontract.

De twee uitleesqueries zijn nu gelijkgetrokken met het bestaande
allocatiebeleid: `running` en `verifying` blijven zichtbaar ook met een
verlopen lease, terwijl een niet-actieve `claimed`-rij de oude leasevoorwaarde
behoudt. Bij ongelijktijdige inconsistente slotregels heeft `running`
voorrang boven `verifying` en `claimed`; niets wordt automatisch vrijgegeven.
Het daadwerkelijke moment waarop een taak door de bevoegde eigenaar wordt
afgesloten, blijft ongewijzigd. Deze branch voert geen live leasevernieuwing,
database- of browserwijziging uit. Alleen normale CI, onafhankelijke review,
protected main en runtime-owner-release kunnen dit operationeel maken.


## 20 oktober · Producer-identiteit blijft volledig onveranderlijk bij herhaling

De bestaande `portfolio_queue_enqueue()` behandelt een retry op dezelfde queue-ID nu
alleen als dezelfde taak als project, exacte titel, parent, prioriteit, volledige
completion criteria en source URL **allemaal identiek** zijn. Eerder kon een
nog vrije queued rij binnen dezelfde project/titel/parent andere requirements,
prioriteit of bron krijgen zonder nieuwe taakidentiteit. De SQLite UPSERT laat
geen inhoudelijke wijziging meer toe; een afwijkende herhaling krijgt een
duidelijke `ValueError` en vraagt de eigenaar om een nieuwe geautoriseerde
taakidentiteit. Ook `NULL` en een lege parent-ID worden niet samengevoegd.

Deze bescherming geldt *naast* de reeds bestaande atomische waarborgen voor
`queued`, eligibility, ontbrekende workerbinding en ontbrekende leasevelden.
Een automatische producer krijgt geen recht om een claim, verificatie, historisch
resultaat of human-held item te wijzigen. Geen database-migratie of productie-
queuewijziging is uitgevoerd vanuit deze branch.

## 20 oktober · Oude QA-backlog verbergt geen vrije herstelopdracht in projectvenster

Het aanvullende per-project bronvenster gaat nu maximaal vier opeenvolgende
pagina's van 128 vrije queued rijen langs en stopt bij acht
policy-toegestane productkandidaten of einde van het bronvenster. Een project
met bijvoorbeeld 128 oudere QA-only werkitems en daarna een uitvoerbaar issue
kan daardoor een herstelkandidaat aanleveren; eerder was alleen de eerste
projectpagina zichtbaar. De 512-rijengrens blijft expliciet: een langer
onbekend restant is geen bewijs dat het project geen productwerk heeft.

Er zijn geen extra workers toegestaan door deze bronselectie: alle bestaande
live lane-/scopeclaims, eigenaarstoewijzing, projectcaps, geheugendruk,
lease- en CI-/review-/deployvoorwaarden blijven ongewijzigd. De pagina's zijn
uitsluitend SELECTs op de bestaande databaseconnectie; geen nieuwe queue,
scheduler- of VPS-mutatie is uitgevoerd.


## 20 oktober · Klikbare operatorbronlinks zijn protocol-veilig

Een browser-HTML-`href` is niet veilig enkel omdat het attribute HTML-escaped
wordt: `javascript:` en `data:` blijven anders navigatie-/scriptmogelijkheden.
De bestaande backend biedt nu één gedeelde `_safe_operator_source_url`
voor bronlinks in de operationele queue en Attention Needed.

- Nieuwe queue-enqueues weigeren niet-canonieke of onveilige `source_url`
  waarden expliciet voordat SQLite wordt gewijzigd. Alleen begrensde absolute
  `http://` of `https://` URL's zonder spaties, control characters, backslash,
  embedded credentials of ongeldig poortnummer worden gebruikt.
- Een human attention item blijft wél bestaan als een oude bronlink onveilig
  blijkt; alleen het doorklikken naar die link wordt weggelaten.
- Ook eerder opgeslagen queue- en attentionitems worden **uitsluitend in de
  read-only API-presentatie** gefilterd, zodat een historische rij geen
  gevaarlijke `href` blijft afleveren. Historische bronbewijzen in SQLite
  zijn niet herschreven, verwijderd of als veilig gemarkeerd.
- De bestaande frontend, CSS, actieve sessies, notificaties, security gates
  en productieautoriteit zijn niet gewijzigd.

Deze broncode maakt geen URL-bezoek, externe fetch of mutatie van live
werkergroepen. De PR vereist nog steeds exact-head CI, onafhankelijke review
en goedgekeurde main/runtime-integratie.


## 20 oktober · Eén menselijk incident kan geen ander project overschrijven

In `portfolio_attention_add` geldt voor nieuwe of herhaalde menselijke
escalaties nu een canonieke incidentidentiteit van maximaal 160 tekens.
Een expliciet lege, gemanipuleerde of niet-canonieke ID wordt geweigerd in
plaats van stilzwijgend omgezet in een mogelijk reeds bezette sleutel.

De bestaande SQLite UPSERT kan een aandachtspunt met dezelfde ID nog steeds
updaten of opnieuw openen, maar **uitsluitend binnen het oorspronkelijke
project**. Wanneer dezelfde ID al bij een ander project hoort, faalt de
bewerking met een bruikbare foutmelding. Zo kan bijvoorbeeld een herstelalert
van Project B niet stilzwijgend de actuele contactactie en bron van Project A
vervangen. Er wordt geen bestaande aandacht opgelost of verwijderd.

Deze wijziging hoort bij dezelfde veilige bronlinkafhandeling en queue-ID-
beveiliging. De status in deze PR is uitsluitend source committed; geen live
attentionrecords, productiedatabase of actieve worker-claim gewijzigd.


## 20 oktober · Menselijke escalatie en beëindiging van werk zijn één transactie

Een `BLOCKED`-antwoord van een actieve worker kan een menselijke vervolgstap
vereisen. Voorheen zette `portfolio_queue_finish()` de taak **eerst definitief
op `dropped`**, waarna de bron in een tweede SQLite-transactie via
`portfolio_attention_add()` een `Attention Needed`-record aanmaakte.
Een ID-collision, SQLite-schrijfweigering of andere fout tijdens dat tweede
record kon een taak dus beëindigen zonder bijbehorende menselijke escalatie:
de HTTP-aanroep rapporteerde dan een fout terwijl de taak al weg was.

De implementatie gebruikt nu één gedeelde transactie op de bestaande
SQLite-connectie voor de `BLOCKED`-statusovergang én de bijbehorende
projectgebonden menselijke aandacht. De nieuwe
`_portfolio_attention_add_locked()` is dezelfde persistente
upsert als de bestaande openbare attentionfunctie, alleen met de
transactie van de bevoegde caller. Wordt het aandachtspunt geweigerd of
faalt een schrijfopdracht, dan blijven taakstatus, worker-slot,
lease, evidence en aandachtspunt **allemaal** onveranderd door rollback.
De gewone attention-API blijft dezelfde functie gebruiken, met een eigen
standaardtransactie.

De nieuwe `_queue_attention_id()` laat de bestaande `queue:<id>`-
referenties ongemoeid zolang ze onder de 160 tekens blijven. Voor een
geldige lange queue-ID (maximaal 160 tekens) genereert de bron in plaats
van een ongeldige 166-teken-sleutel een begrensde, deterministische
`queuehash:<SHA256>`-incident-ID. Geen stille truncatie, geen nieuwe
permissie, geen afname of overname van actieve workerkoppelingen.

Dit is code op de bestaande PR; geen live database-write of service-
herstart. Exact-head CI, onafhankelijke review en geserialiseerde
productierelease blijven ongewijzigd vereist.


## 20 oktober · Handmatige Drop kan geen actieve worker meer beëindigen

De vorige `portfolio_queue_drop()` accepteerde elke rij behalve `done` en
`dropped`. Daardoor kon een vertrouwde beheerclient ook een `claimed`,
`running` of `verifying`-taak op `dropped` zetten en diens
`worker_slot`, `claimed_at` en `claim_expires` wissen zonder de
claimhouder af te wachten. Dat was niet in overeenstemming met het
productdelivery- en exclusief-leasecontract.

De bron voert de DROP nu uitsluitend uit op een `queued`-rij zonder
worker-slot én zonder enig claim-/leaseveld. De controle gebeurt
**in dezelfde SQLite UPDATE** als de statuswijziging: ook een gelijktijdig
nieuwe claim kan niet worden teruggedraaid op basis van een verouderde SELECT.
Verlopen leases of inactief lijkende browsers vormen geen overnamerecht.

Wanneer de rij al een eigenaar heeft of de ID ontbreekt, blijft alles
ongewijzigd. De bestaande beheer-API geeft nu `ok=false`, HTTP `409`
en de vaste reden `unclaimed-queued-item-required` in plaats van een
misleidend `200 ok=true`. Bij een geweigerde drop volgt ook geen
refill/audit met mogelijk andere database-effecten. Alleen een werkelijk
gedropte vrije taak volgt het bestaande geautoriseerde refillpad.

Dit is broncode op de bestaande PR #1536; geen live queue/claim mutatie,
QA- of regressiesuite, workerherstart of release uitgevoerd.


## 20 oktober · Volledige vs gedeeltelijke afronding zichtbaar in queue-response

De bestaande `portfolio_queue_finish()` registreerde een echte
`DONE`/`BLOCKED`/`CONTINUE`-overgang, maar gaf vervolgens alleen
`updated=true` terug, ook wanneer het aanmaken van een vervolgtaak door
bestaande producer-/eigenaarsregels werd geweigerd of het schrijven van het
statusbewijs faalde. Daardoor kon een operator het succes van de
**statusovergang** verwarren met een volledige overdracht aan vervolgwerk.

De backend houdt de bestaande responsevelden en statusovergangen intact,
maar levert nu daarnaast drie strikt onderscheiden waarden:

- `continuation_status` is een vaste enum:
  `created`, `not_created`, `rejected_by_producer_guard`,
  `not_requested` of `original_task_requeued`. De exacte afwijsreden
  en gevoelige bronvelden worden niet in de publieke response gekopieerd.
- `state_receipt_saved` is alleen `true` wanneer de bestaande
  statusbewijsopslag daadwerkelijk zonder uitzondering terugkeerde.
- `handoff_complete` is alleen `true` wanneer het statusbewijs bewaard
  is én geen vereiste vervolgstap ontbreekt of is afgewezen.

Een ontbrekende continuation wordt niet automatisch als toegelaten of
uitgevoerd aangemerkt; de oorspronkelijke `updated=true` blijft
waarheidsgetrouw uitsluitend voor de database-statusovergang.
De bestaande beveiligde beheer-API, claims, PR-/CI-reviewvoorwaarden en
productiedatabase zijn niet gewijzigd buiten de nieuwe broncode.


## 20 oktober · Human-gated queue-audit houdt menselijke actie vast

De gewone `portfolio_queue_audit()` ruimt uitsluitend vrije, nog nooit
geclaimde queued-taken op, maar het oude pad verwijderde ook werkpakketten
waarvan het project `queue_mode=human-gated` is zonder een menselijke
escalatie te bewaren. Daarmee kon een werkpakket uit de wachtrij verdwijnen
terwijl de reden voor stilstand niet op de `Attention Needed`-lijst stond.

Voor een **daadwerkelijk door de audit verwijderde human-gated rij** wordt
nu een begrensd, projectgebonden aandachtspunt gemaakt in precies dezelfde
SQLite-transactie. Een `SAVEPOINT` per rij waarborgt dat een afwijzing van
de bron-/incidentidentiteit, een ID-conflict of een databasefout uitsluitend
die rij ongedaan maakt. Een nooit geclaimde taak blijft dan ongewijzigd
staan, zonder stilzwijgende statuswijziging of incomplete melding.
Andere onafhankelijke auditregels kunnen gewoon doorgaan.

Een taak die intussen is geclaimd kan niet aan dit pad deelnemen: de
SQL UPDATE verifieert `status='queued'`, `worker_slot IS NULL`,
`claimed_at IS NULL` en `claim_expires IS NULL` opnieuw bij de
daadwerkelijke schrijfopdracht. De melding bevat alleen een generieke
projectactie (geen prompt-, secret- of queue-tekst), een bestaande
source-URL volgens het veilige URL-filter en een deterministische
`queue:` of `queuehash:`-incident-ID. Geen workerclaim wordt gemaakt,
vrijgegeven of overgenomen. Productie en verplichte releasegates blijven
ongewijzigd.


## 20 oktober · Ongeverifieerde workerdone is geen CI- of main-bewijs

Het project-statusreceipt werd voorheen bij `portfolio_queue_finish(DONE)`
opgeslagen met `ci_status='success'`; bij `BLOCKED` stond
`ci_status='failure'` en bij `CONTINUE` `in_progress`. Deze waarden
beschrijven uitsluitend het antwoord van een browserworker en **zeggen niets
over de verplichte externe GitHub Actions-rapporten, een onafhankelijke
review, een geslaagde PR-merge of de live applicatie**. De bestaande
`project_runtime.apply_receipt()` gebruikt `ci_status` echter voor de
zichtbare project-executiestatus; hierdoor kon een eenvoudige taakafmelding
onjuiste CI-succesinformatie in het cockpitoverzicht tonen.

De workerqueue schrijft bij een taakafmelding geen geverifieerde CI-status
meer. Het oorspronkelijke `DONE`/`BLOCKED`/`CONTINUE` blijft
auditbaar in `action` en de brongebonden receipt-`evidence`; aan de
evidence worden `ci_evidence=not_observed_by_queue_transition` en
`main_merge_evidence=not_observed_by_queue_transition` toegevoegd. De
API-response vermeldt expliciet `ci_verified_by_queue_result=false` en
`main_merge_verified_by_queue_result=false`. Een werkelijke CI-/mergestatus
kan uitsluitend uit de desbetreffende geautoriseerde exact-head-bron volgen.

## 20 oktober · Minder SQLite-belasting per browser-allocatieronde

De echte scheduler riep voor verschillende global worker-slots opnieuw
`_portfolio_eligible_lane_candidates_locked()` aan. Sinds de
projecteerlijke selectie kon één schedulerpass daardoor herhaaldelijk vier
globale bronpagina's én vier aanvullende 128-rijenpagina's per mogelijk
verborgen project scannen, terwijl dezelfde bron-inventaris in die ene
`BEGIN IMMEDIATE`-transactie grotendeels onveranderd bleef.

Er is nu een **transactie-lokale, optionele ID-shortlistcache**: de dure
begrensde bronselectie wordt maximaal één keer gebouwd in een
`portfolio_queue_allocate()`-aanroep. Voor ieder volgend slot vraagt de
helper de actuele, werkelijk `queued`, geldige en claimvrije rijen voor
die hoogstens 240 IDs opnieuw op bij SQLite. Daarna worden
`_portfolio_lane_snapshot_locked()` en de bestaande lane-, claims-,
preemption-, Force Start-, geheugen- en projectcapregels op de actuele
transactiestatus toegepast. Een eerder in dezelfde ronde geclaimd of
vrijgegeven item wordt niet opnieuw toegewezen op basis van een
verouderd cache-record. Cachegegevens verlaten de transactie niet.

Voor bestaande `running`/`verifying`-slots die toch al niet mogen worden
gepreëmpt, wordt de herhaalde kandidaat-/laneselectie volledig overgeslagen
en de eigenaar volgens dezelfde bestaande beschermingslogica vernieuwd.
Buiten de allocator blijft de helper zonder cache werken zoals voorheen.
Er zijn geen nieuwe workerrechten, background loops, index-migraties,
workflowtriggers of live VPS-mutaties toegevoegd.


## 20 oktober · Menselijke BLOCKED-gate start geen afhankelijk vervolgwerk

Een browserworker kon in hetzelfde `portfolio_queue_finish()`-antwoord
`BLOCKED` met een menselijke blokkade én een voorgestelde `next_task`
aanleveren. De oude control-plane maakte dan toch een nieuw workerqueue-
item aan voordat de menselijke toestemming er was; de menselijke Attention
Needed-rij verhinderde niet dat die nieuwe opdracht via een andere worker
zou kunnen worden geclaimd. Dit is geen bewezen onafhankelijke taak.

Zodra `BLOCKED` een echte menselijke Attention Needed-actie oplevert,
wordt geen nieuwe `next_task` en ook geen generieke
`portfolio_write_continuation` vanuit **diezelfde geblokkeerde queue-ID**
gestart. De API meldt `continuation_status=held_for_human`,
`human_approval_required=true` en `handoff_complete=false`.
De individuele blocker en bestaande incidentbron blijven bewaard en
worden niet geautoriseerd door een voltooid statusreceipt.

Dit is geen globale schedulerpauze: bestaande aparte, ongeclaimde
productwerktaken kunnen normaal doorlopen binnen hun eigen claims,
cap- en autorisatieregels. Alleen automatisch vervolgwerk dat direct uit
deze menselijke blokkade voortkomt blijft onuitgegeven. De beslissing
vindt plaats in de bestaande taak-afhandelingsbron, zonder nieuwe
QA-gates of live workflow-, VPS- of claimactie.


## 20 oktober · Een menselijke blocker bewaart het concrete vervolgvoorstel

Een `BLOCKED`-taak met aangetoonde menselijke escalatie creëert geen
automatische afhankelijke werkopdracht meer. Als de oorspronkelijke
worker wel een gestructureerde `next_task.title` voorstelde, wordt die
titel nu bounded (maximaal 200 tekens, newline-genormaliseerd) bovenaan
de projectgebonden `Attention Needed`-details opgeslagen, voorafgegaan
door: `Voorgestelde vervolgstap na toestemming (NIET ingepland)`.
Dat betreft uitsluitend het bestaande geautoriseerde menselijke inbox-pad.
De beheerinterface ontsmet nog steeds de HTML-tekst van de detailregel.
Geen `next_task` wordt hierdoor ingeschreven of automatisch toegestaan;
de echte menselijke autorisatie en een latere vrije queue-opdracht zijn
afzonderlijke handelingen.

Een `BLOCKED` zonder menselijke actie kan nog steeds zelfstandig
uitvoerbaar vervolgwerk genereren binnen de bestaande productpolicy.
Andere reeds bestaande producttaken blijven op hun eigen lanes en
onder de eigen worker-/CI-/reviewgates lopen.


## 20 oktober · Ook automatisch gegenereerde vervolgopdrachten blijven product-only

De rechtstreeks door `portfolio_queue_finish(..., next_task=...)`
gecreëerde vervolgopdrachten gebruikten nog een oudere afsluitzin
met `automated test/build/run evidence`. Die kon een nieuwe worker ten
onrechte richting test- of QA-rondes sturen ondanks het geldende
bouwgerichte productbeleid. De nieuwe doorlopende opdracht verlangt nu
meerdere materiaalwijzigingen of één grote end-to-end oplevering en alleen
een build of directe functionele inspectie als aanvullende broncontrole.
De tekst verbiedt expliciet schrijven, uitbreiden of draaien van QA-,
regressie- en testsuites. Verplichte bestaande CI, onafhankelijke review
en geautoriseerde releasevoorwaarden blijven intact. De foutmelding bij
een niet-uitvoerbaar queuevoorstel gebruikt voortaan eveneens
productbouw, integratie en herstel in plaats van 'test' als toegestane
werkcategorie. Dit geldt alleen voor nieuw gegenereerde vervolgstappen;
actieve queueclaims en historische receipts zijn niet herschreven.

Het brede inventarisverzoek van andere open GitHub-product-PRs kreeg
een externe HTTP 504; er is geen merge, branch-protection-bypass,
retrigger of aanname over review-gereedheid uitgevoerd op basis van
dat incomplete resultaat.


### 21 oktober · Onvolledige actieve claimbewijzen zichtbaar vóór nieuw werk

De bestaande geautoriseerde `GET /api/portfolio-queue` geeft in
`delivery_policy` nu brongebonden tellingen voor **actieve**
`claimed/running/verifying` taken waar de zichtbare rij geen
`worker_slot`, `claimed_at` of `claim_expires` bevat. Deze drie
tellingen zijn onafhankelijk (één taak kan meerdere onvolledige velden
hebben). De velden zijn `active_missing_worker_slot_visible`,
`active_missing_claim_start_visible` en
`active_missing_claim_expiry_visible`. `project_activity` bevat
dezelfde teller per project; `active_claim_evidence_gap_projects_observed`
telt de afzonderlijke getroffen projecten.

In het begrensde operatoroverzicht verschijnen projecten met zulke
ontbrekende actieve claimbewijzen **vóór** projecten met gewone
nieuwe-productwerkdruk. Hun `operator_next_step` is
`owner_reconcile_active_claim_evidence_before_any_new_work`.
Een ontbrekend veld is **geen bewezen verlopen lease**, geen bewijs van
stilstand, en nooit autorisatie om een taak over te nemen, te herstarten,
vrij te geven of een worker toe te wijzen. De bestaande limiet van
twintig zichtbare projecten en de bronwaarschuwing
`visible_api_rows_only_not_full_queue` blijven behouden: nul
waarnemingen in een deelvenster bewijst niets over de hele queue.

Deze wijzigingen betreffen uitsluitend read-only presentatie in de
**bestaande PR #1536**. De runtime allocator en actieve/queued
database-rijen blijven ongemoeid. Productiegedrag vereist nog steeds
exact-head verplichte CI, onafhankelijke review, protected-main merge
en de reeds bestaande geautoriseerde deployroute. Geen QA-suites of
losse tests geschreven of gestart.
