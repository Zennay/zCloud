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

De code leest uitsluitend GitHub `GET /repos/{owner}/{repo}/pulls` en `GET /pulls/{number}/files`. De bron is beperkt tot de eerste maximaal 100 open PR's en maximaal 100 bestanden per PR; een bron die te groot is wordt als **onvolledig** gemarkeerd, niet als conflictvrij. De open-PR-heads worden vóór en na de bestandsscan opnieuw gelezen; elke verandering maakt de inventaris onvolledig. Ook hernoemde voormalige bestandspaden worden meegenomen.

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

De bron is een begrensde GitHub-GET-steekproef: maximaal 48 open PR's worden geïnspecteerd, op elk maximaal 100 bestandspaden, en maximaal 200 botsende PR-paren weergegeven. De bron-HEAD en base-SHA van alle PR's worden in de vóór-/nalezing gepind; padhernoemingen tellen voor zowel oude als nieuwe naam. Een eerste pagina met precies 100 items, de ingestelde limiet, een bestandslijst met precies 100 records, veranderde PR-identiteit of afgekapte paarlijst zijn expliciet onvoldoende voor een volledige open-PR-inventaris. GitHub/Notion/workerclaims worden nooit gewijzigd.

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
