# zSentinel — live PR-bestandsscope bij projectherstel

## Wat dit voor de operator oplevert

Voor een voorgestelde codewijziging toont het programma uit welke **momenteel open GitHub-pullrequests** dezelfde bestandspaden worden aangepast. Het is een read-only broncontrole **vóór** de eigenlijke implementatie: je kunt de bestaande PR openen en een overlap gericht afstemmen. Dit is geen eigenaar-, claim-, release- of mergebesluit.

Deze functie is **apart** van de bredere zSentinel herstelcockpit in PR #1563, de offline menselijke workbench in PR #1560 en het bestaande zCloud-specifieke PR-loze-branchonderzoek. In alle gevallen blijven actieve Notion-claims en het werkelijke bestandsleasebeheer de autoritatieve bron voor eigenaarschap.

## Eén commando, zonder VPS-rechten of live writers

Vanaf de repository root:

```bash
python3 scripts/zsentinel_pr_scope_guard.py \
  --project supa --path src/ --path server/app.mjs \
  --format html > /secure/location/zsentinel-pr-scope.html
```

De uitvoer kan ook `--format text` (standaard) of `--format json` zijn. Een map wordt altijd aangeduid met afsluitende `/`, bijvoorbeeld `src/`. Zonder die slash is het een exacte bestandsnaam. Eén project per run, 1–12 expliciete doelpaden. `--max-prs 24` bekijkt standaard maximaal de 24 recentst bijgewerkte open PR's, maximaal 48. Privérepositories zijn alleen toegankelijk als de GitHub-identiteit daarvoor bevoegd is; `GITHUB_TOKEN` is optioneel en wordt niet naar output geschreven.

De code leest uitsluitend GitHub `GET /repos/{owner}/{repo}/pulls` en `GET /pulls/{number}/files`. De bron is beperkt tot de eerste maximaal 48 open PR's en maximaal 100 bestanden per PR; een bron die te groot is wordt als **onvolledig** gemarkeerd, niet als conflictvrij. De open-PR-heads worden vóór en na de bestandsscan opnieuw gelezen; elke verandering maakt de inventaris onvolledig. Ook hernoemde voormalige bestandspaden worden meegenomen.

**Statuscodes:** `0` betekent geen overlap gevonden in een volledige stabiele open-PR-inventaris (nog steeds géén claimtoestemming); `2` betekent minstens één conflict gezien; `3` betekent onvolledige of geweigerde bron. Het JSON-resultaat heeft altijd `file_edit_authorized=false`, `claim_authorized=false`, `merge_authorized=false` en `writes_performed=false`.

## Grenzen die niet mogen worden overschreden

- Geen GitHub/Notion/SQLite-writes, claims, dispatch, workflow-reruns, runner-restarts, servicebeheer, releases of betaald runnergebruik.
- Verifieert **niet**: actieve Notion-/zCloud-leases, PR-loze branches, lokale ongecommitte wijzigingen, tijdsgebonden eigenaarafspraken, de bevoegdheid voor een release of bestaande verplichte CI-/reviewgates.
- Een afwezige botsing in de steekproef is nooit bewijs dat een pad vrij is. De operator moet de huidige claim, branches en bestandscope afzonderlijk controleren alvorens een wijziging te maken.
- GitHub-prioriteiten en oude open PR's autoriseren geen overname van andermans taak. Bij onvoldoende netwerkrechten, bronwijziging tijdens de scan of teveel geopende PR's eindigt het programma conservatief.
- Gebruik dit alleen als **standalone pre-edit observatie**; het is niet gekoppeld aan een automatisch werkdistributieproces en bevat geen planner- of scheduler-hook.

Relateert aan het zSentinel-herstelwerk en GitHub collision/ownership-scope, maar **lost runner-P0 #1326, hosted billing #1523 of writer-conflict #677 niet op**. Deze P0's blijven uitsluitend bij de aangewezen bevoegde eigenaren.


## Open PR als bron: automatisch alle gewijzigde bestanden vergelijken

Een bestaand feature-PR kan ook rechtstreeks als bron dienen, zonder zelf 12 bestandspaden te kopiëren:

```bash
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --source-pr 1567 \
  --format html > /secure/location/vergelijk-pr-1567.html
```

De GitHub-bron-PR moet exact bestaan, open zijn en bij het gekozen canonieke project horen. De tool leest maximaal 80 gewijzigde bestandsnamen (inclusief hernoemde oude paden), sluit de bron-PR uit van de conflictregels, scant de andere open PR's en herbevestigt de bron-HEAD vóór de uitslag. Bij een veranderde bron-HEAD is de gegevensdekking **onvolledig**. De JSON bevat de exacte bron-PR URL en SHA. Het overzicht is nog steeds slechts een GitHub PR-steekproef, geen Notion-claim- of mergevrijgave. Met extra `--path` kunnen naast de PR ook geplande nieuwe bestanden worden vergeleken.

