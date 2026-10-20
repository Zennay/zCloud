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
observes **at most four per batch** using the same existing max-20-read
bounded current PR gate collector. For a larger source, supply `--batch 2`,
`--batch 3`, etc. as separate explicit operations with distinct new
output filenames. Only the chosen batch is observed in each run; the
output contains `source_review_pr_count`, `source_review_batch_index`,
`source_review_batches_total`, `source_review_batch_first_pr`, and
`source_review_batch_count` to make omitted batches explicit.

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


### Follow-on PR gate batches: never leave the rest invisible

If the manifest lists more than four existing PRs, first collect batch 1 as
shown above, then choose a **new** output filename for each later batch:

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./zsentinel-offline-merge-first-review.json \
  --batch 2 --output ./private-current-head-gates-batch2.json
```

For direct `--pr` choices, `--batch` is not allowed. For manifest selection,
batches are one-based and bounded by the saved candidate count;
out-of-range requests stop before a GitHub API request. Each output
remains a separate private 0600 source observation, not a cross-batch
atomic view of GitHub.

The offline workbench in [#1560](https://github.com/Zennay/zCloud/pull/1560)
now rejects a supposed manifest-derived receipt if its original
`source_review_board_sha256` does not exactly equal the currently loaded
board, or if its batch index/start/count/total do not match the saved
candidate count. It describes the imported batch number and explicitly
marks all **other** batches not inspected in that file. These local
evidence rules never authorize a merge, claim, workflow retry or deployment.


## Nieuwe merge-first totaalweergave: meerdere opgeslagen PR-gate-batches

Het bestaande gereviewde manifest uit [PR #1560](https://github.com/Zennay/zCloud/pull/1560) kan meer dan vier PR's bevatten. Het reeds bestaande `zsentinel_pr_gate_now.py` inspecteert daarom maximaal vier huidige PR-heads per GitHub-GET-batch. De nieuwe **offline batchverzamelaar** toont alle afzonderlijk verzamelde bonnen in één overzicht, zonder nieuwe GitHub API-verzoeken of claims te doen:

```bash
# Verzamel handmatig per batch precies één brongebonden, private gate-bon:
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./merge-first-review.json --batch 1 \
  --output ./private-gates-1.json
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./merge-first-review.json --batch 2 \
  --output ./private-gates-2.json

# Groepeer de bewaarde waarnemingen veilig in een nieuw offline HTML-rapport:
python3 scripts/zsentinel_pr_gate_batches.py \
  --from-review ./merge-first-review.json \
  --receipt ./private-gates-1.json \
  --receipt ./private-gates-2.json \
  --format html --output ./private-gate-overview.html
```

Dit operatorresultaat bevat alle ingelezen PR-URL's en waargenomen HEAD-SHA's,
de voornaamste menselijke blokkade per PR, aantallen ontbrekende batches,
ontbrekend/veranderd GitHub-bewijs en bronbonnen ouder dan vijftien minuten.
Daarnaast zijn `--format json`, `--format markdown` en `--format text`
beschikbaar. Het HTML-resultaat is offline en heeft geen JavaScript.

De tool leest maximaal 32 bestaande bonnen van elk maximaal 1 MiB, met een
beperkte reguliere bestandstoegang zonder symlinks waar het platform dat
ondersteunt. Hij hergebruikt de bestaande strikte manifestparser van
`zsentinel_pr_gate_now.py` en accepteert uitsluitend precies dezelfde
manifest-SHA, board-SHA, PR-volgorde en batchgrootte als de originele
prioriteitsgesorteerde bron. De originele reviewbron mag tussentijds niet
veranderen. Dubbele batch, onverwachte PR/HEAD/autoriteitsvelden of vervalst
`merge_authorized=true` worden afgewezen.

**Statuscode 0** betekent uitsluitend dat de aangeleverde *opgeslagen*
gatebonnen compleet, leesbaar en maximaal 15 minuten oud zijn; het betekent
**nooit** dat een PR klaar is voor merge. **Statuscode 3** betekent
onvolledige, verouderde of niet-vertrouwde bron. Indien een opgeslagen
gatebon ontbreekt, wordt uitsluitend dat ontbrekende segment weergegeven;
andere bonnen blijven bruikbaar als historische operatorinformatie.

Geen van beide statussen controleert de live vereiste branch protection,
bevoegde onafhankelijke reviewers, actuele PR-heads, Notion-claims,
productie-release of runner-resourcing. Zelfs alle groen ogende bronbonnen
kunnen intussen achterhaald zijn. Alle uitvoer bevat expliciet
`claim_authorized=false`, `merge_authorized=false`,
`deploy_authorized=false` en `writes_performed=false`.

Standaard wordt op stdout geschreven; gebruik `--output` voor exclusief
nieuw aangemaakt privaat bestand met mode 0600. Niets wordt automatisch
naar GitHub, Notion, VPS, workers, queues of een deployment geschreven.
De beperking van maximaal 32 bonnen betekent dat zeer grote manifests
expliciet gedeeltelijk blijven, niet stilzwijgend als afgevinkt verschijnen.


### Gatebatchresultaat terugzien in de bestaande interactieve issuecockpit

De nieuwe brongebonden, opgeslagen PR-gategegevens verschijnen ook rechtstreeks
bij de betreffende **bestaande PR-link per P0/P1-issue** in de
`zsentinel_issue_scope_cockpit.py`-werkbank. Naast het reeds beschikbare
live-issue- en bestandscope-overzicht ziet de operator de historische
gatecategorie, laatst opgeslagen menselijke vervolgactie en expliciete
bronouderdom. Hierdoor blijft de merge-first-afweging zichtbaar op dezelfde
plek waar de productissues staan, in plaats van twee losse rapporten naast
elkaar te moeten lezen.

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --issue-board ./private-product-delivery-board.json \
  --project cloud --from-review ./merge-first-review.json \
  --saved-gate-receipt ./private-gates-1.json \
  --saved-gate-receipt ./private-gates-2.json \
  --format html > ./private-issue-and-gates.html
```

