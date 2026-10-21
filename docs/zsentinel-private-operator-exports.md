# zSentinel · privé live operator-export

Deze bestaande zSentinel live cockpit kan nu rechtstreeks een **nieuw lokaal
0600-bestand** schrijven. Een handmatig kalenderbestand is daarmee direct
bruikbaar voor menselijke opvolging; een offline HTML-bord of JSON-/tekst-
overdracht kan vanaf **dezelfde eenmalige bronlezing** bewaard worden.

```bash
# Read-only GitHub-only observatie: queue, claims en workerstatus blijven UNKNOWN.
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format ics --output /private/zsentinel-human.ics

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format html --output /private/zsentinel-operator.html

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-evidence.json

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format text --output /private/zsentinel-owner.txt
```

**Bediening en grenzen.** Maak zelf vooraf een alleen voor de bevoegde operator
toegankelijke doelmap. Gebruik telkens een nog niet bestaande bestandsnaam;
dezelfde naam opnieuw gebruiken of een symlink overschrijven wordt geweigerd.
Met `--output` is er geen ruwe reportdump op stdout. De gekozen `--format`
en extensie moeten overeenkomen: `html/.html`, `json/.json`, `ics/.ics`,
`text/.txt`. `--format desk` blijft de aparte bestaande
`--desk-output NEW.html` route. Bestaande `--handoff-output` en
`--disk-hotspots-output` blijven expliciete afzonderlijke privé-uitvoer.
`--output` mag geen alias zijn van een bestaande invoer, ledger,
projectregistry, eerdere diskbron of ander rapportpad. De output-writer
weigert overschrijven en gebruikt een nieuwe `O_EXCL|O_NOFOLLOW` 0600 file
(maximaal 6 MiB). Een mislukte uitvoer wordt niet als geslaagde rapportage
gemeld; ledgerverwerking volgt pas na succesvolle rapportaanmaak.

**Kalenderimport is bewust menselijk.** ICS bevat uitsluitend uit de
niet-autorisende zSentinel-beslissing afgeleide P0/P1
`watch_alert_candidates` die aan menselijke incidenten zijn gebonden, met
originele beoordelingsdeadline (ook als die verstreken is). Het is **geen**
live agenda-integratie: het bestand wordt niet automatisch geïmporteerd of
verstuurd. De inhoud is vertrouwelijk; deel het alleen met de bevoegde eigenaar.
Een leeg ICS-bestand kan betekenen dat de begrensde steekproef geen menselijke
kandidaten bevat, nooit dat infrastructuur hersteld is.

**Releasegrens.** Deze opdracht schrijft geen SQLite/Notion-claim of
zCloud-workerqueue, herstart geen runner, retryt geen CI, autoriseert geen
merge/deploy en verleent geen nieuwe security- of systeemrechten. De
project-PR moet nog steeds door normale exact-HEAD verplichte CI, onafhankelijke
review, protected-main en runtime-release-eigenaarschap. Het bronresultaat op
een PR-branch bewijst geen live serverinstallatie.

## Eenzelfde bronmoment voor cockpit én agenda

Met `--agenda-output` kun je naast een gekozen normale operatorweergave in
één run een afzonderlijke privé-ICS voor de bevoegde incidenteneigenaar
aanmaken. Hierdoor hoeven GitHub- en SQLite-bronnen niet opnieuw gelezen te
worden voor alleen het kalenderbestand; beide resultaten zijn gebaseerd op
dezelfde routerbeslissing. Iedere bestandsnaam moet uniek en nieuw zijn.

```bash
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format html --output /private/zsentinel-operator.html \
  --agenda-output /private/zsentinel-owner.ics
```

Je kunt het HTML-bord lokaal openen en de ICS afzonderlijk in een
kalenderprogramma **handmatig** importeren. Het kalenderbestand bevat geen
bevestiging van een actieve eigenaar, verzonden notificatie of GitHub-merge.
`--agenda-output` werkt met de normale cockpitformaten, inclusief
`--format desk --desk-output ...`. Bij `--format ics` is die optie niet nodig:
gebruik `--output NEW.ics`. De uitvoerbestanden mogen geen bronbestanden,
elkaar, een queue-ledger of een andere privé-uitvoer overschrijven.

