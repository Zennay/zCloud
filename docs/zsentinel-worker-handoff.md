# zSentinel · privé workercontinuïteit zonder control-plane writes

## Wat is geleverd

Eén bestaand zSentinel workerobservatiepad produceert nu direct een
zelfstandige **Nederlandstalige HTML-cockpit** of **Markdown-handoff** op
basis van de reeds geïndexeerde `runner_events` metadata en de gekozen
worker/queue-indeling in de bestaande SQLite-database. Er is **geen**
zCloud HTTP-status-GET (die op bepaalde endpoints de SQLite kan
reconciliëren), GitHub-API, Notion-call, nieuw schedulerpad, timer,
runnerrestart of claimsysteemwrite nodig. De import draait binnen
`mode=ro` met `PRAGMA query_only=ON`, en vereist de bestaande
`runner_events_project_worker_id` en
`runner_events_project_worker_event_id` indexen.

Per project wordt de bestaande limiet van 8 workers gebruikt, maximaal
3 projectselecties. Per worker toont de cockpit hooguit twaalf recente
eventtypen met leeftijd en indicatoren of de gebeurtenis na de laatste
zichtbare `prompt-sent` plaatsvond. De raadpleging toont niet de
workerprompt, foutinhoud, chat-URL, conversation-ID, token, originele
queue-ID of raw assistant-output. Een actie wordt nooit zelf uitgevoerd.

## Direct lokaal gebruiken

Een bevoegde operator kiest een **al bestaand**, zelf beheerd privédossier
(mode 0700), met toegestane read-only toegang tot de bestaande
`history.db`. Bijvoorbeeld:

```sh
python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud \
  --format html \
  --output /private/authorized/zsentinel-workers.html

python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud \
  --format markdown \
  --output /private/authorized/worker-handoff.md
```

Alternatief `--format html` of `--format markdown` zonder `--output`
schrijft uitsluitend naar stdout. `--format json` en `--format text`
blijven compatibel. Voor `--output` is het pad expliciet en de
bestandsextensie passend; het bestand wordt alleen nieuw aangemaakt met
rechten 0600. Bestaande dossiers, symlinks, DB's, cursors en rapporten
worden niet overschreven. De bovenliggende map moet bestaan en 0700
zijn, eigendom van de uitvoerende gebruiker. Bij weigering wordt
**niet** teruggevallen op een onveilige schrijfroute.

Het HTML-resultaat is volledig **offline**, zonder scripts en externe
assets en met een restrictieve CSP. Het heeft bovenaan een
actie-inbox met concreet advies voor de bevoegde owner en daarna
uitklapbare historie; Markdown heeft dezelfde actievolgorde en
context. De operator kan documenten meenemen naar een menselijke review,
maar ze vervangen geen autorisatie of actuele claimcontrole.

## Betrouwbaarheid, zichtbaarheid en grenzen

Een recente browserblokkade zonder later activiteitssignaal verdient
andere aandacht dan een blokkade waarop een nieuwe `generation-progress`
of `generation-finished` volgt. Geen van beide bewijst **werkelijke
productdelivery**. Een browser-heartbeat kan een oudere progressregel
overschrijven; de read-only bron reconstrueert hoogstens een begrensde
eventtijdlijn, maar heeft **geen geverifieerd claim-epoch eventbewijs**.
Dit is daarom een operatorverbetering, **niet de control-plane reparatie
van issue #1342**.

De 12-eventselectie is geen complete historische inventaris. Het rapport
toont brontijd en meldt duidelijk wanneer deze ouder dan 10 minuten is.
Een eerder opgeslagen v1-bronmoment blijft leesbaar; ontbrekende nieuwe
velden worden als `ONBEKEND` behandeld, niet als nul blokkades.

De volgende velden blijven `false`: `owner_claim_verified`,
`worker_resumed_verified`, `repair_authorized`,
`recovery_authorized`, `continuation_verified`,
`actions_performed`, `database_write_authorized`.
Het dossier bevat geen retry-key, geen commandolijst en geen
`approve`-/`resume`-/`force`-knoppen. Menselijke approval, originele
eigendom, verplichte CI, VPS-rechten en beveiligingsgates mogen
hierdoor niet worden omzeild.

## Integratie

