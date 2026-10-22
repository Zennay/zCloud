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

## Twee snapshots: verdwenen workers blijven zichtbaar (#1342)

De read-only worker-eventvergelijking `scripts/zsentinel_worker_progress_delta.py`
toonde eerder alleen worker-slots die in **beide** metingen voorkwamen.
Daarmee verdween een niet meer gesampled of nieuw verschenen slot uit
de concrete eigenaaracties, hoewel een totaalteller wel veranderde.

Nu vergelijkt het werkbord de **vereniging** van beide begrensde
samples. Per betrokken project en slot wordt vastgelegd of het in
de vorige en/of huidige bronwaarneming aanwezig was. Een nieuw slot
krijgt `new_in_bounded_sample_not_new_claim`; een niet langer zichtbaar
slot `missing_in_current_sample_not_resolved`. Beide leveren expliciet
een eigenaaractie op, maar **nooit** een conclusie over nieuwe claim,
voltooide taak, vrijgekomen slot, herstelde runner of veilige takeover.
Het aantal gedeelde slots wordt afzonderlijk en correct berekend.

De gewone tekst en offline HTML tonen deze ontbrekende/nieuwe
waarnemingen, inclusief dezelfde disclaimer. Daarnaast geldt een
striktere eventcursorregel: een voor het eerst waargenomen cursor is
**geen** nieuw event tussen de meetpunten. Een progress-event telt pas
als nieuw wanneer het na de bekende vorige progresscursor ligt, of
(als die ontbrak) aantoonbaar na de vorige hoogste algemene
eventcursor. Een zichtbare cursor zonder die continuïteit krijgt
`first_cursor_observed_not_delta` en een eigenaaractie, nooit vals
`generation_progress_signal_observed`. Voor een actieoverdracht
kan de operator vanuit twee reeds bestaande privé-observaties ook
een **nieuwe privé Markdown-werkbak** laten maken:

```sh
python3 scripts/zsentinel_worker_progress_delta.py \
  --previous /bestaande-prive-map/worker-1.json \
  --current-snapshot /bestaande-prive-map/worker-2.json \
  --format md --output /bestaande-prive-map/worker-eigenaaracties.md
```

De Markdown bevat per slot de bronclassificatie en concrete opvolgactie,
samen met tijdstippen en de aantallen gedeeld/nieuw/verdwenen.
De privé HTML- en Markdown-werkbakken tonen nu **eerst verdwenen
workers, daarna mogelijke bronresets, eigenaarspauzes en stille running
workers**. Technische stagecodes zijn vervangen door duidelijke
Nederlandse operatorlabels. Deze sortering is uitsluitend een
leesvolgorde, geen automatische prioriteit, claim of escalatie.
De bestaande create-only 0600-bestandsgrens geldt ongewijzigd; er
wordt geen bron overschreven en geen queue, worker, lease, GitHub,
Notion of VPS-service gewijzigd. Een verdwenen slot is in deze
beperkte bron **geen herstelbewijs**.

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


## 22 oktober · actuele queue-claimvenster versus oudere worker-events

De read-only workerobserver valideert nu zowel `claimed_at` als `claim_expires`
voordat hij een queueclaim als huidig beschrijft. Beide timestamps moeten geldige
tijdzones hebben, het claimvenster moet de observatietijd omvatten en de
waargenomen duur mag niet negatief of langer dan zeven dagen zijn. Een ontbrekende
of ongeldige starttijd is dus geen geldige lease meer.

Bij een geldige lease krijgen de huidige queue-rij en het project/worker-slot een
niet-omkeerbare SHA-256-vensterfingerprint op basis van de queue-ID,
`attempts` en `claimed_at` (microsecondenprecisie wanneer aanwezig). Een
leaseverlenging verandert de fingerprint niet; een nieuwe poging of claimstart
wel. De ruwe queue-ID, eigenaar en tijdstempels worden niet in het
operatorrapport opgenomen. Voortgang, prompt- en browserblokkadesignalen uit
eerdere claims worden niet aan het huidige claimvenster toegeschreven; de
read-only HTML en tekst tonen welke events binnen/buiten het venster vallen.

**Belangrijke grens:** `runner_events` bevat geen queue-ID. Dit is daarom
uitsluitend tijdvenster-correlatie, expliciet `time_window_only_not_identity_bound`,
en geen exacte claim-epoch-binding of herstelautorisatie. Bij een gewijzigd
venster tussen twee snapshots weigeren `zsentinel_worker_progress_delta.py`, `zsentinel_worker_continuity.py` én
`zsentinel_worker_handoff.py` weigeren een cursorvergelijking over gewijzigde
claimvensters als dezelfde taak en maken een afzonderlijke eigenaarcontrole
zichtbaar. Productoplevering,
claimovername, retry, restart en release blijven onbewezen en onbevoegd.


## Toegewezen worker zonder betrouwbare queueclaim (22 oktober 2026)