Als een van meerdere afzonderlijke bestandsaanmaken faalt, kan een eerder
aangemaakt privérapport behouden blijven. De CLI meldt de fout en verzint
nooit een complete transactie of geslaagde menselijke kalenderimport.

## Verifieerbaar historisch bronmoment in de agenda

Iedere ICS-export bewaart `X-ZSENTINEL-SOURCE-UTC` in de kalenderkop.
Elk menselijk actie-item toont daarnaast het **daadwerkelijke
observatietijdstip van de oorspronkelijke routerbeslissing**, niet de tijd
waarop de kalender opnieuw werd opgeslagen. Een bron uit de toekomst of met
`ci_retried=true` wordt geweigerd. De kalender bevat
`X-ZSENTINEL-NO-AUTHORITY:TRUE` om vast te leggen dat een ontvangen
kalenderitem geen runner-, release-, claim- of mergebevoegdheid is.
Dit bronmoment kan historisch zijn; controleer issue, eigenaar en gates
altijd opnieuw voordat een menselijke actie volgt.

## Later opnieuw openen zonder netwerk of queue-herlezing

Nieuw: `scripts/zsentinel_saved_operator_replay.py`. Een eerder met
`--format json --output /private/source.json` opgeslagen cockpitbron
kan maximaal 30 dagen later opnieuw worden weergegeven, **zonder GitHub,
Notion, VPS, SQLite, runner of andere externe provider te benaderen**.
Bron en bestemming worden expliciet gekozen. Alleen een regulier
enkelvoudig owner-owned 0600 JSON-bestand (max. 6 MiB) wordt geaccepteerd;
de bronreader gebruikt dezelfde no-follow/same-inode/duplicate-key-weigering
als de bestaande router. Autoriserende/muterende bronvlaggen en onbekende
broncontext worden geweigerd. De bestaande daadwerkelijke operatorrenderers
worden hergebruikt, inclusief de gebonden menselijke agenda.

```bash
python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format html \
  --output /private/historisch-bord.html

python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format ics \
  --output /private/historische-mensenagenda.ics

python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format md \
  --output /private/historische-handoff.md
```

Daarnaast zijn `--format text --output NIEUW.txt` en
`--format desk --output NIEUW.html` beschikbaar. De HTML- en
tekst-/Markdown-overdracht bevatten een zichtbare historische
bronwaarschuwing; ICS bewaart het daadwerkelijke originele bronmoment.
Ook wanneer het opgeslagen bestand gisteren is gemaakt, ontstaat
**geen nieuwe CI- of eigenaarswaarneming**. Alleen de operator kan de
eventuele oude herinnering bewust importeren. Source-only branchfunctie:
geen scheduler, claim, auto-deploy of live service-activering.

## Historische P0/P1-incidenttijdlijn (2–8 echte bronmomenten)

De zelfstandige productmodule
`scripts/zsentinel_decision_timeline.py` vergelijkt twee tot acht
eigenaar-private, chronologisch opeenvolgende **live-cockpit JSON-rapporten**.
Het hergebruikt dezelfde 0600/no-follow/duplicate-key-admissie als de
bestaande offline replay; originele bestanden worden niet gewijzigd. De
uitvoer is een nieuw bestand, nooit een overschreven bron.

```bash
# Maak bronbestanden op verschillende momenten met de bestaande cockpit:
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-t0.json
# (Apart later uitgevoerde bronobservatie; geen achtergrondtaak ingepland.)
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-t1.json

# Later op hetzelfde apparaat, ZONDER een provider te raadplegen:
python3 scripts/zsentinel_decision_timeline.py \
  --snapshot /private/zsentinel-t0.json \
  --snapshot /private/zsentinel-t1.json \
  --format html --output /private/historische-incidenten.html
```

