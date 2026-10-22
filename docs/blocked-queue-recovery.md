# Parked dependency work in the portfolio queue

The portfolio queue distinguishes **blocked** work from **dropped** work.

- `DONE`: the assigned queue item is completed.
- `CONTINUE`: the assigned queue item returns to ordinary queue selection.
- `BLOCKED`: requires concrete dependency evidence (at least 12 characters). The exact original queue item is preserved as `status=blocked`, `eligible=0`; its worker slot and lease are cleared. The scheduler can then allocate unrelated executable work.
- `dropped`: an intentional terminal retirement. It is not the representation for a task waiting on a dependency.

A blocked result still creates an independent continuation under the existing queue lifecycle. The original task remains visible through `GET /api/portfolio-queue`, and the health response includes a separate `blocked` count. A read failure is not a zero count.

## Resume one parked task

Only a trusted admin client may send this action to `POST /api/portfolio-queue`:

```json
{
  "action": "resume_blocked",
  "queue_id": "the-exact-parked-queue-id",
  "resolution_note": "The dependency was resolved; the authorized owner checked the current source and confirmed the resolution."
}
```

The server requires an existing `blocked` row with `eligible=0`, a live non-human-gated project and a concrete operator note. It preserves the previous blocker and bounded evidence in the resume receipt, records the authenticated actor's attestation both on the queue item and in the project's state-receipt history, then returns the task to `status=queued`, `eligible=1`. The response explicitly says `operator_attestation_not_independently_verified`: this is an operator assertion, not automatic proof that the external dependency cleared.

Resume does **not** force a worker, reserve a slot, steal an active claim, bypass the memory guard, or skip normal scheduler lease/project-capacity checks. The task becomes a normal candidate and may remain queued until capacity is available. If the dependency is still unresolved, leave the task parked.

Billing/spending-limit holds, missing independent review, branch protection and owner-authorization blockers are mapped to specific human actions rather than generic alerts. Ordinary queued/pending CI is not classified as a failure solely because it is waiting.

The dashboard's **Dependency holds** panel displays parked tasks separately from human/external attention. The operator must confirm the dependency resolution and enter a note before using **Resume after confirmation**. Failed queue reads are shown as unavailable, never as “no blocked tasks”.

## Recovery integrity

- Preserve the original title, project, priority, source, dependency evidence and queue identity.
- Keep the task non-runnable while blocked.
- Continue independent work instead of waiting on the blocked task.
- Never infer that a blocked task is dropped, completed, or safe to resume from an old heartbeat, saved PR status, or historical CI observation.
- Do not create or run new QA/regression suites for this workflow. Existing required CI, review, authorization and release gates remain unchanged.

## Atomic human Attention lifecycle

When a worker reports `BLOCKED` for an issue requiring human action, the exact queue task becomes `blocked`/`eligible=0` and a `queue:<queue_id>` Attention card is inserted or updated in the **same SQLite transaction**. Either both writes commit or both roll back; a failed Attention write cannot silently strand an invisible blocked task. The queue item is retained with its dependency evidence and claim released, not dropped or reassigned. Unrelated work may continue under the existing independent-admission rules.

Human Attention classification matches explicit owner/review/permission/paired-device gates. Generic terms such as `watchdog` or `human-readable` alone do not establish human intervention. A physical-watch or paired-device gate still requires Attention.

Only an authenticated, authorized operator may use the existing `portfolio_queue_resume_blocked` flow after a concrete resolution note. That flow atomically restores this exact blocked task to `queued`/`eligible=1`, records an operator-attested (not independently verified) receipt and resolves only the associated `queue:<queue_id>` Attention card. The result includes `attention_resolved` to distinguish a previously open Attention card from none. It does not force a worker slot or claim the resumed task. Independent CI, review, permission and release gates still apply.

