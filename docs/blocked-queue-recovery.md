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