Formaten: `html` (interactieve offline filters voor urgentie, aanwezigheid,
menselijke actie en zoeken), `json` (gebonden bron- en incidentmomenten),
`md` (privé overdracht) en `text`. De tool leest alleen eerder
aangemaakte privébestanden en exporteert exclusief nieuwe
`.html/.json/.md/.txt`-bestanden. Maximaal 8 bronnen, 30 dagen per
bron, 200 incidenten per moment, 1600 unieke incidenten intern en
400 zichtbare kaarten (eventuele afkapping expliciet). Bestaande
exact-incidentidentiteit is nodig voor herhaling; hetzelfde ID bij een
ander project/probleem wordt geweigerd.

**Bronwaarheid:** P0 blijft historisch urgent als een latere bron P1
ziet. Een tijdelijk niet-bemonsterd incident dat daarna terugkeert
krijgt de categorie *bronhiaat/heropgemerkt*, **geen** veronderstelde
tussentijdse oplossing. Ontbrekend in de laatste steekproef betekent
NOOIT opgelost; de CLI houdt zulke incidenten als menselijke
reconciliatiekandidaten. Het oorspronkelijke bronmoment, chronologische
vensters, genormaliseerde SHA-256-identiteit, waargenomen P0/P1-cijfers,
menselijke opvolgacties en onbekende eigenaarstoestand blijven zichtbaar.

Dit is een **operatorproduct**, niet een nieuwe Quality Validation- of
schedulerfunctie. Het genereert geen providerrequests, eigenaarsclaims,
GitHub merges, CI-retries, runneracties, betalingen of productie-deploys.
Een opgeslagen waarneming is geen actueel CI- of reviewbewijs.

### Privé menselijke actieoverdracht vanuit de tijdlijn

De offline HTML-tijdlijn heeft lokale filters en een aparte **Bekijk
privéoverdracht**-voorvertoning. Alleen geselecteerde, historisch als
menselijke P0/P1 geobserveerde incidentkaarten worden in de Markdown
opgenomen. Na expliciete controle kan de operator op **Download geselecteerd
Markdown** klikken. Een gewijzigd project-/zoek-/prioriteitsfilter maakt de
oude voorvertoning ongeldig; de download wordt pas na een nieuwe preview
beschikbaar. De overdracht bevat originele bronvensters en bron-SHA-256,
incidentidentiteit, oorspronkelijke opvolgdeadline, eigenaarlabel met
onbevestigde status, bronhiaten en de waarschuwing dat niet opnieuw gezien
**geen bewezen herstel** is. Er wordt niets verstuurd, geclaimd, gemerged,
als geslaagd afgetekend of automatisch in een agenda geïmporteerd.

### Veilig doelpad en bestandsaanmaak

De gedeelde operator-exportwriter is nu ook op de **bestaande doelmap**
vergrendeld. Maak die map vooraf onder het eigen operatoraccount en voorkom
groep-/wereldschrijfrechten (bijvoorbeeld een eigen privémap met `chmod 700`).
De writer weigert ontbrekende, vreemde of groep-/wereldschrijfbare mappen,
opent de map met `O_DIRECTORY|O_NOFOLLOW`, vergelijkt de werkelijke
geopende directory-inode met de vooraf gelezen eigenaargegevens, en maakt
het bestand uitsluitend via directory-geankerde `openat` met
`O_EXCL|O_NOFOLLOW`. Bestand blijft exact 0600. Ook bij een schijffout
wordt alleen de eigen nieuw gemaakte inode opgeruimd; een vervangen
bestandsnaam wordt nooit blind verwijderd. Dit is uitsluitend opslag voor
uitdrukkelijk gevraagde privébestanden en geeft geen rechten voor andere
VPS-paden, runner- of productieacties.

### SQLite-wachtrijdruk en claimchurn per bronmoment

De tijdlijn combineert voortaan ook de bestaande, al gevalideerde
`live_queue_pressure`-waarneming uit dezelfde privé-JSON-bronnen. Er wordt
geen nieuwe databaseverbinding geopend. Het nieuwe
`scripts/zsentinel_queue_pressure_timeline.py` vergelijkt uitsluitend
**aangrenzende bronvensters** waarvan beide wachtrijmetingen op hun
oorspronkelijke bronmoment volledig en actueel waren. Anders luidt de
uitkomst `UNKNOWN_source_gap`, nooit nul of opgelost.