De POSIX-padparser accepteert Unicode en routebestanden zoals `src/app/(dashboard)/page.tsx` en `src/routes/[slug].ts`. Absolute paden, `..`, control-tekens en backslashes worden geweigerd; HTML-links en bestandsnamen worden veilig ge-escaped. Een bestaand bestand met ongebruikelijke naam leidt dus niet automatisch tot een valse GitHub-bronstoring.

GitHub's PR-bestandsscope kan óók veranderen wanneer alleen de doelbranch opschuift. Daarom pint de bron zowel `base_sha` als `head_sha` van alle bekeken PR's. Een afwijkende basis of head bij de tweede inventarislezing (of bij de expliciet geselecteerde PR) wordt als gewijzigde/onvolledige bron behandeld. Het resultaat blijft altijd niet-autoriserend.


## Projectbrede open-PR-conflictkaart

Het vorige werk vereiste een geselecteerde PR of bestandspad. Nu is er ook één commando om zonder handmatig invoerwerk de *huidige open PR's binnen één project onderling* te vergelijken:

```bash
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs --max-prs 48 --format html \
  > /secure/location/zsentinel-pr-conflictkaart.html
```

De output toont paren met **dezelfde bestandsnamen**, de links naar beide bestaande PR's, de waargenomen aantallen en maximaal 24 gedeelde bestandspaden per paar. De resultaten worden op het aantal gedeelde bestanden gesorteerd, zodat de operator eerst potentieel grote overlap inspecteert. Dit is **geen Git-mergeconflict-analyse**, geen eigendomsbeslissing en geen automatisch instructie tot sluiting of merge.

De bron is een begrensde GitHub-GET-steekproef: maximaal 48 open PR's worden geïnspecteerd, op elk maximaal 100 bestandspaden, en maximaal 200 botsende PR-paren weergegeven. De bron-HEAD en base-SHA van alle PR's worden in de vóór-/nalezing gepind; padhernoemingen tellen voor zowel oude als nieuwe naam. Een eerste pagina met precies 48 PR's, de ingestelde limiet, een bestandslijst met precies 100 records, veranderde PR-identiteit of afgekapte paarlijst zijn expliciet onvoldoende voor een volledige open-PR-inventaris. GitHub/Notion/workerclaims worden nooit gewijzigd.

*Exitcode*: `2` betekent zichtbare bestandsoverlap, `3` onvolledige bron zonder overlap, `0` complete stabiele open-PR-steekproef zonder overlap. Ook code `0` geeft geen claim-, edit-, merge- of deployrechten.

Deze conflictkaart maakt de grote open-PR backlog bespreekbaar voordat een worker bestaand werk dupliceert. Eventuele bronbotsingen moeten eerst langs de echte eigenaar/lease- en reviewprocedure. Een oude open PR is nooit op zichzelf bewijs van actuele eigenaarschap.


## Portfoliooverzicht voor de vliegende keep: 1–3 projecten

Dezelfde bestaande read-only GitHub-bron kan nu meerdere **verschillende canoniek geregistreerde** projecten in één run bekijken. Dit is vooral nuttig als zSentinel een geblokkeerde release moet parkeren en een andere onafhankelijke backlog wil inspecteren.

```bash
python3 scripts/zsentinel_pr_scope_portfolio.py \
  --project cloud --project supa --project haxlab \
  --max-prs 10 --format html \
  > /secure/location/zsentinel-portfolio-pr-conflicten.html
```

Ook ondersteund: `--format markdown` (een direct leesbare, handmatig te delen PR-handoff), `--format text` en `--format json`. De Markdown-uitvoer is een privé-operatorresultaat op stdout: er wordt **niets** automatisch geüpload naar GitHub, Notion of het zCloud-portfoliosysteem.

De maximale bronbelasting per run is **3 projecten × (16 open PR-file GET + 2 open PR-list GET) = 54 publieke GitHub GET-aanvragen**. Standaard bekijkt het programma 10 open PR's per project (maximaal 36 GET over drie projecten), zodat het onder de typische anonieme primaire GitHub API-uurgrens blijft. Rechten, huidige quota en latency kunnen alsnog een bron weigeren; een geweigerd project krijgt `source_status=unavailable` en blokkeert **niet** het onafhankelijke inzicht van een ander project. Dat geeft nooit een kunstmatige `0 conflicten` voor een geweigerde bron.

Dit is **broninspectie, geen autonome werktoewijzing**: een botsing kan de operator helpen een bestaande PR te hergebruiken, maar deze tool mag geen claim-lease overnemen, merge doen of runner herstellen. Het portfoliorapport gebruikt uitsluitend `observed_*`-velden en zet alle autorisatieflags expliciet op `false`.

De drie functies kunnen apart worden gebruikt:
1. `zsentinel_pr_scope_guard.py --project PROJECT --path FILE`: gericht geplande bestanden vergelijken.
2. `zsentinel_pr_scope_guard.py --project PROJECT --source-pr NUMMER`: één bestaande PR vergelijken met de rest.
3. `zsentinel_pr_scope_portfolio.py --project PROJECT [--project ...]`: een read-only conflictkaart over meerdere projecten.