`--live-issues --project cloud` mag eveneens als issuebron, maar is dan een
**andere bronwaarneming**, geen atomische GitHub snapshot. De commandovlag
`--inspect-pr-gates` (echt live, hooguit vier PR's) is bewust exclusief met
`--saved-gate-receipt` (opgeslagen, historisch). Het JSON-resultaat toont
`saved_gate_batch_reconciliation` en per gekoppelde PR optioneel
`saved_reconciled_gate_observation` zonder de bestaande gescheiden
`live_exact_head_review_checks_observation` te vervalsen.

Ook bij groene opgeslagen signalen blijft `merge_authorized=false`.
Onvolledige batches, ontbrekende actuele Notion-claims, niet geverifieerde
branch protection en release-eigenaarschap kunnen nooit verborgen
mergetoestemming opleveren. Er wordt geen bestaande review, branch,
testuitvoering, queue of productiedienst gemuteerd.


### Doorbouw 20 oktober · volledige manifestcapaciteit en geprioriteerde werkbak

De gecombineerde `zsentinel_pr_gate_batches.py` ondersteunt nu **het gehele
bestaande manifestmaximum van 300 PR's**, met 75 afzonderlijke bronbatches
van telkens maximaal vier PR's. Er blijft **maximaal één batch per expliciete
`zsentinel_pr_gate_now.py`-opvraag**: geen verborgen massale GitHub-poll,
geen betaalde of background worker en geen automatische CI-reruns.

De HTML-, tekst- en Markdown-overzichten tonen bovenaan **Eerst bekijken**:
een compacte handmatige merge-first-werkbak met maximaal 24 bestaande
PR-links, huidige *historisch waargenomen* CI/reviewcategorie en de
eerstvolgende menselijke actie. De volgorde volgt exact de originele
P0/P1-eerst gesorteerde reviewmanifestvolgorde. Als een eerdere batch ontbreekt,
blijven de oorspronkelijke manifestposities behouden; niet de nummering van
het beperkte ontvangstbestand. Overige PR's blijven in de volledige
batchsecties zichtbaar.

Batches zijn nooit één gelijktijdige GitHub-snapshot. De uitvoer toont nu
het **tijdsverschil tussen de oudste en nieuwste bewaarde bronbon**:
meer dan 600 seconden maakt de bron expliciet incoherent. Ook ontbrekende,
onbereikbare en ouder dan vijftien minuten opgeslagen bonnen leveren
statuscode `3` op. In de JSON staan deze niet-autoriserende bronvelden:

- `cross_batch_observation_spread_seconds`
- `cross_batch_observation_within_10_minutes`
- `manual_review_queue_preview`
- `manual_review_queue_hidden_after_24`
- `all_observations_present_and_recent`

Ook de bestaande interactieve `zsentinel_issue_scope_cockpit.py` toont deze
bronwaarschuwing en de compacte menselijke werklijst in een uitklapbare
sectie bij de brongebonden issues. Een ontbrekende of incoherente
opgeslagen PR-batch laat de overige issuekaarten staan, maar de tool
geeft statuscode `3` in plaats van een ogenschijnlijk volledige
merge-first-beoordeling. Dit is geen nieuwe release-, review- of
toestemmingsgate: het is alleen een correcte aanduiding van incomplete
**historische waarnemingen**.

`read-only`, `writes_performed=false`, `claim_authorized=false`,
`merge_authorized=false` en `deploy_authorized=false` blijven bindend.
Exact-head CI, GitHub-branch protection, onafhankelijke review,
huidige claims en bevoegd releasebeheer moeten afzonderlijk live worden
gecontroleerd vóór uitvoering van enige merge of productiehandeling.


### Compacte bediening bij grote PR-backlogs

Bij meer dan een handvol afzonderlijk opgeslagen batches toont de offline
HTML eerst de **brongebonden menselijke werkbak**. Elke volledige batch staat
daaronder in een toegankelijk uitklapbaar `details`-onderdeel met
keyboard-focus, het originele batchnummer, waargenomen aantal PR's en
expliciete historische bronouderdom. Hiermee hoeft een operator niet door
75 lange geopende bronsecties te scrollen om de P0/P1-opvolging te vinden.
Dezelfde broninhoud blijft beschikbaar in JSON, Markdown en tekst; alleen
de visuele weergave is compacter.

Dit blijft een volledig offline document met gewone links, geen scripts,
geen live GitHub-status en geen automatische workflow- of mergeactie.


## Eén commando: actuele PR-botsingen én exacte-head CI/review (optioneel)

De bestaande portfolio-conflictkaart en de bestaande huidige-head PR-gatecollector
zijn nu geïntegreerd in **dezelfde operatorflow**. Zonder extra optie verandert
niets en blijft het programma uitsluitend bij PR-bestandscope. Met
`--inspect-current-pr-gates` selecteert de tool uit de reeds gelezen stabiele
PR-steekproef maximaal vier bestaande open PRs en vraagt hun actuele
GitHub-checks, oudere statuscontexten en ingediende reviews op:

```sh
python3 scripts/zsentinel_pr_scope_portfolio.py \
  --project cloud --project supa --project haxlab \
  --max-prs 10 --inspect-current-pr-gates \
  --format html --output ./private-zsentinel-current-merge-desk.html
```

Hiermee ontstaat één offline operatorpagina met naast elkaar:
bestandsbotsingen tussen bestaande PR's, dubbele verwijzingen naar open
productissues, en **voor maximaal vier PR's** een apart exact-head
CI/reviewoverzicht met bron-HEAD, lopende/mislukte checks, waargenomen
onafhankelijke approvals en relevante menselijke vervolgactie.
Voor menselijke bediening zijn ook `--format markdown`, `text` en
`json` beschikbaar. Het bronveld
`current_live_pr_gate_observation` in JSON is altijd niet-autoriserend.

De selectie gebeurt **zonder extra open-PR-zoekronde**:
- Alleen eerder dubbel gelezen, stabiele PR-identiteiten komen in aanmerking.
- Bij gedeelde bestandspaden worden die PR's eerst ter menselijke inspectie
  aangeboden, daarna PR's die in hun body hetzelfde issue noemen, vervolgens
  de rest van de bestaande waargenomen PR-volgorde.
- Maximaal vier PR-gatebronopvragen per run, eerlijk verdeeld over maximaal
  drie gekozen projecten. De huidige exacte PR-HEAD en base-SHA worden
  vergeleken met de opgeslagen file-scope-bron. Bronverandering = UNKNOWN,
  nooit een groen of mergeklaar oordeel.
- Elke PR gebruikt de **bestaande** `zsentinel_pr_gate_now.py`-broncollector
  (maximaal vijf GitHub GETs per PR). De extra limiet is **20 GETs**;
  volledige standaardvraag maximaal drie projecten × twaalf GETs plus
  twintig = 56 GETs. Gebruik een geautoriseerde `GITHUB_TOKEN` wanneer
  rate limits dit vereisen. Er wordt niets opnieuw getriggerd.
- Projecten met onbereikbare of tijdens het lezen veranderende scopebron
  krijgen géén verzonnen PR-gate-uitslagen; andere onafhankelijke projecten
  kunnen wel worden weergegeven. Ongecontroleerde PR's en brononvolledigheid
  worden expliciet genoemd.

De bestandsinventaris en de gate-inventaris zijn **twee opeenvolgende**
waarnemingen, geen atomisch GitHub-snapshot. Zelfs de vier gevonden
exact-head CI-uitslagen bewijzen nog niet dat **alle verplichte branchregels,
Notion/workerclaims, onafhankelijke reviewpolicies of releasebevoegdheid**
zijn vervuld. `merge_authorized=false`, `claim_authorized=false`,
`deploy_authorized=false`, `writes_performed=false` blijven expliciet
van kracht.

`--output` is optioneel en maakt een **nieuw**, niet te overschrijven
privébestand met mode 0600 en fsync. Het programma print dan geen
privébroninhoud of lokaal bestandspad in workerlogs. Zonder `--output`
blijft de bestaande stdout-interface bruikbaar. Het resultaat wordt niet
automatisch in zCloud geïmporteerd en veroorzaakt geen queue-, claim-,
GitHub-mutatie, workflowdispatch, CI-rerun of production deploy.


### Productselectie bij optionele actuele gatecontrole

De geïntegreerde `--inspect-current-pr-gates`-optie gebruikt nu ook de
reeds in de huidige PR-inventaris vastgestelde titelclassificatie uit
`zsentinel_product_issue_inbox.py`. Een **QA-only/test-only PR-titel**
wordt niet als zelfstandig product-merge-werk naar voren geschoven;
dat voorkomt dat oudere quality-validation-taken opnieuw capaciteit
vragen onder het product-delivery-beleid van 17 oktober.

Deze classificatie is beperkt tot de *waarneembare PR-titel*: een
feature-PR is daardoor niet bewezen veilig, vrij of zelfstandig eigendom,
en een overgeslagen QA-PR wordt **niet automatisch gesloten**. De
bestandsbotsingskaart blijft alle open PR's inspecteren, zodat gedeelde
bronpaden en actieve mogelijke collisions zichtbaar blijven. Alleen de
extra maximaal vier GitHub-check/review-GET-kandidaten zijn
productgericht geselecteerd. De JSON/tekst/HTML toont het aantal
overgeslagen QA-only titels; elke bron blijft strikt read-only.


### Richt de gecombineerde live cockpit op specifieke bestaande PR's

Normaal kiest de operatorfunctie automatisch maximaal vier eerder waargenomen
product-PR's in een eerlijke portfolioselectie. Voor daadwerkelijk
**merge-first** opruimen van een bestaande P0/P1-PR kan de gebruiker nu ook
zelf 1–4 bestaande PR-referenties aanwijzen, zonder extra open-PR-query of
QA-only-werk te starten:

```bash
python3 scripts/zsentinel_pr_scope_portfolio.py \
  --project cloud --max-prs 16 \
  --inspect-current-pr-gates \
  --gate-pr cloud#1567 --gate-pr cloud#1563 \
  --format html --output ./private-zsentinel-focused-prs.html
```

`--gate-pr` accepteert uitsluitend een bestaande canonieke
`project#PRnummer`-referentie bij een van de geselecteerde projecten.
Als de PR in de huidige **dubbel gelezen stabiele** recente product-PR-lijst
voorkomt, wordt de daar waargenomen HEAD/base gebruikt. Zo niet, voert
zSentinel een gerichte aparte bronlezing van **exact die expliciet genoemde
bestaande PR** uit: `selected_source_pr` leest canonieke open-PR metadata en
de pagineerbare veranderde-bestandenlijst (maximaal vier extra GitHub GETs:
PR-metadata plus maximaal drie bestandspagina's).
Alleen een geldige open PR van het gekozen geregistreerde project met
product-PR-titel en complete bestandslijst (maximaal 240 unieke paden,
waarbij hernoemde oude paden meetellen) kan door.
Bij afwezig, QA-only, ongeldige/truncated bron of netwerkfout wordt **alleen
die gekozen PR** als `direct_selected_pr_unavailable_or_not_product` met
onbekende HEAD en **geen mergevrijgave** opgenomen. De andere onafhankelijk
gekozen en beschikbare PR's worden nog wél gelezen; het overzicht valt
dus niet volledig om door één externe GitHub-bronfout. De HTML/tekst/JSON
toont het aantal onbereikbare of geweigerde gerichte PR's en exitcode 3
bij incomplete dekking. Ook buiten het recente PR-venster kan de operator
daardoor een oudere P0/P1-product-PR gericht inspecteren zonder de hele
backlog te downloaden.

Voor oudere gericht gelezen PRs toont de cockpit daarnaast uitsluitend
**positief waargenomen bestandsbotsingen** tegen de al eerder gelezen recente
PR-steekproef: directe PR-link, huidige-HEAD-op-het-bronmoment,
aantal overlappende paden en een beperkte lijst van maximaal twaalf namen.
Deze overlappende bestandspaden veroorzaken geen nieuwe GitHub-GETs.
Alle bronoverlap is tijdelijk, gedeeltelijk en geen bewijs van een
echte Git-mergeconflict of actieve eigenaar. Geen overlap in de beperkte
steekproef betekent nooit dat de oudere PR geheel conflictvrij is.

De extra gate-API-grens blijft **maximaal vier PR's × vijf GETs** plus
**maximaal vier afzonderlijke scoped GETs per expliciet gericht opgevraagde
oudere PR (één metadata plus maximaal drie filepagina's; ook bij onbereikbare
bron voor de reservering)**, dus maximaal **36 extra GETs**. De enige automatische bijwerking bij een mislukte
gerichte bron is een expliciet onbekende operatorregel, niet een
claim-/merge-/deployactie.
`current_live_pr_gate_observation.mode` onderscheidt expliciet
gekozen PR's van de automatische beperkte shortlist;
`explicit_selected_prs` bevat uitsluitend die brongebonden
project-PR-referenties. Het huidige exacte-head CI/reviewbewijs is
niet hetzelfde als de vereiste branch protection, onafhankelijke review
voor merge, een geldige actuele eigenaarclaim of releasebevoegdheid.
Alle autorisatievelden blijven `false`; er worden géén merges uitgevoerd.


### Live gate-cockpit: volledige blokkade-informatie en bronmoment

De samengevoegde optionele live-workbench geeft naast de oorspronkelijke
`human_next_gate_step` ook een **Nederlandstalige actuele blokkadecategorie**
`human_gate_blocker` per PR terug. De operator ziet nu afzonderlijk:

- Onbereikbare of tijdens twee waarnemingen gewijzigde HEAD/base;
- Ontbrekende, niet volledige of afgekorte Check Runs, legacy commit-statussen
  en reviews;
- Expliciet mislukte Check Runs **of legacy statuscontexten** (die anders
  onzichtbaar bleven wanneer alleen Check Runs werden getoond);
- `github_mergeable_boolean_observed=false` als waargenomen
  GitHub-mergeconflict, `null` als nog niet berekende mergeability;
- Draft-PR's, op de exacte HEAD gevraagde wijzigingen en lopende
  checks/legacy-statussen;
- **Geen enkel waargenomen Check Run of legacy statuscontext** als
  onbekende verplichte CI, niet als groene CI;
- Ontbrekende onafhankelijke actuele-HEAD-approval;
- Als laatste de **altijd afzonderlijk te verifiëren** branchregels,
  huidige eigenaar/claim en releasebevoegdheid.

De bron levert `observed_gate_blocker_counts`, expliciete
`check_runs_total_observed`, `observed_legacy_failure_or_error`,
`observed_exact_head_changes_requested` en `draft_at_read`.
De HTML/text/Markdown-uitvoer toont deze indicatoren daadwerkelijk per PR
en onderstreept dat het géén automatische mergebeslissing is.

Oudere rechtstreeks opgehaalde `--gate-pr` PR's krijgen bovendien
`observed_in_recent_pr_collision_sample=false`, een afzonderlijke
lijst van **positief waargenomen** `sampled_file_overlaps_observed` en
`complete_file_collision_coverage_verified=false`. Het overkoepelende
`source_coverage_complete` wordt bij iedere uit-het-venster afkomstige
PR uitdrukkelijk `false`; de CLI geeft dan statuscode 3. Zo kan nooit
onbedoeld een oude volledig-dekkende PR-lijst worden gecombineerd met
een nieuw rechtstreeks gevonden PR en vervolgens als compleet verschijnen.
De werkbank toont de UTC-tijd van de gatebron en blijft bij maximaal
vier gate-PR's, maximaal 28 aanvullende GETs in directe-PR-modus
(maximaal 20 in de automatische modus; direct/gericht maximaal 36). De file-overlapstatus is
gedeeltelijk, zelfs als nul paden overlappen.

Er worden geen PR's gemerged, geen branchregels of CI overgeslagen,
geen Notion-/workerclaims genomen en geen workflows of VPS-diensten
opnieuw gestart.


### Uitbreiding: grote gerichte product-PR's zonder onbeperkt GitHub-verkeer

De optionele `--gate-pr`-route heeft een afzonderlijke begrensde bestandslezer
`changed_paths_focused` gekregen. De gewone single-project- en portfolio-
bestandsinventarissen houden hun bestaande goedkope limiet: **maximaal
één bestandspagina per bekeken PR**. Alleen een door een operator expliciet
gekozen oudere PR kan maximaal **drie filepagina's van elk honderd regels**
ophalen. De bron wordt afgewezen bij dubbele file-identiteiten, ongeldig
formaat, meer dan **240 unieke paden**, of een derde pagina met precies
100 entries (mogelijk onvolledige verdere pagina's). Voor de laatste
gedeeltelijke pagina is de bron wel volledig; oude en nieuwe namen bij
hernoemen worden beide als paden geteld.

De operator ziet per direct gelezen PR zowel
`focused_pr_file_pages_observed` als `focused_pr_paths_observed` in
de JSON en, wanneer beschikbaar, in HTML en tekst/Markdown. Ook de
reeds waargenomen **gedeelde paden** met recentere PR's blijven zichtbaar.
Een git repository buiten de eerder gekozen beperkte open-PR-steekproef
wordt niet stilzwijgend als geverifieerd beschouwd. Een bron die over het
vaste pad- of paginabudget gaat blijft **UNKNOWN** en blokkeert alleen
het betreffende gerichte PR-deel. Tot vier gerichte PR's levert
maximaal **36 extra GitHub GETs** inclusief de bestaande vijf-GET huidige
CI-/reviewcollectie per succesvol geselecteerd PR. Er vindt geen
GitHub-write, claim, merge, workflowrerun of production-deploy plaats.


### Rechtstreeks een grote bestaande PR vergelijken met andere open PR's

Naast de portfolio-route werkt de uitgebreide bestandsopname nu ook
**direct** in de bestaande single-project-bestandsscopecockpit,
uitsluitend wanneer de gebruiker daarvoor één concrete bron-PR kiest:

```bash
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --source-pr 1567 --expanded-source-files \
  --max-prs 24 --format html > ./source-pr-overlap.html
```

Dit verzamelt maximaal **drie begrensde GitHub-filepagina's** voor de gekozen
canonieke open product-PR (maximaal 240 unieke huidige én bij rename oude
paden). Daarna worden die paden vergeleken met de reeds bestaande
begrensde lijst van maximaal 48 andere recente open PR's, zonder de bron-PR
zelf als conflict te tonen. Een tweede bron-PR metadata-GET controleert dat
dezelfde HEAD/base nog open staat aan het eind van de waarneming.
Dit is een expliciete read-only vorm van PR-integratievoorbereiding,
**geen** Git-mergeconflict- of eigenaarsverificatie.

Zonder `--expanded-source-files` blijft de bestaande snelle route met
één bronbestandspagina en hoogstens tachtig paden intact. De extra optie
mag niet zonder `--source-pr` en niet samen met
`--audit-open-prs`; QA-only-titels zijn voor uitgebreide bronlezing
niet toegestaan. Bij ontbrekende/gewijzigde bron, dubbele paginaregels,
meer dan 240 paden of een volledig gevulde derde pagina wordt de
bron geweigerd. Hierdoor zijn **maximaal vijf gerichte bron-GETs**
nodig (metadata + maximaal drie filepagina's + één metadata-freshness-read),
boven op de bestaande begrensde open-PR-steekproef en file-GETs.

De JSON bevat `source_file_pages_observed`,
`source_file_count` en `expanded_source_file_pages_requested`
onder `selected_source_pr`. Het aantal gelezen bronpagina's/paden
wordt ook in de Nederlandse HTML-, tekst- en Markdown-uitvoer getoond.
De CLI drukt bij bronfouten geen ruwe exception met eventuele lokale
privébestandspaden meer af. Elke bevinding blijft zonder autorisatie:
verplichte exacte-head CI, onafhankelijke review, actuele Notion-claims,
branchregels, eigenaar en releasevenster moeten separaat worden gecontroleerd.


## 20 oktober · Veranderingen tussen twee private PR-conflictopnamen

De portfolio-PR-conflictenbalie vergelijkt nu twee begrensde, read-only bronopnamen.
Daarmee worden HEAD/base-wijzigingen van dezelfde bestaande PR zichtbaar,
zonder eerdere incomplete inventarissen als bewezen opgeloste blokkades te melden.

Schrijf de eerste momentopname als NIEUW lokaal privé JSON-bestand (0600):

```bash
python3 scripts/zsentinel_pr_scope_portfolio.py \
  --project cloud --project supa --max-prs 10 \
  --format json --output /private/zsentinel-pr-earlier.json
```

Vergelijk op een volgend, afzonderlijk gekozen moment met dezelfde projectvolgorde:

```bash
python3 scripts/zsentinel_pr_scope_portfolio.py \
  --project cloud --project supa --max-prs 10 \
  --compare-previous /private/zsentinel-pr-earlier.json \
  --format html --output /private/zsentinel-pr-delta.html
```

Ook JSON, Markdown en tekst bevatten nu `historical_pr_delta`.
De resultaten onderscheiden aantoonbare HEAD/basis-wijzigingen bij stabiele
open-PR snapshots van PRs die alleen in een steekproef voorkwamen.
PRs alleen in de vorige steekproef zijn **niet automatisch gesloten**.
Nieuwe of verdwenen botsende PR-paren worden uitsluitend gemeld als beide
complete, stabiele GitHub open-PR snapshots bevatten. Als een bron deels
onbeschikbaar is, toont de output ONVOLLEDIG en geen vals herstelbewijs.

Beide opnamen moeten dezelfde geordende 1–3 geregistreerde projecten bevatten,
een UTC-tijd hebben, binnen zeven dagen liggen en niet uit de toekomst komen.
Het historische JSON moet een reguliere 0600-file zijn van dezelfde gebruiker,
niet een symlink, maximaal 2 MiB. `--output` creëert exclusief een nieuw
privé bestand en overschrijft geen eerdere observaties.

**Geen autorisatie:** vergelijking is geen claim-, PR-review-, merge-,
runner- of deploybesluit. Exact-HEAD CI, onafhankelijke review, actuele
eigenaren en releasegates blijven verplicht. Geen GitHub/Notion/queue-writes,
workflow dispatch, of actieve herstelacties worden uitgevoerd.

### Hetzelfde bronverschil in de issue- en PR-cockpit

De gecombineerde issue/PR-cockpit hergebruikt exact dezelfde lokale
vergelijker. Daarmee kan een P0-issuekaart worden bekeken samen met
recent geobserveerde PR-HEAD-wijzigingen, zonder extra GitHub-requests
voor historische vergelijking of een tweede eigen herstelcontroller:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --max-prs 10 \
  --compare-previous /private/zsentinel-pr-earlier.json \
  --format html > /private/zsentinel-issues-en-pr-delta.html
```

Voor het aanmaken van een **nieuw 0600-bestand** blijft de portfolio-CLI
met `--output` de aanbevolen route; voor een redirect naar een privélocatie
moet de operator de lokale bestandsrechten zelf beschermen.
De issue-cockpit toont historische wijzigingen apart van actuele P0/P1
productissues en van afzonderlijk opgevraagde exacte PR-checks.
De vergelijking verifieert geen huidige Notion-claims, runner-herstel,
verplichte branchregels of bevoegdheid om te mergen.


### 20 oktober · Historie direct bij P0/P1-issuekaarten en veilige privé-output

De gecombineerde issue-/PR-cockpit gebruikt nu dezelfde bronvergelijker
om **concrete, reeds gekoppelde open PR's** in P0/P1-issuekaarten aan te
merken wanneer HEAD/basis, hun beperkte bronvermelding of een gedeeld
bestandspad tussen twee snapshots veranderde. De offline HTML heeft een
filter **Met waargenomen PR-wijziging**; Markdown, tekst en JSON geven
dezelfde per-issue en per-PR observaties met expliciet volledige of
ONVOLLEDIGE bron aan. Afwezige activiteit betekent niet dat een PR
niet is bijgewerkt of een issue is opgelost.

De issuecockpit kan zelf een privérapport aanmaken in plaats van
een onveilig geërfde shell-redirection te vereisen:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --max-prs 10 \
  --compare-previous /private/zsentinel-pr-earlier.json \
  --format html --output /private/zsentinel-issues-met-historie.html
```

`--output` maakt **uitsluitend een NIEUW regulier bestand** met mode 0600
aan en weigert overschrijven, symlinks en bestaande workerbestanden.
Zonder `--output` blijft uitvoer op stdout ondersteund. Bij één geweigerde
historische bron wordt geen rapport als compleet gepresenteerd.

Deze informatie is een brongebonden mensenwerkbak, geen automatische
workerclaim, PR-merge, CI-rerun, release of runneractie. Bestaande
eigenaren, exact-head verplichte checks, reviews, branchregels en release
autoriteit blijven bepalend.


### Grote repositories: veranderingen in onvolledige bestandssteekproeven

Bij drukke repositories zoals zCloud is de begrensde recente PR-inventaris
vaak **onvolledig**. Een bestaand overlap-paar dat in de nieuwste beperkte
opname voorkomt maar niet in de vorige kan dan niet als *nieuw* worden
beschouwd. De historie-vergelijker toont nu afzonderlijk:

- Overlapparen alleen in de huidige steekproef (**niet bewezen nieuw**).
- Overlapparen alleen in de vorige steekproef (**niet bewezen opgelost**).
- PR-HEAD/basis-wijzigingen die wel in beide stabiele snapshots stonden.

Dit gebruikt uitsluitend al aanwezige gesamplede, stabiele GitHub-bronparen,
geen extra API-calls. De bronlimiet (maximaal 40 getoonde paren per richting)
en eventuele afgekapte bron blijven expliciet. De gerelateerde open PR's
verschijnen ook binnen P0/P1-issuekaarten via de bestaande PR-link; zonder
historische bron of bij niet-stabiele opname blijft de relatie onbekend.
Een positief steekproefverschil is nooit een geverifieerde bugfix,
succesvolle merge, runner-herstel of toestemming om andermans claim te nemen.


## 20 oktober · Merge-eerst P0/P1 operatorwerkbak met één bronopvraag

De bestaande gecombineerde issue/PR-cockpit bevat nu een **menselijke
merge-eerst werklijst** boven de issuekaarten. De werklijst verwerkt
de al opgehaalde issue- en PR-bestandsinformatie, optioneel actuele
exact-HEAD check/reviewwaarnemingen en optioneel historische PR-snapshots.
Er worden **geen aanvullende GitHub-GETs** uitgevoerd om deze
actiecategorieën te berekenen; de oorspronkelijke bronbudgetten blijven gelden.

Per issue staat een bestaande gevonden PR vóór nieuwe implementatie.
Een zichtbaar P0-infrastructuurissue zoals #1326 is expliciet
**human-only**; een PR met falende gates wordt naar de actuele PR-eigenaar
verwezen; lopende/missende checkgates krijgen een HOLD die alleen die PR
raakt; bestandsoverlap wijst naar ownerafstemming. Geen enkele categorie
mag een claim, release, merge of dispatch autoriseren.

Maak een compacte overdracht voor een eigenaar:

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --max-prs 10 \
  --inspect-pr-gates --format worklist-markdown \
  --worklist-limit 20 --output /private/zsentinel-p0-werkbrief.md
```

De volledige offline HTML, JSON, tekst en Markdown bevatten dezelfde
geprioriteerde werkbak. Gebruik `--format worklist-json` of
`--format worklist-text` voor een kort handoff-artifact. De rijlimiet is
1–60; verborgen rijen worden geteld en mogen niet als afwezig beschouwd
worden. Alleen `--output` schrijft lokaal en maakt exclusief een
**nieuw 0600-bestand**; zonder deze optie schrijft de opdracht stdout.
Er wordt geen Notion-claim, workerjob, PR-review, GitHub-write,
runnerrecovery, test of deploy uitgevoerd.

Wanneer `--compare-previous` is meegegeven, markeert de werklijst
PR-beweging tussen twee bronmomenten, maar zelfs een gewijzigde HEAD
geeft geen automatische eigenaar- of mergevrijgave. Verplichte exact-head
CI, onafhankelijke review, eigenaarsclaims en branchregels blijven externe
verplichtingen.


### Operatorwerkbak: betrouwbare dekking en directe navigatie

Elke kaart scheidt nu **dekking van PR-bestandspaden** van
**dekking van PR→issue-tekstverwijzingen**. De twee onafhankelijke
bronnen worden niet tot één misleidend 'compleet' samengevoegd. Bij
onvolledige vensters staat expliciet `ONVOLLEDIG`; geen enkele bron
bewijst afwezige PRs, een vrije eigenaar of veilig mergerecht.

In de offline HTML staat bij ieder item in de merge-eerst werkbak
**Bekijk kaart**: dit brengt de operator naar dezelfde issuekaart met
de bestaande PR-links, bronhiaten, optionele historische wijzigingen
en, als die expliciet gelezen zijn, exacte-HEAD checkinformatie.
Als de operator een filter actief had, worden de lokale filters voor
die navigatie gereset. Er worden hierbij geen requests verzonden,
geen remote state gewijzigd en geen workflowacties gestart.


## 20 oktober · Interactieve privé P0/P1-werkbak (offline, zonder remote writes)

De bestaande merge-eerst productwerkbak kan nu als een zelfstandige,
responsieve HTML-werkpagina worden gemaakt. De kaarten bevatten
canonieke issue-/PR-links, prioriteit, concrete menselijke volgende stap,
onvolledige-bronwaarschuwingen en precies dezelfde conservatieve
autorisatiegrenzen als de gecombineerde productcockpit.

```bash
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --max-prs 10 \
  --format worklist-html --worklist-limit 30 \
  --output /private/zsentinel-merge-first-desk.html
```

**Nieuw productgedrag:** lokaal zoeken en filteren op prioriteit, project
of handmatige status; per issue een handmatige opvolgstatus (*nog te
bekijken*, *wachten op eigenaar*, *afstemming*, *opvolging genoteerd*)
en een privénotitie tot 700 tekens. Met **Download privéoverdracht**
worden alle momenteel geladen bronkaarten inclusief menselijke notities
als Markdown gedownload. Dat bestand heeft uitsluitend door de browser
bepaalde lokale rechten; behandel het daarom zelf als privé.
Het nieuw gemaakte HTML-bronbestand via `--output` heeft wel 0600.

Er is bewust **geen localStorage**, sessieherstel, externe POST,
GitHub-/Notionmutatie, runneractie, automatische claim of deploy.
De notities bestaan alleen zolang de HTML-pagina openstaat en zijn
niet opgehaald uit Notion of veilig naar zCloud opgeslagen; de browser
waarschuwt bij het verlaten met niet-geëxporteerde menselijke invoer.

Alle output is volledig brongebonden: maximaal 60 kaarten, optionele
`--worklist-limit`, bronmomenten voor issues en PRs, expliciet hoeveel
kaarten buiten de beperkte export vallen en per kaart aparte
PR-bestandsscope en issueverwijzingsdekking. Het ontbreken van een
PR of van overlap in een beperkte steekproef geeft NOOIT vrijgave.
Het nieuwe HTML-formaat hergebruikt de bestaande read-only GitHub
opvraag zonder aanvullende providerrequests.


### Heropen handmatige opvolging via een brongebonden privé-sessie

De interactieve P0/P1-werkbak biedt nu ook **Bewaar lokale sessie (JSON)**
en **Heropen lokale sessie**. Deze optie is apart van de bestaande leesbare
Markdown-overdracht: het privésessiebestand bevat alleen handmatige
statussen en notities (maximaal 700 tekens per getoonde issuekaart),
de exacte volgorde van de lokale bronkaarten en een SHA-256 vingerafdruk
van de oorspronkelijke issue-/PR-bronmomenten en de getoonde canonieke
issue-/PR-links.

Import gebeurt volledig lokaal vanuit een expliciet gekozen bestand;
bestanden groter dan 64 KiB worden geweigerd. Alle bronidentiteiten
en handmatige statuswaarden moeten **eerst allemaal** overeenkomen.
Bij mismatch wordt de hele import geweigerd zonder gedeeltelijke
wijzigingen aan de huidige sessie. Er worden uitsluitend bestaande
`select.value` en `textarea.value` velden gevuld; geïmporteerde
inhoud wordt niet als HTML of JavaScript geïnterpreteerd.

Een nieuwe GitHub-opname of ander PR-head/issuevenster vereist een nieuwe
beoordeling en accepteert de oude sessie bewust niet stilzwijgend.
Handmatig opgeslagen JSON en gedownloade Markdown blijven **privé
bestanden onder beheer van de operator**; alleen het initiële HTML-bestand
dat via `--output` ontstaat heeft automatisch permissies `0600`.

Alle statuswaarden zijn menselijke aantekeningen, geen GitHub-review,
Notion-claim, CI-bewijs, machine-action, merge- of deploybevoegdheid.


### Bestaande PR's gebundeld over meerdere P0/P1-issuekaarten

De merge-eerst operatorwerkbak groepeert een bestaande PR die in
meerdere huidige issuekaarten voorkomt nu **één keer per canonieke
PR-URL**. Een groep toont de hoogste waargenomen issueprioriteit en
alle bronissue-links. Dit verschijnt boven de afzonderlijke acties in
de gecombineerde issuecockpit, de zelfstandige offline HTML-werkbak en
de compacte Markdown-/tekstoverdracht.

De groepsweergave voorkomt dubbel werk: een operator kan de bestaande
PR eerst bij zijn huidige eigenaar laten afronden, in plaats van twee
parallelle fixes voor dezelfde PR-verwijzing te starten. De
**issuevermeldingen zijn echter géén bewijs dat die PR elk issue oplost**.
GitHub-scope, Notion-claims, CI, onafhankelijke reviews en releasegates
moeten nog steeds separaat worden gecontroleerd. Geen claim, merge,
dispatch, self-hosted runnerwrite of workflowactie vindt plaats.


## Oudere, nog-open PR's opzoeken op een volgende GitHub-pagina

Een drukke zCloud-repository heeft meer open PR's dan de eerste
maximaal 48 recent bijgewerkte kandidaten. De bestandsconflicttool kan nu
ook een **expliciete oudere sorteerpagina** inspecteren, zonder het
standaardbudget aan GitHub-GETs per run te vergroten:

```bash
# Controleer oudere open PR's tegen precies de bestaande product-PR scope.
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --source-pr 1567 --open-page 2 --max-prs 24 \
  --format html > ./private-older-pr-collisions.html

# Zoek ook oudere conflicten of dubbele issuevermeldingen tussen PR's
# op diezelfde beperkte oudere pagina.
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs --open-page 3 --max-prs 24 \
  --format markdown > ./private-older-pr-graph.md
```

`--open-page` accepteert expliciet 1–50 en gebruikt dezelfde maximaal
24/48 inspectielimiet als voorheen. GitHub sortering blijft
`updated desc`; vóór en ná het uitlezen van bestanden leest de tool
exact **dezelfde pagina en paginagrootte** opnieuw. De geselecteerde
source-PR krijgt daarnaast dezelfde onafhankelijke HEAD/base-herlezing
als voorheen. De JSON, tekst, Markdown en lokale HTML vermelden
**paginanummer, begin- en eindpositie, de daadwerkelijk gelezen PRs**
en dat eerdere/niet-gelezen pagina's onbekend blijven.

Een pagina groter dan 1 kan **nooit** `pr_file_scope_sample_complete=true`
produceren, zelfs niet als die pagina kort of stabiel lijkt. Het
audit-resultaat verbergt daarom altijd vermeende onafhankelijke of
'overlapvrije' PR-kandidaten op oudere pagina's. Waargenomen botsingen
blijven bruikbaar voor handmatige review, maar géén botsing in zo'n
venster heeft exitcode 3 (nooit claimvrijgave). De twee requests voor de
gekozen PR-pagina worden niet vermenigvuldigd en er is geen automatische
volledige paginacrawl, merge, claim, workflow of deploy.


## Samenhangende PR-conflicten tussen oudere en nieuwere GitHub-pagina's

De aparte oudere-pagina-inspectie was onvoldoende zodra PR #A op pagina 1
een bestand wijzigt dat ook door PR #B op pagina 2 wordt gewijzigd:
een los rapport voor iedere pagina kan dat gedeelde bestand **niet**
herkennen. De bestaande CLI ondersteunt daarom nu een aaneengesloten,
begrensd venster met `--page-count`:

```bash
# Ontdek gedeelde bestandspaden tussen de eerste drie pagina's
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs \
  --open-page 1 --page-count 3 --max-prs 16 \
  --format html > ./private-cross-page-conflicts.html

# Vergelijk één al bestaande product-PR tegen diezelfde 48 kandidaten
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --source-pr 1567 \
  --open-page 1 --page-count 3 --max-prs 16 \
  --format markdown > ./private-existing-pr-scope.md
```

Ook handmatig geplande bestandspaden ondersteunen deze gebonden
cross-page-opvraag. De bron verzamelt per geselecteerde pagina de exact
aaneensluitende `updated desc` resultaten en vergelijkt **over alle
gelezen pagina's samen** de PR-bestanden en mogelijke gedeelde
issuevermeldingen. Hierdoor zijn ook botsingen tussen twee verschillende
pagina's zichtbaar, in plaats van alleen binnen één pagina.

Veiligheidsgrenzen: 1–3 aaneengesloten pagina's, maximaal 48
PR-bestandsinventarissen samen, plus hoogstens zes dubbele
open-PR-lijstlezingen. Bij `--page-count 2/3` gebruikt GitHub
`per_page = --max-prs` zonder de extra single-page lookahead die anders
stilzwijgend elke paginagrens één PR zou overslaan. Op alle pagina's wordt
dezelfde geordende bron **vóór en ná** de bestandinspectie gelezen. Dubbele
PR-identiteiten tussen pagina's of verschoven bronvolgordes zijn expliciet
onstabiele bronwaarnemingen. Een geselecteerde bron-PR wordt apart opnieuw
aan exacte HEAD en base-SHA gebonden.

HTML, tekst, JSON en Markdown tonen de echte pagina's en posities.
Botsingen zijn bestaande PR's met dezelfde bestandspaden, nooit bewezen
Git-mergeconflicten. **Meerdere pagina's worden altijd als gedeeltelijk
behandeld:** een korte laatste pagina of geen botsingen betekent geen
volledige inventaris, geen vrij eigenaarschap, geen werkovername en
geen automatische mergebeslissing. De bestaande Notion-claim-,
review-, beveiligings- en productie-releasegates blijven vereist.
Er wordt geen CI-rerun, QA, queue-update of VPS-mutatie uitgevoerd.


## Zelfde nummer in meerdere product-PR's: titel, body en echt GitHub-issue

Een **concreet productgat** in de eerdere cross-page-controle: bestaande
PR's konden hetzelfde issue noemen in hun titel (`fix(#1326)` of
`... (#1326)`) terwijl de body dat nummer niet herhaalde. De
werkbak vond zulke potentiële dubbele werkzaamheden niet.

De bron combineert nu dezelfde expliciete `#N`-titelverwijzingen die al
in de bestaande issue-first route worden gebruikt, met de reeds bekende
body-verwijzingen. De JSON bewaart **per PR/nummer de bronherkomst**:
`title`, `body` of beide. Een te grote/onbetrouwbare PR-body
maakt de tekstdekking **onvolledig**, zelfs wanneer er wel een
titelverwijzing zichtbaar is. Alle zichtbare issueverwijzingen blijven
slechts tekst-hints, nooit een fix-, ownership-, review- of mergebewijs.

Omdat `#123` in een titel soms ook **PR #123** kan zijn, bestaat
een optionele, strikt begrensde live GitHub-issue-identiteitscontrole:

```sh
python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs \
  --max-prs 16 --open-page 1 --page-count 3 \
  --resolve-issue-identity --format html \
  > ./private-issue-and-pr-overlaps.html
```

Alleen bij zichtbare dubbele nummervermeldingen vraagt deze modus via
GitHub **GET /issues/N** maximaal acht verschillende nummers op.
Hij leest geen volledige issue-body of persoonsgegevens in de output.
De pagina en JSON onderscheiden `confirmed_open_issue_at_observation`,
`confirmed_closed_issue_at_observation`,
`reference_is_pull_request_not_issue`,
`identity_unconfirmed`, `source_unavailable` en `not_observed`.
Een onbeoordeeld nummer wordt nooit als geverifieerd issue gepresenteerd.
De uitvoer toont het requestbudget en ontbrekende dekking.

De verificatie is bewust opt-in; zonder de vlag wordt **geen extra
GitHub-GET** toegevoegd. Hij is uitsluitend voor de bestaande
`--audit-open-prs`-modus en kan niet met een rewrite, merge, workerclaim,
queue-update of workflow-dispatch worden gecombineerd. Het gaat alleen
om de identiteit van het genoemde nummer **op één observatiemoment**:
de echte issue-status, toegewezen eigenaar, productfix, conflicten,
verplichte CI, onafhankelijke review en productie-release blijven aparte
beslissingen. Zelfs een geverifieerd open issue verleent geen bevoegdheid.


### GitHub-identiteit daadwerkelijk gelezen

De optionele identiteitscontrole verwerkt het echte onderscheid in
GitHub's `GET /issues/N`-response: een regulier issue heeft een
`html_url` eindigend op `/issues/N`, terwijl een pullrequest op
dezelfde API-route een `pull_request`-veld en `/pull/N` heeft.
De bron verifieert eerst het exacte nummer en de juiste URL voor het
responstype, voordat het een mogelijke dubbele issue-opdracht labelt.

Concreet op de bestaande zCloud-bron read-only gezien op 20 oktober:
[issue #1326](https://github.com/Zennay/zCloud/issues/1326) geeft een
echt issue terug; [PR #1536](https://github.com/Zennay/zCloud/pull/1536)
komt als pullrequest terug. De eerste bronversie zou een PR door zijn
`/pull/N`-URL abusievelijk `identity_unconfirmed` noemen; dat
productgedrag is gecorrigeerd.

Als de GitHub-response ontbreekt, inconsequent is of door rechten/rate
limiting niet kan worden gelezen, blijft `identity_status` onbekend.
Zelfs een volledig uitgevoerde lijst GETs wordt expliciet als
**onvolledig bewijs** gepresenteerd zodra één geobserveerde identiteit
onbereikbaar/onduidelijk is. Geen bestaande GitHub-issue-status wordt
naar een claim, fix, merge of live herstel gepromoveerd.


### Geen verkeerd herstelwerk starten door een PR-nummer

De optionele bronwaarneming is nu gekoppeld aan een **menselijk
operatorvervolg**, zichtbaar in JSON, tekst, Markdown en de private HTML:

- `confirmed_open_issue_at_observation` →
  `review_existing_prs_for_same_open_issue`. Inspecteer eerst de
  bestaande PR's bij dat issue; dit is geen fix of eigenaarschapsbewijs.
- `confirmed_closed_issue_at_observation` →
  `review_closed_issue_before_more_product_work`. Sluiting kan een
  externe beslissing zijn, dus geen nieuwe claim op basis van een titel.
- `reference_is_pull_request_not_issue` →
  `inspect_referenced_pr_not_issue`. Zo verdwijnt een foutieve
  aanname van dubbel issuewerk; integreer het bestaande PR eerst.
- Onbereikbaar, onvoldoende of niet aangevraagd →
  `verify_reference_identity_before_routing`. De route blijft open
  totdat een echte bron is waargenomen.

De gevolgen blijven **weergave en menselijke triage**. Er wordt niets
automatisch geclaimd, gedispatcht, betaald, gemerged of gedeployed.
De status `issue_identity_lookup_incomplete` wordt ook waar wanneer
een uitgevoerde GitHub-opvraag een onduidelijk/afgewezen resultaat
teruggeeft: acht ontvangen responses zijn niet gelijk aan acht
geverifieerde identiteiten. Open issue- en PR-nummers uit dit
werkoverzicht worden nooit als bewijzen van codefix of live herstel
gebruikt.


### Prioriteit bovenaan: alleen voor werkelijk waargenomen open issues

Bij opt-in issue-identiteitslezing toont de collision workbench nu de
bronprioriteit van een **bevestigd open GitHub-issue**. Alleen een
expliciet P0/P1-issue-label (waaronder de reeds bestaande
`priority:p0`/`priority:p1`-varianten) of titelprefix
`P0`/`P1` wordt meegenomen. Onjuiste of onvolledige bronvelden
geven `unclassified` of `not_observed`, nooit een gegokte
prioriteit. Geverifieerde P0-kandidaten komen vóór P1 en vóór de
onbekende kandidaten in de visuele werkbak. Binnen dezelfde groep
worden de bestaande PR-vermeldingen behouden.

Deze volgorde geldt **alleen binnen de brongebonden en maximaal acht
opgevraagde issue-identiteiten**, niet voor het gehele GitHub-project:
de resterende of niet bekeken pagina's kunnen zelf ook P0 bevatten.
In JSON, tekst, Markdown en HTML staan waargenomen prioriteit en de
human-only vervolgactie apart bij ieder nummer. Een `P0`-kaart
beweert niet dat een bestaande PR dat issue repareert, de eigenaar
vrij is, een vereiste check groen is of een veilige productieactie
is toegestaan. Die autorisatie blijft buiten de scanner.


## Veel open PR's: meerdere opgeslagen conflictvensters samenvoegen

De live GitHub-PR-conflictkaart kan bewust maar **maximaal 48 PR's
per run** scannen. Daardoor zou PR #A op pagina 1 en PR #B op pagina 5
elkaars gewijzigde bestand nog altijd niet samen in hetzelfde
live-resultaat kunnen tonen. De nieuwe **privé offline
cross-window-composer** lost deze bronintegratiekloof op zonder het
live GitHub-verzoekenbudget, rechten, workers of releaseproces te verhogen.

```sh
# Privé locatie: de eerdere audit schrijft zelf alleen naar stdout.
umask 077

python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs \
  --open-page 1 --page-count 3 --max-prs 8 --format json \
  > ./graph-pages-1-3.private.json

python3 scripts/zsentinel_pr_scope_guard.py \
  --project cloud --audit-open-prs \
  --open-page 4 --page-count 3 --max-prs 8 --format json \
  > ./graph-pages-4-6.private.json

# Combineren zonder extra GitHub GET of workflow:
python3 scripts/zsentinel_pr_saved_graph_merge.py \
  --repo Zennay/zCloud \
  --input ./graph-pages-1-3.private.json \
  --input ./graph-pages-4-6.private.json \
  --format html --output ./private-cross-window-conflicts.html
```

Met `--format json`, `markdown`, `text` of `html` geeft
de composer de **nieuwe bestandsparen over verschillende
opgeslagen vensters**. Botsingen binnen één reeds opgeslagen venster
staan al in de afzonderlijke graph, en worden daarom niet onnodig
verdubbeld. HTML bevat canonieke GitHub-links, de werkelijk gedeelde
bestanden en een waarschuwing dat het geen atomische live waarneming is.
Er wordt één nieuw bestand met `0600` aangemaakt, bestaande uitvoer
nooit overschreven.

De invoer is minimaal twee en maximaal zes bestaande privé Graph-JSON
bestanden uit dezelfde expliciete GitHub-repository, hoogstens 288
PR's totaal. Per bestand wordt de exacte bronstructuur,
pagina-afbakening, SHA-identiteit, PR-bestandslijst en stabiele
voor-/nalezing geëist. Dubbele bronpagina's, dezelfde PR in meerdere
vensters, veranderende evidence, symlinks, ongeldige JSON, te grote
bronbestanden en observatiemomenten meer dan tien minuten uiteen
worden geweigerd; een incomplete PR-bestandslijst blijft zichtbaar
als **incomplete evidence**. Deze laatste grens voorkomt dat oude
historische PR-overzichten als één huidig snapshot worden voorgesteld.

**Oplevergrens:** de combinatie is per definitie
`source_is_atomic_current_snapshot=false` en
`source_complete_for_all_open_prs=false`. Geen botsing in de
samengevoegde resultaten betekent dus niet dat een PR vrij is van
conflicten. Het product voert geen claims, GitHub writes, workflows,
CI, tests, queue-mutaties, workeracties of merges uit; het is
uitsluitend een private read-only operatorinventaris. De actuele
Notion-/GitHub-eigenaarsclaims, beschermde main, verplichte exact-head
CI, reviews en geautoriseerde deploy blijven onafhankelijke voorwaarden.


### Eén gecombineerde werkbak voor cross-window bestands- én nummeroverlap

De opgeslagen Graph-JSON bevat ook bestaande *tekstuele*
`#N`-verwijzingen met `title`/`body`-herkomst. De privé-composer
verifieert dat die hints alleen aan PR's met dezelfde **exacte
waargenomen HEAD** uit het betreffende bronvenster zijn gekoppeld,
en zoekt nu eveneens naar hetzelfde genoemde nummer in **twee
verschillende bewaarde vensters**. Resultaten zijn zichtbaar als
`cross_snapshot_issue_number_hints_observed` met canonieke
PR-links en oorspronkelijke bronvensternummers, apart van
`cross_snapshot_file_collision_pairs_observed`.

Dat voorkomt dat twee PRs op bijvoorbeeld pagina 1 en pagina 5
aan het *zelfde benoemde probleem* werken zonder gedeelde bestanden,
maar voor de operator onzichtbaar blijven. Een titel/body-`#N`
is echter niet gecontroleerd als echt GitHub-issue (het kan ook een
PR-nummer zijn), laat staan als opgeleverde fix. Daarom krijgt dit
offline resultaat **nooit** de optionele live
`--resolve-issue-identity`-status uit een ouder bestand als
actuele waarheid mee. Actuele GitHub-status, actief eigenaarschap,
verplichte CI, review en main/deploy moeten afzonderlijk worden
vastgesteld. Binnen één eerder bekeken venster getoonde
issuevermeldingen worden niet als nieuwe cross-window-conflicten
herhaald.


## Directe GitHub-venstercollectie: één CLI, één privéresultaat

De offline composer kan nu **ook zonder handmatig opgeslagen
tussenbestanden** worden gebruikt. Voorheen moest de operator meerdere
`--audit-open-prs`-commando's starten en binnen tien minuten alle
bronbestanden combineren. Het nieuwe `--live-scan`-pad gebruikt dezelfde
bestaande read-only GitHub-conflictobservatie en de **exactzelfde
bronvalidatie en gecombineerde rapportgenerator** als de privébestanden:

```sh
# Een complete read-only operatoruitvoer in één commando:
umask 077
python3 scripts/zsentinel_pr_saved_graph_merge.py \
  --repo Zennay/zCloud --live-scan \
  --start-page 1 --windows 3 --prs-per-page 6 \
  --format html --output ./private-live-cross-window.html
```

Resultaat: **zes aansluitende PR-pagina's**, verdeeld over drie
afzonderlijk dubbel gelezen bronvensters, maximaal 36 PR-identiteiten
en maximaal 48 GitHub GETs. De eerdere offline route met herhaalde
`--input` blijft volledig ondersteund. Voor grote repo's zijn
beide opties bruikbaar: de directe variant voor een nieuwe, beperkte
steekproef en de offline variant voor grotere reeds vastgelegde
vensters (hoogstens 288 PRs). Alle resultaten worden als nieuw,
privé `0600`-bestand aangemaakt en bestaande bestanden nooit
overschreven. `--format` ondersteunt HTML, Markdown, tekst of JSON.

**Harde live grenzen:** `--windows` is 2–3, iedere venstergroep
bevat precies twee aaneengesloten pagina's, maximaal twaalf PR's
per pagina, maximaal 36 PR-bestandsinventarissen totaal en een
startpagina waardoor de gekozen reeks binnen pagina 1–50 valt.
De vraaglast is maximaal `windows × (4 + 2 × prs_per_page)`
publieke read-only GitHub GETs. De CLI maakt geen extra
issue-resolutie-GETs en vereist voor live collecties een expliciete
`--live-scan`-vlag. `GITHUB_TOKEN` is optioneel voor toegestane
privérepositories; hij wordt nooit in het rapport opgenomen.
Live en `--input` zijn bewust onderling exclusief.

Het rapport vermeldt expliciet
`collection_mode=current_run_bounded_github_GET_only` of
`private_saved_graph_files`, het aantal bronvensters en voor
live collecties het maximale GET-budget. De **opeenvolgende** GETs
worden niet als een atomische, gegarandeerd actuele PR-lijst
gepresenteerd. Veranderende dubbele bron-PRs, onstabiele
voor-/nalezing, overschrijden van bronlimieten of veranderende
identiteit leiden tot weigering; andere niet-gelezen pagina's,
ongetrackte branches, Notion-leases en vereiste CI/reviews
blijven onbekend. Dit is geen automatische dispatcher of
productieactie en geeft nooit edit-, claim-, merge- of deployrechten.