Zichtbaar in HTML, JSON, Markdown en tekst: historisch totaal actieve en
wachtende taken, hoge (3+) en zeer hoge (10+) claimpogingen, verlopen of
niet-verifieerbare lease-rijen, plus verschillen per bronvenster. **Een
claimpoging is geen gefaalde uitvoering en geen toestemming voor een retry.**
Een daling is niet automatisch herstel: records kunnen door andere,
niet-waargenomen processen verdwijnen of van status veranderen.

Als de oorspronkelijke projectgroepinventaris volledig is, toont de offline
HTML ook een P0-eerst openklapbaar overzicht per project en prioriteit:
veranderingen in wachtenden, hoge claimpogingen en verlopen lease-rijen.
Bij ontbrekende of afgekapt gesamplede projectgroepen wordt niets als nul
gepresenteerd. Er is geen afleiding van een actuele eigenaarclaim,
vrijgegeven lease of correct uitgevoerde job.

De module weigert autoriserende bronflags, inconsistenties in totals
of per-projectgroepen en verwarring tussen originele observatietijd en het
moment van latere rapportexport. De oorspronkelijke bron-UTC en
niet-autorisatiestatus blijven bij ieder venster zichtbaar.

### 21 oktober · aanhoudende bronwaarnemingen in de queue-werkbak

Bij minimaal drie strikt opeenvolgende, volledige wachtrijmetingen
uit dezelfde bronsoort binnen 45 minuten toont de bestaande offline
tijdlijn nu terugkerende hoge claimpogingen of verlopen lease-rijen per
project en P0/P1/P2/P3. De reeks wordt alleen getoond wanneer dezelfde
project-/prioriteitsgroep in alle drie volledige projectinventarissen
met een positieve teller voorkomt. Onvolledige bron, oudere snapshots,
ontbrekende groepen of pauzes breken de reeks; geen fictieve nulwaarde.
Bij meerdere overlappende reeksen blijft de laatst waargenomen
reeks per project en signaal over. Het dashboard toont maximaal twaalf
bronsignalen en maakt bronafkapping expliciet.

De gegevens staan in bestaande JSON-, mobiele HTML-, Markdown- en
tekstoverdrachten, inclusief drie geobserveerde tellingen en bronvensters.
De nadrukkelijke eigenaaractie is: lees de oorspronkelijke actuele
queue-rij en actieve lease bij de bevoegde eigenaar voordat een mens
iets verandert. Historische group-totalen bewijzen geen identieke
taak, mislukte retry, vastgelopen worker of herstelde claim.
Er wordt geen queue-, GitHub-, Notion- of VPS-mutatie uitgevoerd.

### Private eigenaarsoverdracht voor wachtrij-/claimdruk

Naast de eerdere incidentenoverdracht bevat de offline HTML nu een tweede,
specifieke knop **Bekijk privé wachtrijoverdracht**. De voorvertoning toont
de volledig brongebonden P0-eerst gesorteerde projectverschillen, plus de
betreffende oude observatiemomenten en bron-SHA-256-identiteiten. De download
(**Download wachtrijoverdracht als Markdown**) kan pas ná die expliciete
voorvertoning en werkt zonder externe verbinding. Alle ontbrekende of
afgekapt gelezen projectgroepen staan als *ONBEKEND* genoteerd; er worden
geen herstelclaims of geautoriseerde retries van afgeleid.

De overdracht is bedoeld voor **handmatig onderzoek door de huidige
bevoegde eigenaar**: uitzoeken of herhaalde claimpogingen wijzen op
werkelijke stagnatie, verlopen lease-registraties of andere bronnen.
Niets wordt automatisch geclaimd, verwijderd, gerequeued, uitgevoerd,
gemerged, verstuurd of gedeployed. Ook eventuele lagere wachtrijaantallen
zijn zonder expliciet nieuw bewijs geen succesmelding.

## Browserworker-continuïteit tussen opgeslagen bronmomenten