**Existing release HOLD:** [PR #1565](https://github.com/Zennay/zCloud/pull/1565) has a prior mandatory OOM-guard validation that expected the historic `dropped` state for a human-gated BLOCKED task; the actual product contract explicitly requires the durable `blocked` state with possible operator resumption. The stale expectation must be reconciled under the authorized review process. No QA/test source was modified or re-run as part of this change.

## Claim-safe periodic queue audit

The periodic queue audit may prune **only** `queued`, `eligible=1`, unassigned tasks that are no longer executable (for example, a newly human-gated project). Its UPDATE checks status, eligibility and `worker_slot IS NULL` again at write time. If a worker claims a task between selection and update, the audit cannot turn that active task into `dropped` or release its slot.

Already `claimed`, `running` or `verifying` tasks are **never silently dropped by audit**, even when their classification changes. Existing owners must finish or park them using normal claim-aware transitions. Both `portfolio_queue_audit(refill=False)` and the refilling mode return `protected_active` with the queue IDs they intentionally preserved. No claims, queue state or running workers are mutated simply by generating the report; the only mutation is the previously supported pruning of truly unassigned non-executable queued tasks. Refill continues to obey project modes and normal capacity/lease gates.

## Dashboard: an Attention card cannot hide its blocked task

For a queue-derived Attention (`attention_id=queue:<queue_id>`), the dashboard now presents **Confirm & resume** rather than an independent **Done** button. It requires the exact blocked task to be visible in the current queue read; if the queue read failed or the task is no longer blocked, the action is disabled. The existing resume dialog asks for a concrete resolution note and confirms that the operator is attesting, not independently verifying, the dependency. On a successful backend resume with `attention_resolved=true`, the dashboard removes both the parked task row and its Attention card before refreshing.

Ordinary, non-queue Attention notices retain their Done action. The click handler also refuses any stale queue-derived Attention Done event. This prevents an apparent green human-alert panel from hiding a task that remains `blocked`/`eligible=0`; it does not authorize release or preempt a worker.


## Dashboard: een HTTP 200 is geen hervatte taak

De resume-API kan bij een inmiddels gewijzigde status een veilige no-op
teruggeven: HTTP 200 met `ok=true`, maar `result.resumed=false`
en reden `not-blocked-or-already-resumed` of `state-changed`.
De UI mocht in dat geval niet de originele taak wegfilteren en de
knop tijdelijk als `Queued` tonen. Dat zou de operator onterecht
laten denken dat de geblokkeerde taak is hervat.

De interface vereist nu **alle drie** de contractvelden vóór optimistische
UI-opruiming: `result.resumed === true`, `result.status === "queued"`
en `result.queue_id` gelijk aan de geselecteerde exacte taak. Een
no-op of afwijkende respons leidt tot een nieuwe read-only queue-refresh
en een expliciet operatorbericht; oude lokale Attention-status wordt
niet als opgelost gemarkeerd. De backend blijft de werkelijke
autorisatie- en transactiebeslissing nemen; deze bronwijziging voert
zelf geen queue-mutatie, workerclaim of automatische resume uit.


## Brongebonden levering van de herstelknoppen

Het blokkadepaneel gebruikt wijzigingen in `public/app.js` en
`public/style.css`. De startpagina op deze PR verwijst daarom naar
het **exacte** Git-blobfragment van beide assets:

- `app.js`: `07cfcc746d71` — bevat de strikte
  `resumed=true` / `status=queued` / exact-`queue_id` afhandeling.
- `style.css`: `2fd8df1b45ca` — bevat de bijbehorende
  Dependency holds- en Attention-presentatie.

De ongewijzigde `enhancements.js` en `enhancements.css` behoudt de
reeds bestaande referenties. Deze versies voorkomen dat dezelfde
HTML-route een oude, elders gecachete dashboard-JS/CSS URL blijft
gebruiken. Bij een volgende UI-bronwijziging **moet** de versiepin
opnieuw overeenkomen met de nieuwe blob. Browsercache-headerbeleid,
exact geleverde productiebytes en daadwerkelijke activering blijven
onder de bestaande UI-owner en geautoriseerde releasegates; de
bronverwijzing is niet automatisch bewijs van live deployment.


## Alle geblokkeerde taken zichtbaar, P0 eerst

Het dashboard toont de geladen `blocked`-rijen nu op prioriteit
(P0, P1, P2, P3, daarna onbekend), met stabiele bronvolgorde
binnen elke klasse. Standaard blijven twaalf kaarten zichtbaar voor
mobiele leesbaarheid. Bij meer resultaten verschijnt `Show next 12`
voor opeenvolgende pagina's uit **dezelfde reeds gelezen bron**,
en aan het einde `Show first 12`. De teller toont hoeveel items
in de **geladen wachtrijsteekproef** staan, niet een ongecontroleerde
claim over de volledige database. Dit pagineert uitsluitend de
browserweergave: geen nieuwe queue-write, verborgen auto-resume,
lease-overname of providerrequest.

Een onbereikbare queue toont expliciet `unavailable`, geen
`0 blocked`. Echte aantallen boven de standaardkaartlimiet zijn
niet meer onbereikbaar; ook minder recente P0/P1-items worden
boven laagprioritaire cards getoond. De gewijzigde `app.js` wordt
via de bijgewerkte exact-blobversie in `public/index.html` geladen.


## Snelle P0/P1-triage zonder claimwijziging

De `Only P0/P1`-knop filtert lokaal de **reeds gelezen** geblokkeerde
queue-items naar urgente P0- en P1-holds. De kaartteller in de header
blijft het totale aantal uit de steekproef; een aparte toelichting
maakt het geselecteerde deel zichtbaar. De knop `Show all priorities`
herstelt zonder netwerkactie de volledige bronselectie.

Elk filterwissel zet de kaartpaginering terug naar de eerste twaalf;
`Show next 12` navigeert door de gefilterde selectie. De filter
schrijft geen wachtrij-, claim- of Attention-toestand en verandert
geen autorisatie voor hervatten. Bij geen urgente items geeft het
scherm expliciet aan dat dit **alleen de geladen steekproef** betreft.


De P0/P1-filter en pagineringsknoppen gebruiken een zichtbaar actieve
status (`aria-pressed`), toetsenbordfocus via de bestaande focusring,
en minimaal 44px aanraakhoogte op mobiel. De stylesheetpin in
`public/index.html` is mee veranderd naar de nieuwe CSS-blob;
oudere cached styles vormen daardoor niet stilzwijgend de nieuwe
operatorinterface.


## Hercontrole vlak vóór hervatten

De bediener kan bij het lezen van een `blocked`-taak en het bevestigen van
de benodigde externe actie langere tijd in het bevestigingsvenster zitten.
Een latere statusupdate of nieuwe dependency-evidence maakt die oude
operatorbevestiging mogelijk ongeldig. Vóór een `resume_blocked` POST
verplicht de bestaande dashboardclient nu een **nieuwe read-only**
`GET /api/portfolio-queue`. Hij vergelijkt de exacte `queue_id`,
`project_id`, titel, prioriteit, `blocked`-status, `eligible`,
blocker en evidence met de kaart die de operator aanvankelijk
bevestigde.

Bij onbereikbare of onvolledige bron, niet-exact-een match of een
gewijzigd afhankelijkheidscontract wordt de POST **niet verstuurd**,
wordt de laatst leesbare queue getoond en moet de operator de actuele
situatie opnieuw bevestigen. Twee knoppen voor dezelfde `queue_id`
kunnen niet tegelijk een verzoek starten in één browser-tabblad.
Ook ná de POST wordt nog steeds een exact success-receipt vereist:
`resumed=true`, `status=queued` en dezelfde queue-identiteit.

Deze lokale hercontrole sluit gelijktijdige wijzigingen **tussen**
de GET en de POST niet atomair uit: uitsluitend de bestaande backend
met authentieke operator en transactionele statusgate beslist of de
hervatting plaatsvindt. Er is geen schedulerclaim, automatische
herstart of toestemming voor externe afhankelijkheden toegevoegd.
De nieuwe `app.js` is gebonden aan `index.html` met de exacte blobpin.


## Projectgericht afhankelijke taken hervatten

Dezelfde Dependency holds-werkbak biedt nu ook een projectkeuzelijst
voor alle projecten met geblokkeerde taken in de **op dat moment
geladen** wachtrij. Combineer deze selectie met `Only P0/P1`
en `Show next 12` om een specifiek project af te handelen zonder
andere portfolio-taken uit beeld te verwijderen. De teller in de
sectiekop blijft het **totale** aantal geblokkeerde taken in de
geladen API-lijst; de werkbalk vermeldt hoeveel er aan de lokale
filters voldoen. Een verdwijnende bronproject-ID wordt niet
stilzwijgend als eigenaarstoewijzing behandeld: de selectie valt
alleen dan terug op 'All projects'.

Filteren of naar een volgende pagina gaan veroorzaakt geen nieuwe
providerreads, claims, queue-mutaties of automatische hervattingen.
Vlak vóór een daadwerkelijke `resume_blocked` moet de browser de
originele taak en afhankelijkheid nog steeds opnieuw live lezen.
De dropdown en knoppen werken via de bestaande toegankelijke
focusring, toetsenbordselectie en mobiel 44px-aanraakdoel; actuele
app.js- en style.css-blobpins zijn bijgewerkt op de startpagina.


## Transactionele bronversie bij operator-resume

Een browser kan na zijn verse GET nog een race verliezen tegen een
andere scheduler-/operatormutatie voordat de hervattings-POST aankomt.
Daarom stuurt de huidige dashboardclient **naast** een concrete
resolutienotitie ook `expected_updated_at`, `expected_blocker` en
`expected_evidence` uit dezelfde nieuw gelezen `blocked`-rij.

De backend vergelijkt deze waarden binnen de bestaande
`BEGIN IMMEDIATE` SQLite-transactie met de exact geïdentificeerde
geblokkeerde taak, **vóór** de `blocked → queued`-update en het
afsluiten van de bijbehorende Attention-kaart. Als één waarde afwijkt,
retourneert de bestaande 409/no-op response
`reason=dependency-source-changed-reconfirm`. Er wordt dan geen
queue-/Attention-update en geen operator-receipt geschreven.
Na een mislukking of ontbrekende bron probeert de client de huidige
queue opnieuw read-only op te halen; zonder betrouwbare queuebron
blijven hervatknoppen uitgeschakeld.

Het succesvolle antwoord vermeldt
`dependency_source_guarded=true`; de dashboardclient accepteert
alleen dit antwoord naast de reeds vereiste exacte taakidentiteit
en `resumed=true`. Bestaande externe operatorclients zonder deze
optionele guards behouden hun bestaande backendcontract,
zonder dat zij nieuwe autorisaties krijgen. **De browser en server
moeten als één geautoriseerde productrelease** worden geleverd:
een oude server kan de nieuwe guards niet afdwingen.
Geen onafhankelijke dependency-resolutie wordt beweerd,
en de operatorbevoegdheden veranderen niet.

## 22 oktober · brongebonden zoekwerkbank en hervat-freshness

De **Dependency Holds**-werkbank heeft naast de bestaande projectkeuze
en P0/P1-filter nu een lokale zoekbalk over titel, project, prioriteit
en vastgelegde afhankelijkheid. De UI geeft het **gefilterde aantal**
apart van het totaal uit de geladen queuebron, bewaart de focus tijdens
typen, en biedt **Clear filters**. Een nulresultaat is een lege
zoekselectie binnen de ontvangen steekproef, **niet** het bewijs dat er
elders geen geblokkeerde taken zijn. Er zijn geen aanvullende
bevoorrechte API-verzoeken nodig. De bron blijft alleen-lezen totdat
dezelfde geautoriseerde operator expliciet een individuele taak
bevestigt.

De pre-POST vergelijking van een geparkeerde taak gebruikt nu tevens
`updated_at`, `worker_slot`, `claim_owner` en `claim_expires`.
Bij een wijziging tussen de eerste bevestiging en de verse
queue-observatie wordt de hervatting afgebroken en een nieuwe
menselijke bevestiging gevraagd; de transactionele bronversiecontrole
van de backend blijft daarnaast verplicht. Dit is geen toestemming
om een active claim over te nemen.

De HTML-pins voor `app.js` en `style.css` moeten bij elke nieuwe
wijziging aan die assets opnieuw worden ververst. Een commit op deze
featurebranch is geen productie-installatie: pas na exacte-HEAD
verplichte CI, onafhankelijke review, branch protection en toestemming
van de release-eigenaar mag worden gemerged of geactiveerd.

### Bronknop op de afzonderlijke blokkadekaart

Voor een geparkeerde taak met een `source_url` die exact verwijst naar
`https://github.com/Zennay/<repo>/issues/<nummer>` of
`https://github.com/Zennay/<repo>/pull/<nummer>`, verschijnt nu
**Open source issue / Open pull request**. Hiermee kan de operator
meteen de oorspronkelijke afhankelijkheid bekijken vóór een
hervattingsbevestiging. Aangeleverde willekeurige URLs, queryfragmenten,
userinfo, andere hosts en niet-HTTPS-protocollen krijgen **geen**
klikbare kaartlink. Linknavigatie verandert geen claim of queuebron,
en de daadwerkelijke backend-hervatcontrole blijft onafhankelijk.
De zoekbalk accepteert ook het exacte opgeslagen `source_url` als
zoekterm, maar haalt geen nieuwe privé-informatie op.

## 22 oktober · bronmoment, operatorcontext en private handoff

De kaarten in **Dependency Holds** tonen na uitklappen nu de oorspronkelijk
geregistreerde wachtrijaanmaak, het laatste queue-update-moment, het aantal
geobserveerde claimpogingen en of de geladen status een gekoppelde menselijke
Attention bevat. Claim attempts betekenen niet automatisch mislukkingen.
De bronregel toont de daadwerkelijk meegeleverde `time` van
`GET /api/portfolio-queue`; ontbrekende/tijdelijk onbereikbare bronnen
worden niet als actuele of lege wachtrij gepresenteerd.

De zichtbare hervatknop vereist `status=blocked`, `eligible=false`,
geen worker-slot en geen resterende lease. De backend bewaakt daarnaast de
exacte taak, dependency-velden en versie in zijn transactie. Een afwijkende
bron geeft geen stilzwijgende taakoverdracht of hervatting.

De operator kan met **Export selected handoff** na uitdrukkelijke lokale
bevestiging een JSON-bestand downloaden van maximaal honderd geobserveerde,
door huidige project/urgentie/zoekfilters geselecteerde blokkades.
Formaat `zsentinel.dependency-holds.local-handoff.v1` bewaart
waarnemingstijd, exporttijd, gefilterde aantallen, expliciete truncatie,
canonieke bronlinks, begrensde queue-/project-ID's, prioriteit,
Attention-indicator en niet-autoriserende eigenaarvervolgstap.
De uitvoer is maximaal 100 KiB. **Geen ruwe blocker-, evidence- of
titels worden geëxporteerd.** Het bestand is lokaal en kan
identificerende interne metadata bevatten: controleer het vóór delen.
Dit is een niet-ondertekende bronsteekproef, geen toestemming,
claimbevestiging, queue-retry, workerhervatting of productie-release.
Bij onbeschikbare bron of ontbrekende geldige waarnemingstijd weigert
de export. Er is geen extra API-writer, workflowdispatch of VPS-actie.

### Menselijke Attention afzonderlijk filteren

De Dependency Holds-werkbank biedt aanvullend **Only human Attention**.
Dit gebruikt uitsluitend `attention_needed` uit het reeds geladen
zCloud-statusantwoord en matcht uitsluitend de exacte `queue:<queue_id>`
Attention-koppeling bij een geparkeerde taak. Zonder bruikbare
Attention-bron wordt de filteractie uitgeschakeld; er wordt nooit
geconcludeerd dat menselijke blokkades ontbreken omdat de bron
ontbreekt. De filter combineert met P0/P1, projectkeuze en zoeken.
**Clear filters** wist alle vier keuzes. Een handmatige privé-export
neemt dezelfde geselecteerde rijen en noteert de actieve
Attention-filter in het niet-autoriserende bronpakket.
Geen nieuw achtergrondproces, alert, claim, queue-write of automatische
hervatting.

De implementatie toont bij een verdwenen `attention_needed`-bron onder
een nog ingeschakelde Attention-filter expliciet **bron onbekend**,
in plaats van stilzwijgend alle taken als passend te presenteren.
Ongeldige of meer dan twee minuten toekomstige bronklokken zijn geen
geldige waarnemingstijd en kunnen niet als privé-exportbron dienen.
Een inconsistente geparkeerde rij met `eligible=true`, een worker-slot
of een resterende lease krijgt geen hervatknop; de concrete
inconsistentie staat bij de operatorcontext. De server blijft de
uiteindelijke autorisatie en transactie bewaken.

## 22 oktober · projectpagina's, portfoliosignalen en veilige Attention-links

De bestaande `public/app.js`-client toont de geobserveerde blocked
queue nu ook op **iedere afzonderlijke projectdetailpagina**. Maximaal
zes taakrijen per project zijn direct zichtbaar, oplopend op P0/P1;
daarna brengt **Open project holds in portfolio workbench** de operator
naar het portfolio, met precies dat project geselecteerd. De detailrij
biedt de oorspronkelijke blocker, brongebonden GitHub-issue/PR,
geobserveerde bron-/updateklok en bekende human-Attention-koppeling.
De bestaande `resume_blocked`-actie blijft dezelfde geauthenticeerde,
gecontroleerde backendtransactie. Ontbrekende queuebron is expliciet
`unavailable`, niet nul. Een rij met nog actieve lease of worker-slot
heeft geen beschikbare hervatknop.

De portfolioprojectkaarten tonen bij geparkeerde taakrijen nu ook het
**aantal parked** en het deel **P0/P1**. Alleen het reeds geladen
queuevenster wordt geteld; dit is geen volledig databasebewijs en geen
extra providerverzoek. De dashboard-`index.html` pin is gelijk aan de
bijbehorende `app.js`- en `style.css`-blob op deze branch.

Daarnaast wordt een ontbrekend `attention_needed`-veld nu als
**Human-action source unavailable** weergegeven, in plaats van de
onjuiste claim **Nothing needs you**. Bij Attention-bronlinks worden
alleen HTTPS-URLs zonder userinfo/afwijkende poort als klikbaar gerenderd;
`javascript:`, `file:` en andere onveilige URI-schema's kunnen niet
als operatorlink vanuit de kaart worden geopend.

Alle onderdelen zijn bronwijzigingen op bestaande PR #1565 en worden
niet als live of op `main` geleverd beschouwd totdat dezelfde
oorspronkelijke CI-/review-/protected-main- en releasevoorwaarden zijn
afgehandeld. Geen QA-suites, handmatige workflowtrigger, operatorresume,
queue-/claim-/SQLite-write of VPS-servicehandeling.

## 22 oktober · hoog aantal claimpogingen zonder automatische retry

De Dependency Holds-werkbank en projectdetailkaarten signaleren nu
`attempts >= 3` uitsluitend als **waargenomen herhaalde claims**,
nooit als drie gefaalde runs of een vastgestelde retrybudgetschending.
Bij deze taak verschijnt een brongebonden operatorhint en een directe
link naar open [zCloud-issue #677](https://github.com/Zennay/zCloud/issues/677).
De handmatige `Only repeated claims`-filter combineert met P0/P1,
project, tekst en menselijke Attention; de afzonderlijke privé-JSON
neemt de huidige filterselectie expliciet op. Een lege selectie
betekent alleen nul matches in de geladen steekproef.
`Clear filters` zet deze keuze ook terug.

De onderliggende lifecycle-writer, `attempts`-kolom, SQLite,
actieve claimlease, replay/retrybudget en `FAILED_RETRYABLE` /
`NEEDS_AI`-contract zijn **niet** gewijzigd; #677 behoudt zijn
eigen serialized runtime-owner- en migratievoorwaarden.
De GitHub-bron op deze bestaande featurebranch is geen
runtime-bewijs of mergeautorisatie.

## 22 oktober · issue #677: actieve claimchurn inspecteren zonder retry

De eerdere parked-only monitor toont niet het kernprobleem uit
[issue #677](https://github.com/Zennay/zCloud/issues/677): er kunnen
bijvoorbeeld wachtrijtaken met een hoog aantal claimpogingen nog
`queued`, `claimed`, `running` of `verifying` zijn. De nieuwe
**Claim Churn**-inbox gebruikt daarom dezelfde reeds geautoriseerde
`GET /api/portfolio-queue`-snapshot zonder extra leesverzoek.
Hij bevat **alleen** deze vier actieve/planbare statussen met
`attempts >= 3`, toont het actuele `worker_slot` bij geclaimde
statussen en laat de huidige eigenaar ongemoeid. Claimpogingen zijn
geen foutteller en `>=3` is een **triagehint**, niet het
geïmplementeerde retry-budget uit #677.

De portfolio-weergave geeft P0/P1-first kaarten, een filter voor
P0/P1 en voor status en een incrementele **Show next 12**-weergave.
Op iedere afzonderlijke projectpagina verschijnen maximaal zes
actieve claimchurn-kaarten, ongeacht eerder gekozen
portfoliofilterwaarden; **Open portfolio claim churn** opent de
volledige read-only werkbank. Kaarten bevatten prioriteit, status,
worker-slot of uitdrukkelijk onbekende eigenaar, laatst geobserveerde
queuewijziging, beperkte taaktekst en alleen canonieke
GitHub-bronlinks. Als de actuele wachtrijbron niet gelezen kon worden,
is de status **source unavailable** in plaats van 0 taken.
Er worden geen claim-/release-/retryknoppen getoond.

Met **Export private handoff** kan een operator na expliciete
bevestiging de zichtbare portfolio-filterselectie naar een **lokaal**
JSON-bestand exporteren met schema
`zsentinel.claim-churn.readonly-handoff.v1`. De output bevat maximaal
100 regels en 100 KB en toont expliciet aantal geobserveerde
wachtrijregels, geselecteerde items, ontbrekende items door
truncatie, brontijd en filterkeuzes. De inhoud bevat alleen
queue-/project-ID's, prioriteit, status, geregistreerd aantal
claimpogingen, geobserveerd worker-slot, laatste update,
canonieke GitHub-issue/PR-link en een read-only owner-vervolgstap.
**Ruwe titels, opdrachttekst, blocker en evidence ontbreken** in de
download. Het bestand kan nog interne IDs en worker-slots bevatten;
controleer het vóór verder delen. Zonder geldige bron- of
waarnemingstijd is export onmogelijk.

Dit productoppervlak neemt **niet** de native retry-planner, backend
writer, historische attempts-migratie, nieuwe FAILED-/NEEDS_AI-staten,
claimlease of de door #677 vereiste rollback/owner-review over.
Source branch ≠ main ≠ live. Bestaande exact-head CI,
onafhankelijke review, mergebeveiliging en release-eigenaarschap blijven
ongewijzigd. Er zijn geen QA-tests geschreven of uitgevoerd.

### Lease-interpretatie op het geobserveerde bronmoment

Voor `claimed`, `running` en `verifying` vergelijkt de read-only
Claim Churn-kaart het geobserveerde `claim_expires` met de
`time` die de geautoriseerde queue-GET zelf leverde:
`expired-at-observation`, `valid-at-observation` of `unknown`.
`queued` betekent `not-applicable`, omdat het geen actieve
queueclaim is. Een verstreken lease is hoogstens een
**triagesignaal**, geen toestemming om een worker te vervangen of
stiekem werk opnieuw uit te geven. **Only expired leases** is een
extra statusfilter, en de private claimchurn-export bewaart de
waargenomen leasebeoordeling en vervaltijd zonder ruwe taakinhoud.

Wanneer een nieuwe `/api/status`-verversing faalt, worden beide
queuebronnen (de blocked-only kaart en de volledige actieve snapshot)
expliciet ongeldig gemaakt en vervangen door een onbekende
bronstatus. De browser laat dan geen oude queue als 'live' staan,
en kan geen vervallen operatoractie op basis van die oude snapshot
aanbieden. Een nieuwe succesvolle geautoriseerde queue-GET vult
de informatie opnieuw. Er wordt geen actieve claim aangepast of
herstart. Dit zijn codewijzigingen op de bestaande, nog niet
gemergede product-PR; geen productie-installatie.

### Portfolio-at-a-glance voor actieve claimchurn

De bestaande projectkaarten laten naast blocked task counts nu de aantallen
**repeated claims** voor actieve/planbare taken zien, en waar zichtbaar
het aantal leases dat **op het bronobservatiemoment** reeds was
verstreken. Dit is uitsluitend een telling over de al geladen
geautoriseerde queuebron. Bij ontbrekende queuebronnen verschijnt
geen misleidend nul-label. De herstelwerkbank zelf laat de
onderliggende taken en huidige worker-slots zien. Alleen de eigenaar
van een bestaande claim kan die muteren. Geen automatische
cancel/force/requeue-actie en geen privédataset op een nieuwe API-route.

## 22 oktober · beschermde terminale blokkadesluiting en menselijke Attention

Naast brongebonden hervatten kunnen geautoriseerde operators nu een
**definitief achterhaalde geparkeerde taak afsluiten** met de expliciete
knop **Close permanently…**. Dit is geen automatische herstelactie en
mag alleen worden gebruikt wanneer het oorspronkelijke werk werkelijk
niet meer hoeft te worden uitgevoerd; een nog bestaande dependency is
op zichzelf geen sluitreden. De operator voert een concrete
reden (minimaal 12 tekens) en een tweede bevestiging in.
De reeds bestaande `POST /api/portfolio-queue`-actie `drop` krijgt
daarbij de exact waargenomen `status=blocked`, `updated_at`,
`blocker` en `evidence` mee. De client leest vóór de POST de
geautoriseerde queue opnieuw en vergelijkt tevens de taakidentiteit,
claimtijd, worker-slot, lease, prioriteit en oorspronkelijke bron.
Een andere taakversie of onvolledige autorisatie weigert de sluiting.

**Servercontract:** binnen dezelfde `BEGIN IMMEDIATE`-transactie
controleert `portfolio_queue_drop` zelf de bronversie en
afhankelijkheid, de te sluiten status en het ontbreken van iedere
worker-slot, `claimed_at` of `claim_expires`. Daardoor kan geen
huidige of inconsistente actieve claim via de generieke `drop`-API
worden beëindigd. Ook oudere operatorclients zonder de optionele
bronvelden mogen **alleen niet-geclaimde `queued`/`blocked`**
taken terminal sluiten; afwijzingen geven HTTP 409 en veroorzaken geen
refill. Er is geen nieuwe API-privilege: `action_request_allowed`
en de bestaande netwerkauthorisatie blijven leidend.

Een geldige terminale overgang naar `dropped` plaatst de
handmatige actor en sluitreden **vooraan** in de maximaal 4000 tekens
tellende queue-evidence, met daarachter een beknopte bronhistorie.
De **volledige, oorspronkelijk opgeslagen blocker/evidence** (maximaal
respectievelijk 2000 en 4000 tekens), eerdere `updated_at` en
`source_url` blijven afzonderlijk in de project-state-receipt
traceerbaar. Een afzonderlijke project-state-receipt
met `source=portfolio_queue_drop` wordt **in diezelfde
SQLite-transactie** geschreven. Dit is een taakannulering, geen
bewering dat de volledige project-CI geannuleerd is: de
`ci_status`-waarde van deze receipt blijft daarom leeg.
De uitsluitend bijbehorende `queue:<queue_id>` Attention krijgt
tegelijk `resolved`; alle andere Attention en lopende taken blijven
onaangeroerd. Het API-antwoord vermeldt afzonderlijk
`dropped=true`, `status=dropped`, `source_guarded=true` en
`attention_resolved`. De client accepteert nooit alleen HTTP 200
als bewijs van succesvolle sluiting.

**Losse waarschuwingen kunnen niet worden weggedrukt:**
`portfolio_attention_resolve` weigert voortaan een rechtstreeks
`queue:<id>`-resolve zolang de oorspronkelijke taak nog
`status=blocked` heeft. Alleen de transactionele hervat- of
sluitroute ruimt de waarschuwing op. De resume-transactie zelf
weigert nu ook een vergeten `claimed_at`/worker-slot/lease,
zelfs bij een oudere client zonder optimistic source guards.
De bestaande UI gebruikt dezelfde beschermingsvoorwaarden voor
alle hervat- en sluitknoppen.

**Levergrens:** bovenstaande veranderingen staan uitsluitend op
bestaande PR #1565, niet automatisch op `main` of productie.
Bestaande exact-head mandatory CI, onafhankelijke review,
protected-main en release-autorisatie blijven verplicht. Er zijn
geen QA- of regressiesuites toegevoegd/gedraaid, geen workflow
geretriggerd, geen live queue-, Attention-, SQLite-, worker- of
VPS-mutaties uitgevoerd. De definitieve annulering komt alleen tot
stand wanneer een afzonderlijke geautoriseerde operator deze
nieuwe interface ná release bewust gebruikt.

## 22 oktober · claim-integriteit en bronversheid in de operatorcockpit

De read-only **Claim Churn**-inbox (issue #677) onderzoekt nu niet
uitsluitend het aantal claimpogingen en de verlopen leases, maar ook
de **consistentie van de werkelijk opgeslagen eigenaarvelden**.
Een `queued` taak met achtergebleven `worker_slot`, `claimed_at`
of `claim_expires` verschijnt als *inconsistent claim fields* in
plaats van *No current queue claim*. Bij een actief `claimed`,
`running` of `verifying` item ontbrekende worker/claim/expiry of
een onmogelijke leaseperiode, toont de werkbank een afzonderlijke
owner-reviewwaarschuwing. Een valide lease die bij het oorspronkelijke
bronmoment al verlopen was, blijft eveneens een waarschuwing.
Geen van deze classificaties is toestemming om een eigenaar over te nemen.

**Only owner/lease warnings** is een aparte lokale selectie binnen de
reeds gelezen `attempts >= 3` claimchurn-steekproef. De bestaande
`Only expired leases`-filter blijft beschikbaar, ook gecombineerd
met prioriteit/status. Het paneel toont de waarschuwingsteller en
neemt `claim_integrity`, `needs_owner_review`,
`claim_anomaly_filter` en het waargenomen brontijdstip mee in de
al bestaande privé-JSON-handoff. Het v1-envelope, de bestaande
`lease_status_at_observation`-waarde en de bronidentiteit blijven
ongewijzigd; het zijn uitsluitend aanvullende velden. Geen
nieuwe server-API, extra providerrequest, claim-, retry- of queue-write.

Een tweede operatorrisico was een **oud tabblad**: een eerder succesvolle
queue-GET kon na lange tijd nog als gewone momentopname worden weergegeven.
De claimchurn-, portfolio Dependency Holds- én projectdetailweergave markeren de bron daarom als
**STALE** zodra het waarnemingsmoment meer dan 15 minuten oud is.
Beide handoffs schrijven nu expliciet `source_freshness.state` en
`age_seconds` weg; voor de Dependency Holds-export waarschuwt
de bestaande handmatige bevestiging extra bij stale data. De
alleen-lezen export geeft nooit toestemming voor actie; de al
bestaande verse GET, bronfingerprint, servertransactie en
authenticatie blijven verplicht vóór hervatten of permanent sluiten.

**Bronintegratie:** `public/app.js` Git-blob
`fd8d48963a3ec9177c1c17129eeda3c7d2cf5401` is via de
exacte assetpin `sha=fd8d48963a3e` in `public/index.html`
op dezelfde PR geïntegreerd. De bestaande `style.css`-pin is
ongewijzigd. Dit betreft alleen broncode op [PR #1565](https://github.com/Zennay/zCloud/pull/1565);
geen merge of productiedeploy. Exact-head CI, onafhankelijke review,
branch protection en runtime-/releaseeigenaar blijven leidend.
Er zijn geen QA-/regressietests of workflow-reruns gestart.

## 22 oktober · onafhankelijk Owner Integrity-overzicht voor iedere queueclaim

De eerdere **Claim Churn**-inbox detecteerde eigenaar-/leasewaarschuwingen
alleen op `attempts >= 3`. Daardoor konden een nieuwe actieve claim
zonder owner-slot, een onmogelijke lease bij de eerste claim, of een
ongebruikte `blocked`-taak met overgebleven leasevelden buiten het
overzicht vallen. De nieuwe **Owner Integrity**-werkbank gebruikt
rechtstreeks dezelfde al geautoriseerde `GET /api/portfolio-queue`,
zonder nieuwe backend-route of bevoegdheden.

De werkbank beoordeelt alle in de bron aanwezige `queued`, `blocked`,
`claimed`, `running` en `verifying`-taken, ongeacht attempts.
Hij signaleert afzonderlijk:

- non-active taken met achtergebleven owner-slot/claimtijd/lease;
- `blocked` die niet veilig `eligible=0` en volledig ongeclaimd zijn;
- actieve taken zonder geldig positief worker-slot;
- ontbrekende of niet parseerbare claimtijd/lease-expiry;
- expiry die niet later is dan claimtijd, of een claimtijd die onmogelijk
  voorbij het geobserveerde bronmoment ligt;
- expiry die **op het oorspronkelijke bronmoment** al was verlopen;
- ontbrekende/ongeldige bronklok, zodat leasebeoordeling niet als
  bewezen gezond verschijnt.

De resultaten zijn **advisory source-observaties**: geen enkel
waarschuwingslabel is autorisatie om een claim, lease, taak, scheduler
of worker te muteren. Ook een verlopen lease blijft van de huidige
eigenaar totdat de bestaande autorisatie- en transactieregels een
overgang expliciet toestaan.

**Complete operatorworkflow:** een aparte responsive lijst op portfolio
en projectdetail, P0/P1 eerst, canonieke GitHub-issue/PR-link indien
veilig, waarschuwingen per taak, paginering, P0/P1- en projectkeuze,
directe sprong vanaf projectdetail naar die projectfilter en
eigenaarwaarschuwingsbadges op de projectkaarten. De teller betreft
*taken met minimaal één waarneming*; een taak kan meerdere signalen
hebben. Een ontbrekende queuebron is **UNKNOWN**, nooit nul.
Een gevonden nulresultaat betreft uitsluitend de geladen queue-snapshot,
geen volledig database- of continu leaseonderzoek.

**Veilige menselijke overdracht:** via bewuste bevestiging maakt de
browser `zsentinel.owner-integrity.readonly-handoff.v1` aan, beperkt
tot 100 waargenomen records en maximaal 100 KB. Het JSON-document
bevat het oorspronkelijke queuebronmoment, source freshness,
scope-/afkapgegevens, veilige canonieke bronlink, exacte geselecteerde
queue-/project-ID, optionele worker-slot en begrensde waarschuwingcodes.
Onbeheerde titels, blockers, vrije evidence en ruwe runtime-logs komen
**niet** in de privé-export. Oudere bronmomenten worden expliciet
als STALE gemarkeerd; de export is geen toestemming voor actie.

**Bestanden en bronversie:** `public/app.js` blob
`c3c4d81b8542b49a6e94fef654665992a9dd1e4b`,
`public/style.css` blob
`cf7eee51415b330fa8f306dd16ecaf5b188deb66`; beide exact
uit `public/index.html` geladen op PR #1565. Geen handmatige
workflowdispatch, QA-suites, database/claim/runner- of releaseactie.
De productbron is nog niet op main en niet live geverifieerd; originele
exact-head CI, onafhankelijke review, protected-main en geautoriseerde
runtime-deploy blijven verplicht. Native retry/backoff/writer van
[issue #677](https://github.com/Zennay/zCloud/issues/677) is **niet**
geïmplementeerd en behoudt zijn eigen control-plane eigenaarschap.

## 22 oktober · volledig transactioneel broncontract voor Resume en Close

Een geautoriseerde operator ziet een taak in de bestaande Dependency
Holds-werkbank. De browser haalt vóór een mutatie de queue opnieuw op
en stuurt bij **Close permanently** nu óók de project-ID, exacte titel,
prioriteit en oorspronkelijke bron-URL mee. De backend controleert
deze velden samen met `status=blocked`, `eligible=0`,
`updated_at`, `blocker` en `evidence` in **dezelfde**
`BEGIN IMMEDIATE`-transactie die de terminale UPDATE doet.
Een inkompleet guard-pakket wordt vóór de mutatie geweigerd; een
veranderde bron geeft `dependency-source-changed-reconfirm`.
Ook de SQL UPDATE zelf eist bij zo'n brongebonden sluiting nogmaals
een werkelijk `blocked/eligible=0` item zonder worker-slot,
claimtijd of lease. Ongewijzigde, geauthenticeerde oudere clients
behouden de strengere ongeclaimd-only legacyroute.

Een onbekende project-ID wordt bij terminale sluiting niet langer
stilzwijgend gepasseerd. Zonder actuele projectregistratie kan de
beloofde gelijktijdige project-state-receipt niet worden aangemaakt:
het item behoudt dan zijn bestaande evidence en de API geeft
`missing-project-receipt-context`, zonder refill.
Een misvormde `blocked/eligible`-bron geeft
`blocked-task-not-parked`. De dashboardclient legt beide
afwijzingen begrijpelijk uit en laat de actuele eigenaar beslissen.
Eerdere queue-ID's, project- of bestandsclaims worden niet
automatisch herverdeeld.

De bestaande `resume_blocked` accepteert volledig onbewaakte
verzoeken van oudere, geautoriseerde beheerclients maar vereist bij
**elke** brongebonden bevestiging dezelfde volledige
bronversie en taakidentiteit:
`expected_updated_at`, `expected_project_id`, `expected_title`,
`expected_priority`, `expected_source_url`, `expected_blocker`
en `expected_evidence`. Deze gegevens worden vóór hervatten
in dezelfde SQLite-schrijftransactie vergeleken.
Geen gedeeltelijke nieuwe broncontrole kan ongemerkt een andere
taak of andere blocker bevestigen. De legacycompatibiliteitsroute
vormt nog steeds geen zelfstandige verificatie van een dependency.
Bij daadwerkelijk hervatten bevat de project-receipt niet langer
`ci_status=queued`: een queueovergang is geen nieuw CI-resultaat.

Tot slot volgt na een geldige permanente sluiting de bestaande
queue-audit/refill **buiten de al gecommitteerde transactie**. Een
uitzondering in deze latere vervolgstap kan het definitief gesloten
item niet terugdraaien. De API geeft nu alsnog de ware
`dropped=true`-receipt met
`queue_audit_deferred=true` en registreert de schedulerfout;
de dashboardclient meldt dat de eigenaar de normale toelating moet
onderzoeken en **niet** dezelfde sluiting herhalen.
Een afgewezen/no-op-sluiting triggert nooit refill.

Dit is productbron op [PR #1565](https://github.com/Zennay/zCloud/pull/1565),
geen geautoriseerde live SQL-, scheduler-, claim- of releaseactie.
Verplichte exacte-head CI, onafhankelijke review, protected-main en
release-eigenaarschap blijven intact. De eerder gemelde
`blocked` versus `dropped` OOM-guard-afwijking is hiermee
niet verhuld of omzeild; contractbesluit en CI blijven noodzakelijk.

## 22 oktober · actuele read-only herstelinspectie per geblokkeerde taak

De bestaande Dependency Holds-werkbank op portfolio- én projectdetail
heeft nu **Inspect current recovery conditions**. Dit is een apart,
**alleen-lezen** productonderdeel waarmee een operator de actuele
situatie kan bekijken *zonder* een bevestiging te geven, een claim te
wissen of een taak te hervatten. Het inspecteren verandert geen
queue-/Attention-/runner-/projectstatus en maakt geen receipt of
workflowdispatch.

De knop gebruikt de al bestaande, met
`action_request_allowed` beschermde `POST /api/portfolio-queue`
met uitsluitend `action=inspect_blocked` en `queue_id`.
`portfolio_queue_recovery_inspect` leest de taak én de uitsluitend
bijbehorende `queue:<id>`-Attention via twee `SELECT`-statements in
één coherente SQLite-readtransactie. Er is geen `UPDATE`, claim,
leaseverlenging, audit/refill of afhankelijkheidsattestatie. Er wordt **geen aparte publieke
endpointprivilege** gemaakt. Voor een ontbrekende rij retourneert
de inspectie expliciet `found=false`, niet een verzonnen
beschikbaar herstel.

De actuele diagnose omvat: werkelijke taakstatus en bronversie,
`eligible`, ieder nog vastzittend `worker_slot`/
`claimed_at`/`claim_expires`, projectregistratie, actieve
projectstatus, human-gated-modus en de exacte gekoppelde menselijke
Attention-status; daaruit volgen uitsluitend **read-only**
`resume_candidate`/`close_candidate`-indicaties.
Concrete `reason_codes` verklaren onder meer: taak niet meer
geblokkeerd, malformed `blocked/eligible`, nog gekoppelde
werknemer/lease, ontbrekende projectregistratie, gepauzeerd
project en human-gated-project.

De interface toont die uitkomsten onder de exacte taakkaart,
inclusief oorspronkelijk observatiemoment. Als de bronstatus of
`updated_at` inmiddels verschilt van de geladen kaart, wordt
duidelijk gewaarschuwd om te verversen. De indicaties zijn **geen
transactie-, dependency-clearance-, privacy- of releasebewijs**:
elke mutatie vereist daarna nog een zelfstandige menselijke reden,
verse complete bronfingerprint, autorisatie en de bestaande
`BEGIN IMMEDIATE`-controle. Zelfs een verlopen lease blijft van
de huidige eigenaar tot een onafhankelijke bevoegde overgang.

Geïntegreerde bestanden:
`server.py`, `public/app.js`, `public/style.css` en
`public/index.html`. De HTML laadt expliciet de exact gepinde
GitHub-blobs  `app.js=d7db6a119af1` en
`style.css=87b0cd17c5a2`. Dit blijft werk op de bestaande
[PR #1565](https://github.com/Zennay/zCloud/pull/1565),
niet op `main` en niet op productie. De bestaande falende OOM-guard,
onafhankelijke review en echte release-autorisatie zijn
niet omzeild; er zijn geen nieuwe QA-suites geschreven of uitgevoerd.

**Transactie-uitkomst versus bereikbaarheid:** de browser houdt een door
serverreceipt bevestigde `resumed/queued` of `dropped` overgang
voortaan gescheiden van de daaropvolgende dashboardverversing. Als die
latere GET wegvalt, meldt de interface expliciet dat de taakmutatie
wél bevestigd was en niet opnieuw mag worden verstuurd. Bij een
transportbreuk ná het versturen maar vóór een betrouwbaar antwoord
maakt de UI geen aanname over succes of falen: `outcome unconfirmed`
met instructie tot onafhankelijke herlezing van de queue en duurzame
operatorreceipt. Alleen een expliciet ontvangen brongebonden 4xx-
afwijzing geldt als bewezen weigering. Geen blinde retry of nieuw
recht wordt geïntroduceerd.

**Resume-bewijsbehoud:** het gedocumenteerde operatorattest staat nu
vóór de oude blocker/evidence in het maximaal 4000 tekens lange
queue-evidenceveld; een lange historische bron kan daardoor niet
meer de nieuwe menselijke beslissing wegdrukken. De bijbehorende
transactionele projectreceipt ontvangt de oorspronkelijke blocker
(max. 2000 tekens) en evidence (max. 4000 tekens) apart en behoudt
de expliciete notitie. De bestaande receipt-UTF-8-limiet en afwijzing
bij overmaat blijven leidend; er vindt geen stille overschrijving
van een actieve workerlease plaats.

### Source-linked recovery receipt visibility

Dezelfde read-only inspectie opent expliciet een SQLite-URI
met `mode=ro` en `PRAGMA query_only=ON`, zodat deze route nooit
zelf een ontbrekende database mag aanmaken of schrijfrechten krijgt.
Binnen dezelfde coherente SQLite-leestransactie leest hij naast de taak en diens exacte `queue:<id>`-Attention ook
maximaal **24 recente** `project_state_receipts` voor
`portfolio_queue_drop` en `portfolio_queue_resume_blocked` van
datzelfde project. Alleen receipts met hetzelfde `queue_id` in
gestructureerde evidence tellen mee; een titelmatch is onvoldoende.
De UI toont uitsluitend type besluit (`resume`/`close`),
receipt-ID en observatietijd. Geen vrije operatornotitie, actor,
bronevidence of geheime runtimevelden worden opnieuw gepubliceerd.

Dit helpt een operator bij onzekere netwerkuitkomsten, maar een recente
receipt is **niet** automatisch bewijs dat diens eigen niet-beantwoorde
verzoek slaagde. Als de receipts niet beschikbaar zijn, meldt de
read-only inspectie dat expliciet. Als het bounded venster vol is, wordt
de dekking als beperkt gemarkeerd. De actie knoppen hebben nog steeds
hun eigen verse broncontrole, onafhankelijke menselijke beslissing,
bestaande beveiligingscontrole en transactionele writercontract.


## Separate task-claim lease guard · 22 October

The portfolio queue row is not the only source of ownership. A worker can
leave a separate `task_claims(project_id, claim_key)` record behind even when
the queue row has been marked `blocked` and its `worker_slot`,
`claimed_at`, and `claim_expires` fields were cleared. Portfolio allocation
uses the queue ID as that claim key, so recovery must inspect both records.

The queue snapshot now reads the separate task claim for blocked items in the
same read-only SQLite snapshot and exposes only `task_claim_state`,
`task_claim_source_available`, and its lease expiry. Recovery inspection,
resume, and terminal close all refuse if the claim source is unavailable, a
live claim exists, a malformed lease or conflicting project identity cannot be
classified, or **any claim record remains after expiry**. Expiry alone is not
authorization to take over;
the current owner must reconcile/release the stale record first. No recovery
path prunes, releases, or overwrites a task claim.

The dashboard disables resume/close controls unless both the queue row and
the separate task-claim source prove the task unbound. Hold cards and the
private handoff show claim state and observed lease expiry without exposing
owner IDs. This is still only a read-only observation; the actual writes recheck the claim
inside the same `BEGIN IMMEDIATE` transaction as the guarded state change.

## Stale queue snapshots cannot authorize operator actions

The dashboard distinguishes the **age and integrity of the queue observation** from the task-claim state itself. A queue GET is safe to *view* when old, but must not make an operator action appear ready.

- Missing or invalid observation timestamps, timestamps more than two minutes in the future, and snapshots older than 15 minutes disable **Resume after confirmation** and **Close permanently** on every blocked-task card. Queue-linked Attention's **Confirm & resume** follows the same rule. A separate task_claims record, unavailable claim source, non-parked queue row, or unknown lease also keeps these buttons disabled.
- Operators can use **Refresh queue source** on the Dependency holds panel. It only reads the existing authenticated GET /api/portfolio-queue endpoint, independent of history/host telemetry. No claim, task, Attention, lease, retry, or production state is written.
- A returned HTTP 200 with a stale/missing observation time remains **unavailable** for recovery, not a successful fresh-source receipt. Failed targeted refresh discards the outdated local queue snapshot and displays an explicit error instead of presenting the former data as current.
- Even when a visible card is fresh, resume and terminal close repeat the GET **after** the human confirmation, require a fresh observation time, check the original task fingerprint, and pass the existing exact-state/dependency-version guards to the authorized server transaction. A recent timestamp only permits the *confirmation workflow*; it never proves that a dependency is resolved or authorizes releasing someone else's active claim.
- The existing backend's independent atomic guard, admin permissions, receipt provenance, review and release policy remain authoritative. The 15-minute client window is a user-interface fail-closed boundary, not new authority or a scheduled recovery mechanism.

This ships on the existing zSentinel PR #1565 branch until all exact-head required CI, independent review, branch protection and release ownership conditions permit a real main merge. No QA/regression suites were written, changed or run for this feature.

### Concurrent dashboard reads and independent recovery admission

- The dashboard preserves the current queue observation when an older all-project history refresh completes after a targeted source refresh, a failed status refresh, or a human recovery operation. A source-generation counter invalidates older queue completions; unrelated history panels still update.
- Targeted queue reload remains read-only and is not run concurrently with an already pending operator mutation. Both resume and permanent-close handlers invalidate older observation completions when they enter and leave the guarded flow. New operator recovery/terminal-close actions are serialized across **all** blocked rows while one operator action or queue refresh remains in progress; a second click produces a visible wait-for-current-result message, not a concurrent POST. Independent background read-only evidence collection is unaffected.
- An operator-confirmed action first requests the existing read-only `inspect_blocked` decision on the exact queue ID. The returned source observation must be recent, carry the same project/queue version and report the correct `resume_candidate` or `close_candidate`. Active/malformed separate claims, missing project registration, inactive projects and human-gated resume are refused before a mutation request. This inspect step is an **advisory product guard**, never a replacement for the second current GET or the backend's atomic authorized write checks.
- Project overview badges, parked-task headline and claim summaries distinguish **current** versus **last observed** queue evidence. A stale historical warning does not imply an active owner, current claim, confirmed incident or release authorization.
- Source-only changes are integrated on the existing PR branch. The historically recorded OOM-guard `blocked` versus `dropped` acceptance mismatch remains a separate required-CI/reviewer HOLD; do not edit or bypass the required test gate to hide it.


## Observation-coherent claim triage and release asset pinning

The existing dependency-hold dashboard and its linked Attention cards are joined
to the **same** read-only portfolio queue source. Repeated-claim telemetry is a
per-task allocation counter; three or more recorded claims **does not** identify
a production retry bug or establish that [zCloud issue #677](https://github.com/Zennay/zCloud/issues/677)
affects that task. The operator screen only links that task's own allowlisted
issue/PR when the queue record provides one; it never attaches #677 by default.

Queue observation timestamps must be RFC3339/ISO8601 with an explicit timezone.
Invalid timestamps, an observation clock more than two minutes in the future
and observations older than fifteen minutes cannot authorize UI recovery controls.
Unknown and stale snapshots are labeled as historical in the blocked worklist,
project summaries, repeated-claim panel and private export confirmations.
Read-only historical owner/lease counters do not prove a current owner, current
failure or recovered worker. Operators may fetch a fresh source using the scoped
queue refresh; the existing backend transaction still decides whether any
authenticated action is accepted.

The dashboard entrypoint `public/index.html` points its `/app.js?...&sha=`
cache key at the **first 12 hex characters of the Git content blob SHA**
for the exact bundled `public/app.js`. When the JavaScript source changes,
update that pin alongside the product change; this prevents the browser from
reusing a previously cached script after an authorized deployment. The CSS pin
is not changed when its source blob is unchanged. A source-only pin change is
not evidence of a deployed website.

These are source-side changes to the existing integration PR #1565. Required
exact-head CI, independent review, protected-main merge, authorized runtime
admission and live verification remain separate. No operator claim, queue,
runner, VPS, release workflow or billing setting was changed by this work.


## Queue identity admission: protect existing assignments

The queue's trusted `enqueue` API used to perform `INSERT ... ON CONFLICT(queue_id) DO UPDATE`
unconditionally. A replayed identifier could rewrite the title, priority,
project ID, criteria, source and `updated_at` of a **blocked, assigned or
completed original task** while its execution status remained unchanged.
This mismatched the source receipts and made a protected task appear to have
different work or a different owner.

The existing server-side `portfolio_queue_enqueue` now uses one
`BEGIN IMMEDIATE` transaction to inspect the original queue row and the
separate `task_claims` source before inserting or updating an identifier.
It applies these rules:

- A previously unseen canonical identifier creates a normal queued task
  **only if** the separate task-claim source proves no recorded claim exists.
- A duplicate identifier for a different project or parent task is refused.
  No cross-project/parent reassignment occurs.
- Duplicates of blocked, claimed, running, verifying, done, dropped,
  ineligible or still lease-bound tasks are refused. Existing fields, source
  evidence and owner/claim state are preserved; there is no lease pruning.
- A safely unassigned, eligible **queued** task may have its title, priority,
  criteria or source updated under that same transaction **only if it has never
  been claimed or acquired evidence** (`attempts=0`, empty `evidence` and
  `blocker`). A task returned to queued via `CONTINUE` retains its historical
  identity and receipts even if its current worker/lease fields are clear.
  An unchanged duplicate still returns the original without a write.
- A duplicate whose requested fields already match is **idempotent**:
  it returns the existing task without changing `updated_at` or creating
  artificial activity/churn. The API result exposes
  `enqueue_admission=existing_unchanged`, not an invented new task.
  Successfully created rows expose `created`; updates to an untouched queued
  item expose `updated_unclaimed`. These response markers are advisory
  descriptions of the completed transaction, never scheduling permission.
  The worker-completion state receipt additionally carries the bounded
  `next_task_queue_id` and `next_task_enqueue_admission` when a follow-up
  was admitted. This distinguishes an existing unchanged task from new
  continuation creation in durable project progress evidence; missing
  follow-up admission remains null rather than a fabricated success.
- Explicit queue identifiers must be 1–160 characters of canonical ASCII
  `[A-Za-z0-9._:-]`. The server rejects whitespace, Unicode/confusable
  aliases, invalid punctuation and overlong identifiers instead of silently
  sanitizing or truncating to another task's ID. Default generated IDs keep
  their stable project/hash identity.

Denied admission returns the existing operator-facing validation error;
the caller must resolve source/ownership and, if necessary, submit a
**different genuine task** under its own identity. It must not claim,
overwrite, release or restart another owner's work.

This is a branch-level product source repair in PR #1565. It does not
authorize new workers, a release, queue mutation on the VPS, CI bypass,
or any recovery without existing operator permission and mandatory gates.