## Onafhankelijke kandidaat-PR's: uitsluitend afwezigheid van bestandsbotsing

De single-project-conflictkaart en de nieuwe portfolio-overzichten tonen óók een aparte lijst **"Geen gedeelde paden in de steekproef"**, met directe PR-links. Dit zijn bestaande open PR's die in de geraadpleegde PR-window geen exacte bestandsnaam delen met een andere bekeken open PR. Daardoor kan een operator gericht naar onafhankelijk *potentieel uitvoerbaar* productwerk kijken in plaats van alleen botsingen.

Deze vermelding is **niet** gelijk aan "vrij", "claimbaar", "goed voor merge" of "geen git-mergeconflict". De volgende bewijzen ontbreken nog altijd: Notion en SQLite claim/lease-eigenaar, ongeregistreerde PR-loze branches, uncommitted work, upstream veranderingen, verplichte CI, onafhankelijke review en release-autorisatie. Zelfs bij `source_coverage_complete=true` blijven autorisatievelden false.

Als een portfolio-uitvoer meer botsingskaarten oplevert dan de begrensde zichtbare selectie, toont de UI nu expliciet **"bronoverlap > uitvoerlimiet"** in plaats van een volledig verslag te suggereren. Andere projecten blijven leesbaar wanneer één GitHub-repository een fout retourneert.


## Directe, brongebonden Markdown-overdracht

Alle drie de ingangen ondersteunen nu `--format markdown`. Deze schrijft een operatorleesbare handoff op stdout met echte GitHub-PR-links, bronmoment, geobserveerde bestandsbotsingen, brononzekerheid en expliciete geen-autorisatie-waarschuwingen. Bijvoorbeeld:

```bash
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --source-pr 1567 --format markdown
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs --format markdown
```

De handoff hoeft niet als JSON aan een eigenaar te worden uitgelegd. De tekst veroorzaakt geen issue-comment, Notion-update, claim, GitHub-merge, betaalde job of VPS-mutatie. Alle bestandsvergelijkingen blijven read-only; bij onvolledige GitHub-overzichten worden suggesties van ogenschijnlijk disjuncte PR's **onderdrukt**.


## Werkend geïntegreerd productscherm: issues + bestaande PR's + bestandsscope