De operatorwerkbak ondersteunt nu een **derde bron** naast incidenten
en SQLite-wachtrijdruk: reeds opgenomen, strikt read-only
`worker_progress_observation` uit de bestaande
`zsentinel_worker_progress_observer.py`. Er komt **geen tweede live
SQLite-query** bij het heropenen van het archief. Deze functionaliteit
helpt de eigenaar bij het onderzoeken van stagnatie en aanvulling van
de bestaande voortgangsgeschiedenis, onder andere rond issue #1342.

Maak nieuwe bronmomenten met het al bestaande opt-in-argument voor de
worker-eventbron (met echte, reeds aanwezige privé-DB-paden op dezelfde
VPS en in een eigenaar-private directory):

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/portfolio_queue.db \
  --worker-events-database /path/to/history.db \
  --project cloud --skip-github-issues --skip-ci-discovery \
  --format json --output /private/zsentinel-workers-moment-1.json

# Op een afzonderlijk moment, expliciet opnieuw door de operator:
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/portfolio_queue.db \
  --worker-events-database /path/to/history.db \
  --project cloud --skip-github-issues --skip-ci-discovery \
  --format json --output /private/zsentinel-workers-moment-2.json

# Alleen bestaande privéreports lezen; geen netwerk, worker of queue-writes:
python3 scripts/zsentinel_decision_timeline.py \
  --snapshot /private/zsentinel-workers-moment-1.json \
  --snapshot /private/zsentinel-workers-moment-2.json \
  --format html --output /private/zsentinel-workers-historisch.html
```

De nieuwe module `scripts/zsentinel_worker_continuity.py` aanvaardt
uitsluitend de expliciete bestaande read-only index/lease/runner-eventsbron
met ongewijzigde niet-autorisatievelden. Hij neemt maximaal 24 geobserveerde
workerslots per snapshot en vergelijkt maximaal 8 bronvensters, alleen als
oorspronkelijke bronidentiteit, projectselectie, UTC-tijdstippen en
bronvolledigheid overeenkomen. Bij een ontbrekende of vervangen SQLite-bron,
gewijzigde slots, ge-reset eventcursor, gewijzigde workerinstelling of
geen event-ID wordt **ONBEKEND** vastgelegd; er ontstaat nooit automatisch
een stalled-worker-label.

Nieuw geobserveerde `generation-progress` of `generation-finished`
indexcursors zijn uitsluitend **browsersignalen**, geen bewijs van
commit, afgeronde taak of hervatte eigenaarclaim. Herhaalde classificaties
die menselijke controle verdienen, staan bovenaan het historische
operatoroverzicht, expliciet als *herhaald gezien* en niet als meerdere
bewezen fouten. Privé **Bekijk worker-opvolging** geeft een voorvertoning
van de brongebonden, niet-autorisatieve Markdown-overdracht; de download
is afzonderlijk opt-in. Er worden geen browser-URL's, chatberichten,
prompts, foutpayloads, ruwe hostnamen of onbewerkte claimmetadata
overgedragen. Zonder echte huidige GitHub/Notion/queue- en workerbronnen
zijn lease, herstel, ownership, merge en deploy nog steeds NIET bewezen.

Dit is bronimplementatie op de bestaande PR; geen automatische
schedulerinstallatie, productie- of runnerrelease, SQLite-migratie,
claimovername, workflow-retry of QA-ronde.

### Geen workercontinuïteit over een nieuwe taak of gewijzigde lease

De historische bronvergelijking eist sinds de laatste integratie óók
overeenstemming van `allocated_global_slot`,
`queue_assignment_coverage`, `queue_claim_status_observed` en
`desired_state`. Bij een wisseling van toewijzing, queue-claimstatus
of lease-dekking wordt de vergelijking expliciet
`UNKNOWN_assignment_or_lease_context_changed` of
`UNKNOWN_worker_desired_state_changed`: dezelfde worker-slotindex
kan immers onder een **ander werkpakket** vallen. Een ongewijzigd
slot met nieuw indexed event is slechts een browsersignaal, nooit
een bewijs van dezelfde claim, productieverandering, commit of
eigenaarsovername. Herhaalde eigenaarcontrole wordt uitsluitend
geprioriteerd als beide gecontroleerde bronvensters dezelfde
toewijzingscontext tonen en geen broncursor geroteerd is.