Een browser kan nog `heartbeat` of `generation-progress` melden nadat zijn
queueclaim is vervallen of gewisseld. Een toegewezen `running` slot mag
hierdoor niet automatisch als gezonde, taakgebonden voortgang verschijnen.
De read-only observer meldt nu aparte eigenaaracties wanneer:

- er **geen actieve claim** voor het toegewezen globale slot wordt gevonden;
- er **meer dan één actieve claim** op hetzelfde slot wordt waargenomen;
- de queue-bron ontbreekt of een benodigde kolom (`queue_id`,
  `claimed_at`, `claim_expires`, `attempts` enz.) ontbreekt.

Deze gevallen krijgen respectievelijk
`allocated_no_active_claim_owner_review`,
`allocated_multiple_active_claims_owner_review` en
`allocated_queue_source_unavailable_owner_review`.
De eigenaaractie verschijnt in de bestaande tekst-/HTML-inbox én in de
historische continuïteitsweergave. `paused` en `draining` behouden hun
bestaande owner-hold; een wel aanwezige maar vervallen of inconsistente lease
blijft onder de bestaande specifieke leaseclassificatie.

Dit is **geen** automatisch herstel: browser-events hebben nog steeds geen
server-geverifieerde queue-ID/claim-epoch. De worker wordt niet vrijgegeven,
herstart, geclaimd of herverdeeld. De bevoegde queue-/runtime-eigenaar
controleert de echte lease en de oorspronkelijke PR voordat actie volgt.
Geen QA-suite of workflowrerun is onderdeel van deze bronwijziging.


## Geen taakcontinuïteit zonder twee vergelijkbare leasevensters

De bestaande delta-CLI, historische tweevenstervergelijking en privé-HTML/
Markdown-handoff weigeren nu **cross-task** event-/progresscursordelta's als
een van beide momentopnames geen geldig queueclaimvenster heeft. Een wijziging
van de private claimvingerafdruk behoudt de specifiekere bestaande melding.
De delta-CLI zet de `event_cursor_advanced_observed`- en
`explicit_progress_cursor_advanced_observed`-indicatoren voor zulke
momentopnames op `false` en toont in plaats daarvan
`claim_window_unavailable_requires_owner_recheck`. De historische
weergave toont `UNKNOWN_no_current_queue_claim_window`; de privé-handoff
meldt dat de cursorvergelijking is ingehouden.

De claimvingerafdruk is slechts een privaat, temporeel leasevenster; het
browser-event zelf draagt nog geen betrouwbare queue-ID. Zelfs twee gelijke
vingerafdrukken zijn daarom **geen** productoplevering, geverifieerde
claim-eigendom of toestemming om te herstellen of te deployen.


## 22 oktober · einde-tot-einde incidentkaarten zonder ontbrekende vertaling

De observer classificeert drie concrete gevallen afzonderlijk: geen actieve queueclaim
op een toegewezen slot, twee actieve claims op hetzelfde slot, en een ontbrekende
queuebron/vereiste schemakolom. De bestaande HTML-validator accepteert alle drie
classificaties en hun bijpassende Nederlandstalige eigenaarvervolgstappen; de
visuele kaarten krijgen de waarschuwingstijl. De private runtime-handoff hergebruikt
**dezelfde bronvalidatie**, zodat deze incidenten geen blanco pagina of afgewezen
operatoroverzicht meer opleveren en de actiekaart geen willekeurige broncode toont.

Bij weergave wordt het UTC-bronmoment opnieuw beoordeeld: ouder dan tien minuten
of meer dan twee minuten in de toekomst krijgt een expliciete
**historisch/onbetrouwbaar-bron**-waarschuwing, ook in de zelfstandige tekstuitvoer.
De signalen blijven in de historische sample voor audit, maar mogen geen actuele
queueclaim, werkhervatting of herstelautorisatie bewijzen. Er zijn geen
actieve runner-, queue- of workerwijzigingen.


## 22 oktober · historische workerincidenten blijven leesbaar, niet actueel

De standalone worker-HTML en tekstweergave bepalen de geldigheid van het
opgeslagen UTC-bronmoment bij het **tonen**. Bij ouder dan tien minuten of
meer dan twee minuten in de toekomst wordt de vroegere urgentielijst als
**historische signalen** gepresenteerd. De afzonderlijke workerkaarten blijven
zichtbaar voor reconstructie, maar hun oude actiecodes worden niet als actuele
blokkade-eigenaarschap gepresenteerd. De operator maakt een nieuwe toegestane
read-only bronwaarneming alvorens een actuele herstelbeslissing te nemen.

Wanneer de bron expliciet is aangevraagd maar tijdelijk onleesbaar blijkt,
maakt de gebundelde runtime-handoff een P2 eigenaaractie voor verificatie van
de SQLite-bron of het schema; hij leidt geen vrije slot of voltooide taak af.
Geen QA-suites of handmatige workflows zijn onderdeel van deze implementatie.