Met het nieuwe `scripts/zsentinel_issue_scope_cockpit.py` combineert zSentinel **de reeds op main beschikbare Product Delivery Workbench-bron** met de verse read-only open-PR-bestandsscopecontrole. Geen aparte duplicerende GitHub-issuecrawler en geen aanpassing aan de nog niet gemergede herstelrouter (#1563).

**Stap 1 —** maak een eigen `zsentinel-product-delivery-triage-v1` JSON-rapport via de bestaande, op main aanwezige `scripts/zsentinel_product_delivery_board.py`; gebruik de gedocumenteerde optionele lokale issue/retry/claims/PR-bronparameters van die module. Bewaar het als privébestand buiten de repository.

**Stap 2 —** maak uit die bron **één actueel cockpitoverzicht** zonder opnieuw GitHub-issues te lezen:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --issue-board /secure/location/product-delivery-board.json \
  --project cloud --project supa --max-prs 10 \
  --format html > /secure/location/issues-en-bestandscope.html
```

Zonder `--project` kiest het programma automatisch maximaal drie projecten met P0 vóór P1 vóór P2 vóór ongespecificeerde issues (en vervolgens meer productissues). Dit is **weergaveprioriteit** en neemt nooit een Notion- of zCloud-claim over. Beschikbaar: `--format html`, `json`, `text` of `markdown`.

Het programma hergebruikt `zsentinel_operator_workbench.read_source_and_sha` en `validated_view` uit het reeds gemergede hoofdproduct: dit is de bronvalidator, met gebonden SHA-256, veilige bounded/no-follow bestandstoegang, issueidentiteiten en niet-autoriserende schema-eisen. Vervolgens koppelt het de bestaande issue→PR-verwijzingen aan per-project beperkte live GitHub-bestandsscopewaarnemingen. Het product toont:

- Prioriteit en issue-link; expliciet mensbevoegde P0 #1326 en #1523 **alleen** voor infra/billing-eigenaar.
- Bestaande PR-kandidaten **eerst**, met eventuele geobserveerde bestandsbotsing met een tweede open PR; dat is niet gelijk aan een bewezen Git-mergeconflict.
- Een *geen gedeelde paden*-indicatie uitsluitend wanneer de volledige open-PR-steekproef compleet en stabiel is, de PR daar echt in voorkomt en de twee bronmomenten hoogstens tien minuten uiteenliggen.
- `source_unknown_or_uninspected` bij ontbrekende PR, brondegradatie, onvolledige file-inventaris of oud bronmoment. Afwezigheid van een issue in deze begrensde bron betekent nooit dat het opgelost is.
- Alle outputs laten `claims_verified=false`, `may_take_ownership=false`, `merge_authorized=false`, `deploy_authorized=false` en `writes_performed=false`.

Dit scherm is een **operatorintegratie**, geen automatische productiequeue-adapter. Het raakt geen `server.py`, worker/scheduler, GitHub Actions, tests, Notion of live SQLite, doet geen deployments en kan geen beschermde gates omzeilen. Het kan in bron worden geïntegreerd onafhankelijk van de wachtende PR #1563, waarna die herstelrouter op zijn eigen autorisatiepad blijft wachten.


### Bestaande exacte PR-checks en onafhankelijke review als observatie

Als de canonical `zsentinel_product_delivery_board.py` bij het vormen van de lokale triage-bron een optionele `--prs` exacte-head PR-receipt heeft gekregen, hergebruikt de nieuwe geïntegreerde cockpit deze **al aanwezige** historische status. Het nieuwe scherm toont bij de passende bestaande PR: geobserveerde bron-HEAD, of de toen geobserveerde native checks geslaagd waren en hoeveel approvals de opgeslagen bron meldde.

Er worden hiervoor **geen nieuwe GitHub Actions gestart en geen extra API-calls gedaan**. Een opgeslagen checkstatus wordt expliciet als historisch getoond; niet-beschreven verplichte branchregels, actuele SHA, nieuwe pushes, review-freshness en productieautorisatie blijven **niet geverifieerd**. De PR blijft dus op HOLD voor merge totdat de normale actuele gates apart bevestigd zijn.


## Snelle productstand zonder handmatig JSON-bundelwerk

Dezelfde geïntegreerde Product Delivery Cockpit kan nu in **één opdracht** de actuele GitHub P0/P1/P2-productissues én open-PR-bestandsscope ophalen. Daarmee is er een directe bruikbare operatorflow wanneer geen eerder opgeslagen privéproductboard beschikbaar is:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --project supa \
  --max-prs 10 --format html > /secure/location/zsentinel-live-product.html
```

De eerder beschreven `--issue-board`-modus blijft voor reproduceerbare, privé brongebonden handoffs; beide routes zijn exclusief, zodat één run niet heimelijk oud en nieuw issuebewijs verwart. Live gebruikt **uitsluitend** de al gemergede, canonieke `zsentinel_product_issue_inbox.collect` en `zsentinel_product_delivery_board.prepare` voor productissues en QA-uitsluiting; het maakt geen tweede issuecrawler, geen queue-mutatie en geen Notion-API-sleutel nodig. Het controleert ook dat de twee bestaande projectregisterlezers dezelfde canonieke repositories opleveren.

Bronbelasting: maximaal 3×(2 issue-lijsten + 1 issue-gerelateerde open PR-lijst + 12 PR-bestandslijsten + 2 PR-lijstbevestigingen) = **51 GitHub GET-aanvragen** bij maximaal drie projecten en `--max-prs 12`. Standaard 10 PR's per project = maximaal 45 GET. Op 403/429 of incomplete hoofdbron worden onzekerheid en bronbeperkingen behouden; een ontbrekend issue is geen opgelost issue en een afwezige PR is geen eigenaarsvrijgave. Er zijn geen writes of CI-triggers.

De live-variant toont een SHA-256 van het door de reeds bestaande triageproducer in het geheugen samengestelde JSON-resultaat als *rapportidentiteit*, **niet** als bewijs dat externe GitHub/Notion-werkelijkheid cryptografisch gepind of volledig was. Het controleert geen actuele officiële claims, bevoegdheden, releasegates of Watch/device-eisen.


### Reuse van meer bestaande PR's uit hetzelfde project

De canonieke GitHub productissue-inbox inspecteert uit kostenbeperking slechts een korte reeks actuele PR-beschrijvingen voor issueverwijzingen. De PR-bestandsscopecollector leest echter al een groter, begrensd venster van open PR's. Diezelfde **reeds opgehaalde** PR-beschrijvingen worden nu binnen maximaal 32 verwijzingen per PR uitgelegd als *onbevestigde tekstverwijzingen* naar hetzelfde repository-issue (`#123`).

De geïntegreerde cockpit hergebruikt dergelijke bestaande PR-links alléén bij een compleet/stabiel PR-venster, dekkende bronbody en twee bronmomenten binnen 10 minuten. Per issue worden maximaal vijf PR-kandidaten getoond, met zichtbaar onderscheid tussen `existing_product_issue_board_reference` en `current_open_pr_text_mention_unverified`. Een vermelding is **nooit** bewijs van gefixte code, actieve eigenaar, sluiting of merge-authorisatie.

De bestaande canonieke QA-only titelclassificatie blijft leidend: `QA`/`test`/`regression`/quality-only PR's worden niet als nieuwe productkandidaten voorgesteld (maar zijn nog wél zichtbaar in bestandsbotsingen wanneer zij dezelfde paden raken). Hiermee kan zSentinel meer bestaande echte product-PR's hergebruiken zonder extra GitHub-aanvragen, zonder claim-overname en zonder nieuwe tests.


## 20 oktober: productbeslissingen bij overvolle PR-repositories en incidentcontext

**Groot zichtbaar productprobleem opgelost:** bij een groot project als zCloud staan méér open PR's dan één veilige inspectiebatch bevat. Een gedeeltelijke maar **stabiele** open-PR-inventaris mag niet worden voorgesteld als volledig, maar de reeds positief gevonden bestaande PR→issue-koppelingen waren voorheen geheel verborgen. De integratie toont nu zulke **positieve, SHA-gebonden PR-vermeldingen uit de geïnspecteerde steekproef**, met het label `observed_current_open_pr_text_mention_partial_source`. Ongeziene PR's/afwezige vermeldingen worden nooit als onbestaand geïnterpreteerd. Aanbevelingen zonder bestandsoverlap blijven exclusief voor een **volledige** stabiele open-PR-inventaris.

De Product Delivery Cockpit bewaart nu ook de **echte issuetitels, het oorspronkelijke productadvies en de concrete bronhiaten** van de al gemergede Workbench; het vervangt deze niet door korte mechanische routecodes. De operator krijgt praktische Nederlandstalige vervolgstappen, directe link(s) naar bestaande PR's en hun gevonden bestandsbotsingen. HTML is responsief en heeft een **lokale zoekbalk plus filters voor project en prioriteit**. Het filterscript werkt enkel op de reeds in het HTML-document weergegeven kaarten; CSP blokkeert netwerk/fetch, form posts en externe scripts. Er worden geen persoonsgegevens of notities opgeslagen of geüpload.

**Echte bestaande incidentgegevens geïntegreerd zonder eigenaarschap over te nemen:** als een privé opgeslagen canonieke Product Delivery Board een eerder toegelaten read-only queue/retry-, claim- of PR-loze-branchwaarneming bevat, verschijnt een apart operationeel contextblok met geobserveerde hoge claimcycli, verlopen leases, bronadvies en recente branch-overlapkandidaten. De bronmomenten en opgeslagen versheidsvlaggen staan er expliciet bij. Zonder zulke gegevens is de uitkomst **ONBEKEND**, nooit `0` of `hersteld`. De live GitHub-issuenbron levert geen live SQLite/Notion-inventaris en claimt die ook niet.

Voer de cockpit uit met `--live-issues` of exclusief met `--issue-board`. De JSON maakt dit verschil expliciet als `issue_source_mode=live_readonly_github_issues` versus `saved_local_snapshot` en bevat de werkelijke issue-/PR-waarnemingstijd. De brongeheugenhash is een rapportidentificatie, geen vervanging van externe SHA-/autorisatiecontrole.

**Geen QA-workflow gestart of suites aangepast.** Exacte-HEAD verplichte bestaande CI, onafhankelijke review, Notion/zCloud eigenaar, branch protection, zSSH-rechten en production release gates blijven onaangetast; de HTML geeft geen rechten.


### Bounded bronverkeer voor grote PR-beschrijvingen

De open-PR-metadata gebruikt nu maximaal **48 PR's op de eerste pagina** in plaats van 100, omdat volledige PR-beschrijvingen bij een groot project de begrensde GitHub-response kunnen overschrijden. De bestandslijsten blijven begrensd tot 100 bestanden per PR. Bij precies 48 ontvangen PR's is de open-PR-inventaris **ONVOLLEDIG/UNKNOWN**, ook als 48 toevallig het werkelijke totaal is. Dit versnelt een bruikbare *positieve* gedeeltelijke PR→issue- en gedeelde-bestandsscopelezing in drukke projecten zonder valse 'geen overlap'-conclusies. Er is geen automatische paginering of tweede claimbron toegevoegd.


## Eén bestaande blokkade gericht uitwerken

De live-/offline Product Delivery Cockpit kan nu in dezelfde codebasis een **één-issue-herstelkaart** maken met `--focus-issue`. Hiermee hoeft de operator niet door een volledig portfolio-overzicht te zoeken wanneer al duidelijk is welk P0/P1-issue aangepakt moet worden:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud \
  --focus-issue 'Zennay/zCloud#1326' \
  --format markdown
```

De gefocuste kaart combineert issuetitel, prioriteit, originele Workbench-adviezen/bronhiaten, gevonden bestaande PR's (inclusief positieve vermeldingen uit een beperkte stabiele lijst), geobserveerde bestandsbotsingen en eventuele historische CI-/reviewbewijsverwijzingen. Voor eigenaar-only issues zoals zCloud #1326 of #1523 blijft de tekst expliciet **bevoegde menselijke eigenaar vereist**; er wordt nooit een restart/claim/merge/retry uitgevoerd.

**Nu rechtstreeks opgehaalde live issuebron:** bij `--live-issues --focus-issue` en exact **één geregistreerd** `--project` wordt niet meer gezocht in de beperkte oudste/nieuwste issuewindow. Het programma leest het exacte open issue via de reeds bestaande canonical GitHub GET-helper en gebruikt daarna de ongewijzigde Product Delivery Board-classificatie voor prioriteit, QA-only uitsluiting, claims-unknown en PR-verwijzingen. Dit is zinvol bij oude urgente issues die anders niet in de steekproef voorkomen. De uitvoer krijgt `issue_source_mode=live_exact_selected_github_issue`, **1 directe issue-GET** en vervolgens het bestaande begrensde open-PR-overlaponderzoek. De uitkomst kan geen eigenaar/CI/review/mergegoedkeuring verlenen.

Bij `--issue-board --focus-issue` geldt nog steeds het eerdere, strikte gedrag: het exacte issue moet in de **opgeslagen** board aanwezig zijn. Bij een 404, gesloten status, verwijderde source, of verkeerde repository in directe live-modus is de uitkomst *niet beschikbaar voor deze live open-issueinspectie*, **niet** automatisch "opgelost" of "vrij". QA-only geselecteerde issues worden overeenkomstig het gebruikersbeleid geweigerd; er worden geen tests gestart. Het selecteren verandert de originele private bron-SHA of observatietijd nooit.


### Exacte P0-recovery zonder volle GitHub-backlogscan

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud \
  --focus-issue 'Zennay/zCloud#1326' --max-prs 10 \
  --format html > /secure/location/p0-runner-eigenaaroverdracht.html
```

Zonder `--focus-issue` blijft de reguliere productissue-inbox P0/P1/P2-prioriteit en onafhankelijke projecten ondersteunen. Met `--focus-issue` is het exact ene nog open GitHub-issue de bron en kan het veel oudere P0-probleem niet meer door paginering verdwijnen. Voor #1326 blijft de herstelactie bij de geautoriseerde VPS/runner-eigenaar; de browserkaart is bewijs-/overdrachtsinformatie, geen toestemming om de listener te herstarten.


## 20 oktober · Bestaande integratiefouten opgelost

De oorspronkelijk geïntegreerde Product Delivery Workbench gebruikt `validated_view()` om een veilige, **bewust verkleinde** lijst kaarten terug te geven. Dit object bevat géén ruwe `title`, `project_operations`, `source_times_utc` of `coverage.branches`. Daardoor verscheen het eerder beschreven productdeel met echte issuetitels en opgeslagen claim-/retry-/branchobservaties in werkelijkheid niet volledig. De cockpit koppelt nu de **volledige, al via de canonical validator gecontroleerde** bronkaart via exacte issue-identiteit, project, prioriteit, PR-verwijzingen, advies en bestaande no-authority-flags aan de verkleinde kaart. Ruwe externe brongegevens kunnen zich daardoor niet als een ander gevalideerd issue voordoen. `--focus-issue` beperkt alleen de weergegeven kaart en behoudt de oorspronkelijke volledige bron-SHA.

**API-venster zonder schijnzekerheid:** de PR-bron haalt maximaal `min(48, --max-prs+1)` PR-metadata op, en leest die exacte beperkte selectie vóór en na de bestandsobservatie. Een volle eerste pagina is altijd **onvolledig**, ook wanneer die mogelijk echt de laatste is. Dit maakt een operatoropdracht over 10 PR's goedkoper/kleiner dan onnodig 48 volledige PR-beschrijvingen ophalen. De direct geselecteerde bron-PR blijft daarnaast head én base opnieuw pinnen.

**Broncapaciteit intern consistent:** per PR kunnen maximaal 32 issueverwijzingen worden geobserveerd, met maximaal 48 PRs in de absolute broncap. De samengestelde incidentkaart accepteert nu veilig maximaal **48 × 32** expliciete verwijzingen; een volle drukke GitHub-repository veroorzaakt dus niet meer een onterecht `unbounded`-falen door de oude veel kleinere interne limiet. Dit staat los van de zichtbare cap van vijf kandidaat-PR's per issue.

**Private handoffs:** openbare issuetitels en opgeslagen mensnotities worden in tekst-/Markdown-overdracht tot één inerte regel genormaliseerd en Markdown-metatekens ontsnapt. Ruwe GitHub-issueinhoud of eerdere operatornotities komen daardoor niet als uitvoerbare instructie, HTML of verborgen meerdere regels over.

De directe `--live-issues --focus-issue`-flow blijft strikt read-only: één exact geregistreerd repository/issue, canonical priority/QA-only screening en dezelfde bestaande triagekaart. Onbereikbare, gesloten of QA-only issues worden niet stilzwijgend als opgelost verklaard.

## Meerdere open PR's per issue · eerst bestaand werk benutten (20 oktober)

De bestaande live PR-bestandsscope ontdekt nu ook een TWEEDE type
productoverlap: twee of meer actuele open PR-bodyteksten in dezelfde
GitHub-repository verwijzen naar hetzelfde issue-nummer. Twee PR's
kunnen immers dezelfde bug proberen op te lossen terwijl ze verschillende
bestanden raken. Dit komt niet naar voren uit bestandsbotsingen alleen.

De uitbreiding werkt in de bestaande productflows:

- scripts/zsentinel_pr_scope_guard.py --audit-open-prs: open-PR
  bestandsoverlap EN klikbare issue-PR-groepen met exacte head-SHA's.
- scripts/zsentinel_pr_scope_portfolio.py: beide signalen in maximaal
  drie onafhankelijk gelezen projecten, met onbekende bron als
  fail-closed onbeschikbaar en expliciete afkapgrenzen.
- scripts/zsentinel_issue_scope_cockpit.py --live-issues: bestaande
  P0/P1 issuekaarten geven bij meerdere open PR-verwijzingen de route
  'eerst bestaande implementaties bekijken en eigenaar afstemmen',
  niet automatisch nieuw duplicerend werk maken. Echte bestandscope-
  overlap blijft eerst; owner-only incidenten houden hun eigenaarroute.

Alle gevonden koppelingen zijn uitsluitend TEXTERVAREN verwijzingen
in begrensde open PR's. Een PR met dezelfde issue-verwijzing bewijst
niet dat de PR het issue oplost, geen huidige claim bezit, review
heeft of mergeklaar is. De bron classificeert
same_issue_number_unverified_open_pr_body_text_only.
Bij volle GitHub-pagina, veranderde heads, onleesbare body,
API-weigering of afgekapt PR-inventarisvenster blijft afwezigheid
van overlap UNKNOWN in plaats van claimvrij of mergevrij.
Eigenaarschap, wijzigingen, merges, runneracties en deploys blijven
ongeautoriseerd. Geen bestaande QA- of regressiesuite veranderd
of gestart; de oorspronkelijke CI/review/branch-protection en
bevoegde release-eigenaar blijven vereist.

Broncode op bestaande PR #1567; nog niet gemerged op main of
live uitgerold.

## Huidige exact-head PR-reviews en checks · merge-eerst-operator (20 oktober)

Naast bestaande bestandscope- en dubbele issueverwijzingen kent de gecombineerde
live productcockpit nu optioneel een brongebonden current-head PR-checkreview:

    python3 scripts/zsentinel_issue_scope_cockpit.py \
      --live-issues --project cloud \
      --max-prs 10 --inspect-pr-gates --format html

Nieuwe scripts/zsentinel_pr_gate_now.py leest voor maximaal VIER
canoniek gekoppelde open PR's in huidige P0/P1-prioriteitsvolgorde
de actuele PR base/head identiteit, check-runs (maximaal 100)
en ingediende reviews (maximaal 100), daarna de actuele PR base/head
opnieuw. Bij bronverandering of ontbrekende permissie is de ontvangst
UNKNOWN, geen positief oordeel. Elke bron vereist maximaal vier
GitHub GETs en verandert niets aan GitHub/Notion/worker/queue/VPS.

De operatorcockpit toont per gevonden bestaande PR exact HEAD
(commitprefix), lopende checks, niet-geslaagde checks en getelde
waargenomen onafhankelijke APPROVED-reviews op diezelfde commit.
Reviews van de PR-auteur tellen NIET als onafhankelijk; een latere
CHANGES_REQUESTED/DISMISSED-waarneming op dezelfde exacte HEAD
wordt niet als goedkeuring weergegeven.

**DIT IS GEEN VOLLEDIGE MERGE-ADMISSIE**: niet alle check-runs hoeven
verplichte branch protection checks te zijn, de complete actuele
reviewpolicy en werkelijke claim-/releasebevoegdheid blijven onbekend.
Een 'mergeable' GitHub bronboolean of aantal groene checks/approvals
mag nooit automatisch een merge, force push of deploy autoriseren.
Alle permissionflags in source blijven false; een bevoegde
GitHub-reviewer/CI-protectie bepaalt de feitelijke toelaatbaarheid.
De optionele optie is uitsluitend beschikbaar bij --live-issues;
de eerdere saved-board variant blijft bewust historisch.

De operator ziet een eerlijk gedeeltelijk snapshot; ontbrekende
reviews/checks bij niet-toegankelijke GitHub zijn UNKNOWN, geen
vrijgave of bewezen productherstel. Geen QA-/regressiesuites geschreven
of gestart. Source slechts op open product-PR #1567, niet live gedeployed.

## Current-head gate triage: Check Runs + legacy statuses (20 October)

The optional `--inspect-pr-gates` feature now reads **both** GitHub Check Runs
(`/commits/{sha}/check-runs?per_page=100`) and independent **legacy commit
statuses** (`/commits/{sha}/status?per_page=100`) for the exact observed
PR HEAD. Some mandatory status contexts are reported only through the older
commit-status API; previously the current-head cockpit could misleadingly
show zero failing checks when a legacy context was failing.

For each of at most four already referenced PRs, the Dutch HTML, text,
Markdown and JSON issue views now distinguish legacy-context success,
pending and failure/error from Check Run results. Earlier status entries
on the *same context* are deduplicated by highest observed GitHub status ID,
so a superseded error is not automatically treated as a current failure.
A full 100-status page is explicitly **potentially truncated**. Missing,
unavailable, changing-head, malformed or truncated sources never turn into a
positive merge conclusion. The request bound increased from **4 to 5 GETs
per PR** (two PR snapshots, Check Runs, commit statuses and reviews): at most
20 requests for the four-PR desk, plus the existing separately bounded issue
and scope collection.

The cockpit also gives an **observation-based, non-authorizing human next
step**: inspect incomplete sources, fix a current failure with the PR owner,
wait for existing pending CI, handle changes requested, verify absent check
evidence, obtain independent exact-head review, or independently verify
required branch/release rules. No branch-protection API rule inventory or
independent owner/Notion lease approval is inferred; even all observed green
checks and one approval do **not** authorize merge, dispatch or deployment.

Example, still strictly read-only:

```sh
python3 scripts/zsentinel_issue_scope_cockpit.py --live-issues \
  --project cloud --inspect-pr-gates --max-prs 10 --format html \
  > zsentinel-live-issue-gates.html
```

Use normal protected GitHub merge/review gates and owner authorization
separately. No QA/regression suite, runner workflow dispatch or production
write is part of this change.


## 20 October · Direct current-HEAD JSON source for the offline Merge-first Desk

The existing `scripts/zsentinel_pr_gate_now.py` now has a direct
**read-only operator CLI**, so the offline zSentinel workbench does not
need a separately assembled mock report or a full issue crawl. Give it
between one and four canonical existing `Zennay/repo#number` references:

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --pr 'Zennay/zCloud#1560' --pr 'Zennay/zCloud#1567' \
  --output ./private-pr-gate-observation.json
```

The output file is **create-only** (exclusive, no symlink final component,
mode 0600), never overwrites an existing receipt, contains at most 4×5
bounded GitHub GET observations, and carries the existing
`zsentinel-current-pr-gate-observation-v1` schema. GitHub access comes
from an already configured `GITHUB_TOKEN` environment variable; no
token or raw response body is written into the output. If a PR source is
unavailable, the result is an explicit unknown record rather than a
success/merge claim. Without `--output` the tool prints JSON to stdout
for deliberate human redirection, not an automatic upload or service.

Load the saved JSON into the offline Product Delivery Workbench in existing
[PR #1560](https://github.com/Zennay/zCloud/pull/1560) via
**Import GitHub PR-gate snapshot**. The workbench matches only canonical
already-captured PR links, marks old source observations stale, groups
failed/pending/review-blocked PRs and enables human-reviewed Markdown/JSON
handoffs. **Neither open PR has yet been merged into main merely because
this integration code exists.** The operator must still check the real
current head, mandatory exact-head CI, independent review, permissions and
protected-main rules before a normal authorized merge.

No GitHub issue, pull request, workflow, Notion task, queue, lease,
runner, SSH target or live production application is modified by this
one-shot source generator. No QA suite, regression or CI rerun is created
or invoked.


### Direct Dutch triage without exporting JSON

The same bounded CLI supports `--format text` for an immediately readable
Dutch summary of each explicitly chosen PR:

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --pr 'Zennay/zCloud#1560' --pr 'Zennay/zCloud#1567' \
  --format text
```

Each record shows the observed exact commit, modern and legacy pending/failed
contexts, independent current-head approval count, whether any page was
truncated, and the next **human** gate action. It states explicitly that
protected-main/owner/release permission has not been established. `--format
json` remains the default for source-bound import into the workbench; the
optional `--output` still uses exclusive private 0600 creation in either
format. The command never merges, retries or deploys and is not a
substitute for GitHub's live mandatory rules.


### Source-bound, merge-first manifest handoff (no manual PR retyping)

The read-only CLI can also consume the **review-only JSON manifest** already
exported by the offline Merge-first Workbench in
[existing PR #1560](https://github.com/Zennay/zCloud/pull/1560):

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./zsentinel-offline-merge-first-review.json \
  --output ./private-current-head-gates.json
```

This explicitly requested one-shot operation accepts only a bounded,
strict-UTF-8, regular, non-symlink (when supported by the OS) JSON file under
1 MiB with the expected `zsentinel-offline-merge-first-review-v1` schema,
SHA-256 source board identifier, **all four authorization flags false**,
original canonical Zennay GitHub PR URLs and unique saved PR identities.
It never trusts saved ownership, prior mergeability or cached CI as live
authorization. Malformed/unbounded/missing-source inputs stop before any
GitHub query. It sorts saved PR references P0, P1, P2, Unspecified and
observes **at most four** using the same existing max-20-read bounded current
PR gate collector. Any fifth or later PR is explicitly **NOT OBSERVED**:
the output contains `source_review_pr_count` and
`review_only_top_four_observed=true` when truncated.

The GitHub gate receipt embeds the exact imported manifest SHA256 plus the
original board source SHA256, so the operator can reconcile two saved
snapshots without claiming they were fetched in one live transaction.
`--format text` includes this source provenance and warns about any
omitted fifth-or-later candidates. `--format json` remains the default
for locally importing the fresh observation back into #1560; importing it
never grants merge, deploy or claim authority. Source manifests do not
authorize work, and neither separate PR is considered integrated until
its own required CI, protected branch and independent reviews complete.

The file reader makes **no edits** to its input. The optional output is
exclusive-create mode 0600 with no overwrite, and this operation performs
no QA execution, worker scheduling, Notion writes, PR merge, service change
or deployment.