Deze uitbreiding zit op de bestaande zSentinel hoofd-PR
[#1563](https://github.com/Zennay/zCloud/pull/1563) en raakt alleen
`scripts/zsentinel_worker_progress_observer.py` en de nieuwe renderer
`scripts/zsentinel_worker_handoff.py`. De live cockpit behoudt zijn
bestaande component `worker_progress_html` en dezelfde read-only
databron. Er zijn geen QA-, regressie- of testsuites geschreven of
gedraaid. Een compile-check bewijst alleen geldige broncode, geen
werkende VPS-ingreep of vrijgegeven productie-integratie.

## Vergelijk twee bronmomenten, zonder herstel te verzinnen

Met `--format json --output` wordt een **nieuwe privé-snapshot**
create-only opgeslagen. Die kan later als `--previous` worden aangeboden
wanneer een verse read-only HTML/Markdown-handoff wordt gemaakt:

```sh
python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud --format json \
  --output /private/authorized/worker-20261020-01.json

# Alleen nadat de bron werkelijk opnieuw gelezen kan worden:
python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud --format html \
  --previous /private/authorized/worker-20261020-01.json \
  --output /private/authorized/worker-20261020-02.html
```

De vergelijking weigert verschillende projectselecties, identieke of
teruglopende observatietijdstippen, en vergelijking van meer dan 30 dagen.
Vorige JSON wordt begrensd gelezen (≤512 KiB), met `O_NOFOLLOW` en
weigering van dubbele JSON-keys, en door dezelfde bestaande
read-only schema-admission gevoerd. De vergelijking raakt geen
browser of productie-DB buiten de standaard huidige read-only scan.

De actielijst onderscheidt: opnieuw waargenomen; verandering van
bronclassificatie; nieuw *in de begrensde steekproef*; of verdwenen *uit
de begrensde steekproef*. **Geen** van deze uitkomsten betekent
dat een worker vrij, hersteld, door een nieuwe claim bezet, of als
productdeliverable voltooid is. Er ontbreekt een geverifieerd
claim-epoch-token en bewijs van echte GitHub/Notion-productoutput.
Daarom blijft `recovery_verified=false` in de vergelijkingssamenvatting.
De vorige JSON blijft ongewijzigd en de nieuwe uitvoer wordt nooit
over een bestaande JSON of HTML heen geschreven.

## Browservoortgang is gescheiden van de laatste heartbeat

Het read-only `runner_events` bronmodel bevat nu twee afzonderlijke
**geïndexeerde cursors**, die in tekst en HTML zichtbaar zijn:

- `latest_indexed_event_id`: de recentste waargenomen browsergebeurtenis
  op dit project/worker-slot (kan een gewone heartbeat zijn).
- `latest_indexed_progress_event_id`: de recentste expliciete
  `generation-progress` of `generation-finished` gebeurtenis binnen
  een begrensde indexed-lookback van 240 events. Een herhaald
  `progress_at`-tijdstip op een heartbeat telt niet meer als een nieuw
  expliciet progress-event.

De vergelijking tussen twee snapshots toont nu *afzonderlijk* of de
algemene eventcursor en progresscursor zijn gestegen, gelijk gebleven,
teruggelopen of onbekend. Daardoor wordt een stroom heartbeats niet
verward met nieuwe generatiesignalen. De onderliggende browser meldt
de gebeurtenis zelf; er is **geen** serverbevestigde claimepoch,
onafhankelijke teksthash, productcommit of work-item-verificatie.
Zelfs een toegenomen progresscursor kan nooit automatisch betekenen
dat een claim hersteld, afgerond of overdraagbaar is. Een regressie
van de cursor vraagt bron-/eigenaarsinspectie in plaats van
automatische workerreset.

Dit is een bruikbaar read-only voortgangsproduct naast het bestaande
P1-issue [#1342](https://github.com/Zennay/zCloud/issues/1342), maar
sluit de onderliggende control-plane writer/claim-token reparatie
nadrukkelijk niet.

## Queueclaim zichtbaar, maar lease verlopen: eigenaarcontrole vóór normale pauze

Eerder kon de read-only observer bij een toegewezen gepauzeerde worker
met een `claimed`, `running` of `verifying` queue-rij maar
**ongeldige/verlopen lease** alsnog `configured_paused_not_incident`
rapporteren, omdat de bestaande urgentiecheck alleen een **actueel
geldige** lease meenam. Daarmee verdween een waardevol herstelprobleem
uit de owner-inbox.

De nieuwe status
`allocated_queue_claim_expired_or_invalid_owner_recheck` is nu zichtbaar
in de **bronobservatie, prioriteitsinbox, HTML-kaart, tekst, Markdown
en historische vergelijking**. Hij heeft een aparte veilige route:
verifieer de originele claimrij, bronklok en lease bij de bevoegde
control-plane eigenaar. De observer beweert uitdrukkelijk **niet**
dat deze rij nog steeds een actuele eigenaar heeft of dat de claim
automatisch kan worden vrijgegeven. Geen `finish`, `retry`,
`force`, `resume`, `release` of lease-correctie wordt uitgevoerd.
Een opzettelijk gepauzeerde worker **zonder** dergelijke queue-status
blijft géén automatisch herstelincident.

Dit is een echte productmatige observer/recovery-triageverbetering,
geen vervanging voor de transactionele bounded retry- en
leasewriter-afspraken uit
[zCloud #677](https://github.com/Zennay/zCloud/issues/677).

## Browserblok zonder bekende opdrachtcontext

Een recente `send-blocked` of vergelijkbare browser-event mag
alleen als **blok na de laatste opdracht** worden gepresenteerd als
de geïndexeerde bron óók een eerdere `prompt-sent` heeft gezien en
het foutevent een hoger event-ID heeft. Een foutsignaal **zonder**
waargenomen `prompt-sent` krijgt nu de afzonderlijke categorie
`recent_browser_block_without_prompt_context_owner_review`.
Het blijft in de eigenaar-inbox, maar wordt nadrukkelijk
niet aan een concrete taak, claimepoch, deliverable of actuele
runnerstoring toegeschreven.

De handoff adviseert dan eerst opdrachtbron/toewijzing bij de
bevoegde eigenaar te controleren. Het verschil is zichtbaar in
JSON, tekst, Markdown en HTML; er is geen automatische herstelactie
en geen muterende browser- of runtimeopvraag nodig.
