# zSentinel · Runtime incident router (product-only)

## Privé-opvolgnotities en optioneel Notion actief werk (19 oktober)

De offline HTML-bediening kan na een **bewuste voorvertoning** maximaal
24 **bewust gewijzigde** opvolgkeuzes/notities (maximaal 300 tekens
per issue) binnen de **huidige browserfilters** als privé-JSON exporteren.
Bij een gecombineerd portfolio kan de operator per project filteren en
zo meerdere kleine privébatches bewaren; ongewijzigde standaardwaarden
worden niet onnodig gekopieerd. Via een apart gekozen importbestand kunnen dezelfde notities
in een nieuw bronvenster teruggezet worden, **uitsluitend wanneer de
canonieke GitHub-issue-URL en projectidentiteit overeenkomen**. De import
vereist expliciete browserbevestiging, weigert dubbele/onbegrensde entries,
leest maximaal 64 KiB en gebruikt uitsluitend tekstveldwaarden; hij
raakt geen live queue, GitHub, Notion, worker, browseropslag of netwerk.
Bij elke wijziging moet de handmatige overdracht opnieuw worden bekeken.
Deze privénotities zijn *nooit* statusbewijs, toestemming of actieve claim.

De bestaande read-only Notion-claimobserver kan daarnaast bewust worden
ingeschakeld om te laten zien of er momenteel **actief projectwerk**
in Notion staat (niet om dat werk over te nemen):

```sh
# Vereist een al geautoriseerde privé NOTION_TOKEN uit de omgeving.
# De data-source-UUID moet overeenkomen met de eigen Portfolio Work Queue.
umask 077
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --read-notion-claims \
  --notion-data-source 86e406fd-2c99-4ef5-8058-363c1004b3eb \
  --format html > /private/operator-only/zsentinel-met-notion.html
```

Notion is **opt-in**: standaard geen token nodig en geen extra requests.
De adapter gebruikt het bestaande strikt gebonden maximaal 4 pagina's ×
100 actieve statussen leesvenster. Alleen projecttellingen/bronmoment komen
in het herstelbord, geen privé-taaktitels of token. Positieve telling
verschijnt als *minstens N actief*; een afwezige telling is **ONBEKEND**,
nooit "project vrij". Notion-statussen bewijzen geen file-scope-claim,
lease-eigenaar, werktoewijzing of merge-/releaseautorisatie. Zonder geautoriseerde Notion-bron of bij providerfout wordt de
Notion-dekking expliciet `unavailable`, blijven de projecttellingen
`ONBEKEND` en wordt de eenmalige uitvoer met een niet-successtatus
afgerond, maar **de reeds opgehaalde GitHub-productissues en bestaande
PR's blijven zichtbaar/exporteerbaar**. Externe foutmetadata of tokens
worden niet getoond. Zonder de optionele Notion-vlag is deze afhankelijkheid
helemaal niet van toepassing.

## Echte voortgang tussen twee bronmomenten (19 oktober)

Met `scripts/zsentinel_issue_progress.py` ondersteunt het zelfstandige
herstelbord nu een expliciet lokaal bewaarde **vorige JSON-momentopname**.
De gebruikersbediening vergelijkt maximaal 24 bronissues per gekozen
venster van maximaal drie canonieke projecten, nooit een volledige
GitHub-backlog. Hetzelfde venster en exact dezelfde volgorde van projecten
is verplicht; afwijkende of onbetrouwbare bronidentiteit wordt geweigerd.

```sh
# Eerste bronmoment: uitsluitend zelfbewuste privé-export naar lokale JSON.
umask 077
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --format json > /private/operator-only/zsentinel-issues-eerder.json

# Tweede bronmoment, wanneer er nieuwe informatie beschikbaar is:
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud \
  --previous /private/operator-only/zsentinel-issues-eerder.json \
  --format html > /private/operator-only/zsentinel-issues-verandering.html

# Of compacte machineleesbare bronverschillen:
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud \
  --previous /private/operator-only/zsentinel-issues-eerder.json \
  --format json > /private/operator-only/zsentinel-issues-met-verandering.json
```

De vorige JSON wordt alleen op expliciet opgegeven pad read-only ingelezen
(maximaal 512 KiB, reguliere file, geen symlink, duplicate keys verboden,
maximaal 30 dagen oud en niet later dan de nieuwe waarneming). De CLI maakt
zelf geen historydatabase, cron, automatische alert of watch-taak aan.

De offline cockpit toont **nieuw in de steekproef** (geen bewijs van
een nieuw issue), **veranderde prioriteit/PR-verwijzing, eigenaarcontrole-
route, geobserveerde PR-SHA of opvolgactie**, en **eerder gezien maar nu
buiten de steekproef**. Wanneer beide snapshots expliciet over geldige
Notion-statusbronnen beschikken en positieve ondergrenzen tonen, wordt
ook een gewijzigde *Notion actief-werk ondergrens* weergegeven; afwezig
blijft `ONBEKEND`, nooit "0 werk" of "claim vrij".
Ook de laatste categorie betekent *nooit* dat een issue gesloten of
hersteld is; de machineleesbare bron zet `closed_issues_verified=0`.
Als de nieuwe GitHub-bron **helemaal niet bereikbaar is**, wordt zelfs
"niet opnieuw in de steekproef" niet geconcludeerd. Het rapport toont
dan afzonderlijk `previously_sampled_unverifiable_count` met de
bronstatus `current_source_unavailable_previous_state_only`. Bij een
wel gedeeltelijk leesbare bron blijft een afwezig issue slechts
`not_in_current_bounded_sample_NOT_CLOSED`.
Onvolledige bronvensters en oudere momentopnames degraderen zekerheid,
ze authoriseren nooit claim, automatische retry, merge of productieactie.
De browser heeft een extra filter voor bronverandering en neemt de
bewuste vergelijking op in een lokale handmatige overdracht.

## Brongebonden conceptwerkpakket voor een bevoegde worker (19 oktober)

De zelfstandige én gecombineerde offline cockpit laten nu **expliciet**
maximaal twaalf productissues selecteren als kandidaat-werkpakket.
Echte owner-only infrastructuurissues (#1326 runner, #1523 billing)
hebben geen werkpakket-selectie. De operator ziet eerst de volledige
JSON in een **afzonderlijke voorvertoning**. Daarna kan uitsluitend door
een tweede expliciete klik een lokale
`zsentinel-concept-werkpakket.json` worden gedownload.

Het pakket `zsentinel-product-work-packet-draft-v1` bevat per issue
canoniek project, originele GitHub-issue-URL, originele titel,
prioriteit, gewenste vervolgrichting, bijbehorend bronmoment,
onbevestigde bestaande PR-links en (bij meerdere vensters) het
oorspronkelijke bronvenster. De JSON vermeldt altijd:

- `requires_current_claim_file_ci_review_check=true`: de volgende
  worker of bevoegde operator moet eigenaren, actieve Notion-/runtime-
  claims, bestandscope, bestaande PRs en required CI opnieuw controleren.
- `owner_verified=false`, `permission_to_dispatch=false`,
  `permission_to_merge=false`, `claims_taken=false` en
  `writes_performed=false`: een pakket **is geen claim, goedkeuring,
  workflow-dispatch of release**.

Filter- en selectiewijzigingen maken het voorbeoordeelde pakket
ongeldig. Als een voorheen recent bronmoment tijdens de bediening
ouder dan tien minuten wordt, is een nieuwe expliciete (als historisch
gemarkeerde) voorvertoning vereist. Pakketexport vindt uitsluitend
lokaal in het huidige browsertabblad plaats: geen localStorage,
providerrequest, tokens of automatische workerworkflow. Het bestaande
veiligheids- en productbeleid blijft leidend wanneer iemand het
pakket later bewust in een andere tool aanbiedt.

## Portfolio-breed gecombineerd productherstel (19 oktober)

Het nieuwe `scripts/zsentinel_portfolio_workboard.py` combineert één tot
zes **afzonderlijk vastgelegde, disjuncte** privé-JSON-bronvensters van
`zsentinel_issue_delivery.py` tot één **zoekbaar, responsief portfolio-bord**
voor maximaal 18 canonieke projecten en 144 issuekaarten. Het werkbord
hergebruikt exact dezelfde offline filters, PR-bronacties, persoonlijke
opvolgnotities en bewuste voorvertoning/export. Binnen iedere prioriteit
worden projecten eerlijk verweven zodat één druk project andere
productissues niet uit beeld duwt. Geen nieuwe GitHub/Notion/queue-requests
bij samenvoegen, en geen nieuwe workerclaims.

```sh
# Maak aparte actuele JSON-opnames; live ophalen blijft begrensd op max.
# drie projecten per commando en drie gerelateerde exacte-head PR-bronnen.
umask 077
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --project supa --format json > /private/venster-1.json
python3 scripts/zsentinel_issue_delivery.py \
  --project ftmo --project lightup --format json > /private/venster-2.json

# Eén gecombineerd lokaal HTML-bord, ook als een venster onvolledig was.
python3 scripts/zsentinel_portfolio_workboard.py \
  --snapshot /private/venster-1.json \
  --snapshot /private/venster-2.json \
  --format html > /private/portfolio-werkbord.html
```

De uitvoer kan bovendien expliciet veilig worden aangemaakt via
`--output`, zonder shell-redirect en zonder bestaande bestanden te
overschrijven. De doelmap **moet al bestaan**. De CLI maakt uitsluitend op
verzoek een nieuw .html/.json/.md-bestand met permissie `0600`,
`O_EXCL`/`O_NOFOLLOW` en maximaal 6 MiB; een bestaande naam of symlink
wordt geweigerd. Dit is een **operatorrapport** en nooit een queue-,
GitHub-, Notion- of workerwrite.

```sh
# Maak eerst één bestaande privédoelmap met jouw normale gebruikersrechten:
mkdir -p "$HOME/zsentinel-privé"
chmod 700 "$HOME/zsentinel-privé"

python3 scripts/zsentinel_portfolio_workboard.py \
  --snapshot "$HOME/zsentinel-privé/venster-1.json" \
  --snapshot "$HOME/zsentinel-privé/venster-2.json" \
  --format html \
  --output "$HOME/zsentinel-privé/portfolio-werkbord.html"
```

Dezelfde `--output`-mogelijkheid werkt bij de zelfstandige
`zsentinel_issue_delivery.py`. Zonder deze vlag blijft de standaard
stdout-weergave bestaan. Bij een gedegradeerde GitHub-bron kan het
expliciete rapport worden aangemaakt, maar het proces houdt een
niet-succesexitcode en vermeldt de onvolledige bron.

De invoer moet uit unieke canonieke projecten bestaan (geen dubbel
project, issue of vervalste repoidentiteit). Elk bestand maximaal 512 KiB,
30 dagen bronhistorie en maximaal 24 issues; onvolledige of verlopen
bronnen blijven zichtbaar als onbekend/VEROUDERD, niet als opgeloste
issues of vrije workerclaims. De browser gebruikt het **oudste**
onderliggende bronmoment voor de globale freshnesswaarschuwing. Per
project blijft de exacte oorspronkelijke tijd en GitHub-dekking zichtbaar.
Een niet-volledig of verouderd gecombineerd venster verlaat de CLI met
status 3, maar behoudt het leesbare rapport. Geen verborgen continu
verzamelen, andere API-rechten of achtergrondservice.

### Eén operatorbord met bestaande lokale queue-pressure (opt-in)

Een eerder **bevoegd, read-only** aangemaakt JSON-rapport van
`scripts/zsentinel_portfolio_overview.py --format json` kan aan het
standalone of gecombineerde werkbord worden gekoppeld:

```sh
python3 scripts/zsentinel_portfolio_workboard.py \
  --snapshot /private/venster-1.json --snapshot /private/venster-2.json \
  --queue-overview /private/bestaande-zcloud-portfolio.json \
  --format html > /private/portfolio-met-wachtrij.html
```

Dit maakt in dezelfde UI wachtende taken, actieve statussen, claim-churn
en verlopen lease-rijen zichtbaar. Alle 13 (of de actuele canonieke)
projectrijen en dezelfde repositoryidentiteit moeten aantoonbaar aanwezig
zijn; de lokale queue-bron wordt nergens stilzwijgend ingevuld. De source
is maximaal 1 MiB en maximaal 30 dagen oud; >10 minuten verschijnt als
**VEROUDERD**. Bij een onvolledige queue-observatie blijven counters
expliciet `ONBEKEND`. Ook een waargenomen `owned=0` is **nooit**
file-scope- of lease-eigendomvrijgave. Het werkbord doet geen queue-, DB-,
lease-, worker-, CI-, dispatch- of release-write. Als de lokale bron ontbreekt
kan de operator de optionele vlag weglaten en verder met GitHub-only.

## Productissues direct naar een bruikbaar herstelwerkbord (19 oktober)

Het nieuwe standalone `scripts/zsentinel_issue_delivery.py` werkt zonder
VPS-productiedatabase, Notion API-token of scheduler. Het leest de bestaande
canonieke GitHub-productissuebron (P0, P1, P2 en onbekende prioriteit;
QA-only uitgesloten), hergebruikt **bestaande** PR-referenties en leest
maximaal drie geselecteerde exacte-head PR's via de bestaande veilige
read-only bronadapter. De eigenaar/scope/claim blijven nadrukkelijk
onbekend; een PR-verwijzing bewijst nooit dat het issue is opgelost.

```sh
# Gebruik een privé-outputdirectory. Geen background-daemon of workflow dispatch.
umask 077
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --project supa --format html \
  > /private/operator-only/zsentinel-productherstel.html

# Machineleesbaar rapport voor menselijk issue-overzicht:
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --format json > /private/operator-only/zsentinel-issues.json

# Duidelijke menselijke overdracht op basis van dezelfde begrensde bronnen:
python3 scripts/zsentinel_issue_delivery.py \
  --project cloud --format md > /private/operator-only/zsentinel-issues.md
```

Het offline HTML-bord heeft direct werkende filters voor titel, project,
prioriteit en route; privé-opvolgkeuze en notities (maximaal 300 tekens per
issue) leven **alleen in het huidige browsertabblad**, zonder cookies,
localStorage of externe requests. Een Markdown-overdracht kan pas via
**voorvertoning en afzonderlijke downloadknop** worden opgeslagen. Bij
filter- of notitiewijzigingen vervalt de eerdere voorvertoning. De
handmatige overdracht bewaart ook het oorspronkelijke **UTC-bronmoment**,
kanonieke bestaande PR-links en de reeds ontvangen, begrensde head-/CI-
bronactie. Een HTML-document dat meer dan tien minuten oud is of een
afwijkende systeemklok heeft, krijgt expliciet **HISTORISCH / OPNIEUW
CONTROLEREN** in de browser én in de bewust gedownloade overdracht. Niets
wordt automatisch naar GitHub, Notion, een worker of een eigenaar gestuurd.

Per uitvoering maximaal **drie canonieke projecten**, **24** bronissues,
**drie** gekoppelde bestaande PR's en maximaal **24 GitHub GETs**
(3 per project voor issues/PR-index + 5 per exact geselecteerde PR).
403/429 of ontbrekende bron blijft onbekend en stopt vervolglezingen na
rate-/autorisatieweigering. P0 runnerlistener #1326 en GitHub billing
#1523 staan als *human-only*, nooit als automatische retry-/herstartactie.
Ook lege bronvensters bewijzen geen afwezige problemen, en een GitHub
assignee is geen geverifieerde actieve queueclaim. P0-infrastructuurissues
die echte bevoegde eigenaaractie vereisen (momenteel runner #1326 en
billing #1523) blijven zichtbaar onder een **apart uitklapbaar
eigenaarsoverzicht**. Zij verdringen daardoor geen zelfstandig
uitvoerbaar productissue op het hoofdwerkbord; beide categorieën blijven
in de JSON-bron met afzonderlijke telling beschikbaar.

**Geen claim-, scheduler-, test-, CI-retry-, betaal-, review-, merge-,
release- of productieactie.** De bestaande protected-main, exacte-head
required checks, onafhankelijke review, FTMO/security/device-gates en
zCloud runtime-eigenaarschap blijven onafhankelijk van het herstelbord.

## Zelf bestaande open PR's vinden en afmaken (19 oktober)

De PR-afrondingsdesk toont ook maximaal vijf canonieke **same-repository**\nissueverwijzingen uit de bestaande PR-beschrijving als directe links,\nzonder extra GitHub-GETs. Zo is in één oogopslag te zien op welke\nproblemen een PR zich beweert te richten; zo'n tekstverwijzing bewijst\nnoch een open issue, noch eigenaarschap, noch dat de fix echt werkt.\nBij een gewijzigde PR-head worden ook de oude verwijzingen gewist uit\nhet geretourneerde bronbewijs.\n\nDe PR-afrondingsdesk kan nu zonder bekend PR-nummer de recent
bijgewerkte **bestaande** pull requests uit het project ophalen. Hij
gebruikt `state=open`, de eerste 20 PR's per canoniek project en
maximaal **één extra GitHub-GET per project**. Expliciete P0/P1-titels
gaan voor; daarna overige PR's, per prioriteit eerlijk over projecten
verdeeld. Deze titelprioriteit is een **sorteringshint**, geen
geverifieerde issue-ernst of eigendomsclaim. Een concept-PR wordt in
dezelfde prioriteitsgroep ná een niet-concept gekozen.

```sh
# Werkt zonder VPS of SQLite: selecteer maximaal 3 recente open PR's.
python3 scripts/zsentinel_pr_delivery.py \
  --project cloud --discover-open --format html > zsentinel-open-prs.html

# Optioneel: beoordeel eerst exact PR 1563 en vul de overige plekken aan.
python3 scripts/zsentinel_pr_delivery.py \
  --pr cloud:1563 --project cloud --discover-open --format json

# Binnen het portfoliovenster: eerst expliciete PR, dan PR's uit issues,
# daarna pas overige recent open PR's voor nog vrije plekken.
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 \
  --inspect-referenced-prs --discover-open-prs --format html \
  > /private/operator-only/zsentinel-existing-open-prs.html
```

GitHub-rate-limits of een onbereikbare repository stoppen aanvullende
waarneming zonder het bestaan van andere PR's te ontkennen. De cockpit
toont expliciet bronfouten en als het venster met 20 PR's afgekapt is.
Nul geselecteerde PR's betekent **nooit** nul open PR's. De actieve
Notion-claim, on-gePR'de branches, bestandspad-overlap, required checks,
review-/releasebeleid en echte productiestatus worden niet als vrij
afgeleid uit deze beperkte API-steekproef.

Maximaal 3 projecten × 1 nieuwe discovery-GET en 3 PR's × 5
exact-head GETs: **18 aanvullende GETs** voor standalone.
Het opt-in portfolio heeft daarnaast maximaal 42 bestaande GETs,
dus **60 per operatorrun** in de grootste combinatie. Zonder
`--discover-open`/`--discover-open-prs` blijft het extra
discovery-verbruik nul. Geen scheduler, claim, review, dispatch,
merge, release of muterende HTTP-actie is toegevoegd.

## Bestaande PR's afronden: actuele exacte-head beslisdesk (19 oktober)

De nieuwe `scripts/zsentinel_pr_delivery.py` leest uitsluitend **bestaande,
expliciet gekozen of beperkt ontdekte** pull requests uit de canonieke projectregistratie.
Dezelfde functie heeft nu ook een **zelfstandige CLI**, zonder een
SQLite-productiewachtrij of VPS. Alleen `projects.json` en geautoriseerde
read-only GitHub-toegang zijn vereist. Zo kan een operator buiten het
geblokkeerde self-hosted runnerpad toch bestaande PR's beoordelen:

```sh
# Zelfstandige offline HTML-weergave na maximaal vijf GETs per PR:
python3 scripts/zsentinel_pr_delivery.py --pr cloud:1563 --format html > zsentinel-pr-1563.html

# Eén tot drie bestaande PR's met bronbewijs en eigenaaradvies als JSON:
python3 scripts/zsentinel_pr_delivery.py --pr cloud:1563 --format json

# Menselijke productoverdracht van bestaande PR's, direct lokaal als Markdown.
# Inclusief exacte HEAD, moderne + klassieke GitHub-checks, PR-tekst-issueverwijzingen
# en brongebonden volgende stap (nooit merge- of claimtoestemming).
python3 scripts/zsentinel_pr_delivery.py \
  --pr cloud:1563 --format md > /private/operator-only/zsentinel-pr-handoff.md
```

De optionele Markdown-overdracht is voor een **bewust gekozen menselijke**
reviewer of release-eigenaar. Zij bevat uitsluitend gebonden PR-metadata en
**niet-geverifieerde** issueverwijzingen, geen tekst van privéincidenten,
tokens, opdrachten, automatische API-schrijfoperaties of machtigingen. Een
gewijzigde HEAD of ontbrekende statuscontext maakt de bron onvolledig;
de bevoegde eigenaar moet alsnog de echte GitHub-gates controleren.

Geen automatische periodieke uitvoering: dit is een eenmalig operatorcommando.
Het gegenereerde HTML-document is volledig lokaal, zonder scripts, netwerk,
cookies of externe resources. `GITHUB_TOKEN` mag worden aangeboden als
omgevingvariabele voor reeds toegestane privébronreads maar wordt niet
opgenomen in het rapport. Dit commando leest **geen** Notion, queue,
worker, runtime, VPS of service. Het product kan dus functioneren terwijl
de VPS/self-hosted workflow afhankelijkheid geblokkeerd is.

Binnen één portfolio-venster is dit direct beschikbaar als opt-in:

```sh
# Project 'cloud' zit standaard in --window 0. Geen productie-DB aanmaken.
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 \
  --inspect-pr cloud:1563 --format html \
  > /private/operator-only/zsentinel-existing-prs.html

# Of uitsluitend PR-referenties uit de actuele beperkte issue-shortlist.
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 \
  --inspect-referenced-prs --format json
```

Maximaal drie PR's per operatoractie en **vijf extra GitHub-GETs per PR**:
exacte PR-metadata met `head.sha` en `base.sha`, de eerste 30
check-runs **op die HEAD**, maximaal 30 klassieke commit-statuscontexten
**op dezelfde SHA**, de eerste 30 review-ontvangsten, en een
hernieuwde PR-metadataread die een tussentijdse head/base-wijziging afwijst.
Zonder expliciete PR-inspectie of discovery zijn dit **nul** extra GETs.
Met inspectie zonder open-PR-discovery maximaal 15 extra GETs bovenop
42, dus maximaal **57** bij drie projecten. Met open-PR-discovery
komt maximaal één extra GET per geselecteerd project bij: **60** totaal.

Wanneer de portfolio-CLI tegelijk een productissue-shortlist en
`--inspect-pr`/`--inspect-referenced-prs` gebruikt, verschijnt de reeds
opgehaalde exacte-head PR-actie ook **in de gekoppelde issuekaart**.
Daarvoor wordt niets extra opgehaald: alleen PR-links die de bestaande
issuebron zelf noemt en waarvan de geselecteerde PR expliciet is
geobserveerd, worden verrijkt. Bij een onvolledige PR-bron toont de
issuekaart uitsluitend de verplichte nieuwe eigenaarinspectie.
Hetzelfde brongebonden PR-advies staat ook in de JSON-shortlist als
`inspected_linked_prs` en in de platte tekstuitvoer bij het oorspronkelijke
issue. De referentie draagt expliciet `issue_fix_verified=false` en
`merge_authorized=false`; dezelfde ontvangen PR-bron wordt voor alle
uitvoerformaten hergebruikt, zonder verborgen vervolgrequests.
Een link of groene check is nooit bewijs dat het issue al is opgelost.

De Nederlandse operatorweergave laat per PR zien wat de eerstvolgende
**menselijke** stap is: concept afronden; reviewverzoeken verwerken;
actuele gefaalde CI herstellen; wachten op ontbrekende checks; onafhankelijke
review vragen; of volledige release-policy vooraf laten controleren.
Alle selecties blijven bronsteekproeven. Een eerdere approval op een
andere commit-SHA telt **niet** mee als exacte-head observatie.
Afgeronde GitHub-checks met status `neutral`, `skipped` of onbekende conclusie
blijven apart *onbeslist*; ze worden noch als geslaagde build noch als echte
gefaalde bronbuild gepresenteerd.
De cockpit toont bovendien namen van falende, wachtende en onbesliste
checks **én de afzonderlijk opgehaalde klassieke statuscontexten** op
exact dezelfde HEAD. Een klassieke status `failure`/`error` verschijnt als
concreet herstelwerk; `pending` blijft wachten. Als de statuspagina vol is
(30 regels) blijft de dekking expliciet onvolledig. Beide bronnen blijven
alleen bewijsmomentopnames, nooit een `required checks`-inventaris.
Er staat een directe link naar de actuele GitHub-PR-checkpagina.
Als een PR tijdens het ophalen wijzigt, worden de gedeeltelijk
verzamelde checks/reviews niet als huidige head-resultaten weergegeven.
Check-runs plus maximaal 30 klassieke statuscontexten zijn nog steeds
niet gelijk aan alle door branch-protection vereiste checks; historische
contexten en een afgekapt statusvenster geven nooit zekerheid.
Review-paginering is niet compleet als de eerste pagina vol is.
Daarom verklaren zelfs groene checks en één approval **nooit**
`merge_authorized`, `deploy_authorized` of `owner_verified` waar.
De bevoegde PR-/runtime-eigenaar moet de huidige GitHub branch-protection,
required CI, onafhankelijke reviews, echte claim- en productiereleasepoort
zelf blijven respecteren.

Dit is een read-only operatorfunctie in dezelfde open [zCloud #1563](https://github.com/Zennay/zCloud/pull/1563).
De tool opent alleen bestaande GitHub GETs en lokale read-only SQLite;
geen nieuwe QA, runnerstart, CI retry, PR-merge, Notion of productie-write.

## Lopende workflows met wachtende jobs: P0-route niet meer overgeslagen (19 oktober)

GitHub Actions heeft **twee aparte statussen**: de workflow-run en elke
individuele job. Een workflow kan `in_progress` zijn terwijl een vereiste
self-hosted job nog `queued` is. Alleen workflow-status `queued` lezen
mist dat scenario. zSentinel doorzoekt daarom in vaste, begrensde volgorde
`queued` → `in_progress` → `failure` en selecteert een gedateerde,
run-ID/job-ID/SHA-gebonden job die in de steekproef werkelijk `queued`
is. De bestaande exacte jobverificatie en self-hosted-**labelvereiste**
blijven ongewijzigd. Zonder die labels is wachttijd nooit een bewezen
VPS-listenerstoring.

De collector onderzoekt beide wachtende vensters voordat hij kiest:
een bevestigde `self-hosted`-job gaat vóór een job waarvan de runnerlabels
onbekend zijn; die gaat weer vóór een expliciet hosted/custom wachtrij.
Binnen de categorie telt de oudste brongebonden startdatum. Dat is een
**leesadvies**, geen bewijs dat capaciteit opnieuw gealloceerd mag worden.
Een bronfout in één venster wist reeds geobserveerde, onafhankelijke exacte
jobidentiteiten niet; de gebruiker ziet `partial_window` en welke
workflowstatussen niet konden worden onderzocht. Projectdekking wordt
dan gedegradeerd, ook als een concrete job nog afzonderlijk verifieerbaar is.

De operatorcockpit vermeldt het workflowvenster waarop de selectie berust:
*Al lopende workflow, maar job nog wachtend* is niet hetzelfde als een
mislukte workflow. Buiten de begrensde eerste twee runs per venster en de
eerste tien jobs per run is het resultaat ONBEKEND; nul kandidaten is
geen bewijs dat de portfolio gezond is. Maximaal 9 discovery-GETs per
project en maximaal 42 gecombineerde GETs voor drie projecten inclusief
issue- en exact-job-bronnen. Geen achtergrondpolling, nieuwe QA,
runnerherstart, betaalde reruns of queue-claims.

## GitHub-job voltooid zonder één stap: aparte menselijke probleemroute (19 oktober)

Een exact door GitHub teruggelezen job kan als `completed/failure` eindigen
terwijl de job nooit een runnerstap uitvoerde. Dat is **niet hetzelfde**
als een mislukte bronbuild. De GitHub REST-job levert daarbij soms letterlijk
`"steps": null`, niet `[]`. De bestaande collector herkende alleen de
lege lijst, zodat operators zulke echte platformadmissiegevallen enkel als
gewone CI-bronfout zagen.

`zsentinel_recovery_refresh.py` bewaart nu voor een **expliciet aanwezige**
`steps: null` of `steps: []` exact `no_job_steps=true`, uitsluitend uit
de pin `repository + run_id + job_id + head_sha`. Bij een gefaalde of
door een platform afgebroken **afgeronde** job zonder toegewezen runner
wordt `verified_failure_category=job_unstarted` gezet. Als de steps-property
ontbreekt, blijft deze bron **ONBEKEND**; ontbrekende velden mogen geen
incident of automatische handeling veroorzaken.

De bestaande router onderscheidt dan `github_job_unstarted` (P1,
menselijke check vereist) van een bron-/testfout en van P0
`github_hosted_billing_admission`. Alleen die laatste vereist apart
geverifieerde billing-aanwijzingen; `steps:null` alleen is **nooit**
een bewijs van betalingsweigering, approval-wachtstand of VPS-listeneruitval.
De operatorcockpit toont daarom letterlijk *GitHub-job niet gestart ·
oorzaak nog onbekend* met een gerichte actie om exacte GitHub
check-run/annotaties en accounttoestemming door de bevoegde eigenaar te
laten controleren. Geen automatische retry, geld, runnerrestart,
sourceclaim of production write.

Deze wijziging ligt in dezelfde bestaande [PR #1563](https://github.com/Zennay/zCloud/pull/1563).
De oudere vaste CI-verwachting voor een ongelabelde queued runner is
**niet** aangepast: `queued` zonder bewezen self-hosted labels mag
nooit als offline VPS-listener P0 worden aangemerkt. Verplichte exact-head
CI, onafhankelijke review en geserialiseerde productie-release blijven
van kracht. De bronfix is geen bewijs van geïnstalleerde scheduler of
herstelde job.

## Open problemen eerlijk prioriteren, óók zonder P0/P1-label (19 oktober)

De bestaande read-only GitHub-issuebron kan óók open productissues zonder
prioriteitslabel of met expliciete P2-prioriteit waarnemen. Voorheen vielen
deze na het ophalen weg, ook als een P0/P1-venster leeg was. De huidige
operatorroute laat nu **eerst P0**, dan **P1**, vervolgens **P2** en tenslotte
**Unspecified** zien. Een ontbrekend label wordt **niet** tot P1 of P0
gepromoveerd. `qa_only=true` uit de canonieke bron blijft volledig
uitgesloten onder het product-deliverybesluit van 17 oktober.

Per prioriteitsgroep wisselt de router tussen de werkelijk waargenomen
projecten, zodat acht issues van één repo niet altijd een andere repo
verdringen. De bestaande maximaal acht samplekaarten blijven dezelfde
GitHub-read-only receipts hergebruiken: **nul aanvullende API-calls**, geen
GitHub claim/issue/PR-write, geen dispatch, geen tweede eigenaar of extra CI.
Een item verwijst eerst naar een bestaande open PR; zo niet, dan blijft
actuele pad-/owner-/Notion-leasetoets vereist. Een P2 of ongelabeld item
mag nooit als vrij uitvoerbaar of urgent incident worden geïnterpreteerd.

Het portfolio toont de begrensde aantallen **P0/P1** en
**P2/zonder label** apart; beide zijn steekproefondergrenzen (`≥N`).
Projecten zonder betrouwbare issuesbron tonen `ONBEKEND` en nul in een
succesvolle steekproef betekent niet dat een repo issuevrij of gezond is.
De JSON handhaaft `dispatch_authorized=false` en
`queue_owner_authorized=false`; dezelfde exacte-head security/CI-review en
seriële runtime-ownerpoorten als in PR #1563 blijven verplicht.

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

## Volgende problemen · gekoppelde P0/P1-werkkeuze (19 oktober)

De volledige portfolio-CLI integreert nu `scripts/zsentinel_problem_picker.py`.
Dit is een **echte, read-only prioriteringslaag** op de al opgehaalde
GitHub-P0/P1-issue-inbox en dezelfde portfolio-/Notion-status; geen extra
GitHub-verzoeken, polling, duplicaat-controller, databasewrites of CI-retries.

- Iedere geselecteerde projectrepo krijgt eerlijke rotatie binnen de prioriteit:
  alle gevonden P0 eerst, daarna P1, maximaal acht issuekandidaten.
- Als een bestaand open PR naar het issue verwijst, adviseert de kiezer
  expliciet **eerst die PR controleren** in plaats van een tweede branch te maken.
  Een verwijzing is geen bewijs dat de PR de bug oplost, actief eigendom heeft
  of door de gates is gekomen.
- Bij actieve projectstatus `claimed/running/verifying` in SQLite of Notion
  wordt eerst eigenaar-/padcontrole geadviseerd. Ontbrekende scopes zijn
  **nooit** impliciet vrij. Alle kandidaten blijven
  `ownership_verified=false`, `dispatch_authorized=false`.
- De expliciet bekende, afzonderlijk eigenaargebonden zCloud P0's
  [runner #1326](https://github.com/Zennay/zCloud/issues/1326) en
  [GitHub billing #1523](https://github.com/Zennay/zCloud/issues/1523)
  worden apart als infrastructuurblokkades getoond en verdringen geen
  onafhankelijke productproblemen. Andere brongebonden human-action
  CI-/runtime-incidenten staan in een eigen paneel.
- Wanneer GitHub-bronnen verouderd of incompleet zijn, vermeldt het overzicht
  de beperking. Een leeg venster is **geen** bewijs dat er geen open problemen
  bestaan; niet-geobserveerde repositories krijgen nooit een groene status.

```bash
# De nieuwe shortlist verschijnt in HTML, JSON én tekst (binnen één command):
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 --format text

# Machine-readable: veld next_problem_shortlist, inclusief classificatie en
# bestaande PR-verwijzingen. Dit is NIET een opdracht voor de scheduler.
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 --window 0 --format json \
  > /private/operator-only/portfolio.json
```

Automatische dispatch kan pas in een **apart bevoegde scheduler-/worker-write
integratie** nadat live file scopes, claims, ownership epochs, PR-conflicten,
release-, CI-, resource- en securitygates voor een exact werkitem opnieuw
zijn geverifieerd. De nieuwe shortlist maakt werkkeuze beter, maar schrijft
geen queue-rijen of claims, neemt geen eigendom over en kan de VPS
niet herstarten. Broncode in PR #1563 is nog geen geactiveerde productie.

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

 Use a private, existing operator-owned output directory and existing readable DB file, without changing VPS services. `--project` is repeated up to three times, must exist in canonical `projects.json`. Optional `--job` is limited to one pinned existing run/job/commit per project; GitHub details must match exactly. The issue collector is read-only, credential-safe, no-redirect, capped at three API calls per selected project. The CI discovery reader uses up to nine GitHub GETs per project (three status-filtered workflow windows: `queued`, `in_progress`, `failure`; each has one bounded run-list GET plus at most two bounded job-list GETs). The exact pinned-job collector uses up to two additional GETs per pinned or discovered job. The default combined bound for three projects is at most 42 GitHub GETs per invocation (27 discovery + 9 issue/PR + 6 exact-job reads), with no background polling. A workflow may already be `in_progress` while one of its jobs is still `queued`: the previous two-window lookup silently missed this high-impact runner scenario. All three windows are only sampled, never an all-clear. Credentials are never printed. The command does **not poll, daemonize, create a watch notification, or dispatch GitHub Actions**.

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


## 19 oktober · Opeenvolgende portefeuilledekking zonder extra API-burst

De read-only portefeuille-operator kan nu naast de standaard UTC-uurrotatie een **privé venstercursor** gebruiken. Hiermee bezoekt elke opeenvolgende, volledig gerenderde operatorrun het volgende groepje van maximaal drie canonieke repositories — ook wanneer meerdere handmatige starts binnen hetzelfde uur plaatsvinden. Eén run doet nooit méér GitHub-verzoeken dan voorheen en een niet-geobserveerd project blijft expliciet onbekend.

```bash
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --window auto --window-state /secure/zsentinel-windows.json \
  --format html > /secure/portfolio.html
```

- De optionele lokale cursor bevat enkel registry-fingerprint, volgende vensterindex, de vorige poging en per canoniek project maximaal één laatst geobserveerd pogingstijdstip plus de **partiële** GitHub-bronstatus; nooit tokens, ruwe queue IDs, claims of API-antwoorden. Schrijfacties zijn uitsluitend in dit expliciet gekozen privébestand; de SQLite-database blijft mode=ro.
- HTML-, JSON- en tekstoverzichten tonen per project de **laatste bronpoging** met leeftijd, dekkingstype en onderscheid huidige run/historische bron. Historische pogingen zijn geen live bron, tellen niet mee als huidige dekking en bewijzen geen herstel, CI-succes of vrij eigendom. Projecten zonder eerdere cursorpoging blijven `ONBEKEND`.
- De private projecthistorie blijft begrensd tot 32 canonieke projecten (4 KiB bestand) en wordt bij een veranderde registry weggegooid; een ongeldige of toekomstige historische tijd wordt niet geaccepteerd.
- Bij een gewijzigde canonieke projectregistry begint de cyclus opnieuw op venster nul; oude dekking wordt **niet** overgenomen of als gezond voorgesteld. Ongeldige of gemanipuleerde cursorinhoud wordt geweigerd.
- Cursorverplaatsing gebeurt pas na bronvalidatie en rapport-rendering. Een afzonderlijke, persistente lokale **`.lock` met exclusieve niet-blokkerende `flock`** beschermt nu de volledige lees-/GitHub-/render-/schrijfcyclus: gelijktijdige tweede starts met dezelfde cursor weigeren expliciet, ook als de cursor via atomaire `os.replace` een nieuwe inode krijgt. De lock is een eigen regulier bestand met modus 0600 in de reeds bestaande privémap; onveilige links, groepsrechten en vreemde eigenaar worden geweigerd. Na beëindiging wordt de OS-lock vrijgegeven; het lege lockbestand blijft liggen voor veilige volgende runs. De cursor is géén scheduler, geen workerlease, geen productclaim en geen eigendomsbewijs. Opeenvolgende pogingen betekenen niet dat elk project aantoonbaar gezond is.
- Een expliciet `--window N` blijft beschikbaar **zonder** cursor; combinatie met `--window-state` wordt geweigerd. `--state` is de **afzonderlijke** optionele incident-ledger en mag niet naar hetzelfde pad verwijzen.
- De CLI schrijft geen productie-queue, restarts, GH workflow dispatches of reviewergoedkeuringen en activeert geen achtergrondtaak. Automatische uitvoering vereist nog steeds expliciete toestemming van de scheduler-/release-eigenaar.

## 19 oktober · Handmatig kalenderoverzicht voor uitsluitend menselijke blokkades

De bestaande `watch_alert_candidates`-bron heeft nu een bruikbaar **privé iCalendar-exportpad**. De gebruiker kan P0/P1-escalaties met vereiste menselijke eigenaaractie bewust in een agenda importeren zonder runner-, betalings-, Notion-, worker- of releasebevoegdheid aan de scanner te geven.

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --format ics > /secure/zsentinel-human-only.ics

# Of, met de al bestaande private vensterrotatie:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --window-state /secure/zsentinel-windows.json \
  --format ics > /secure/zsentinel-human-only.ics
```

Ook de reeds bestaande offline bronrouter (`scripts/zsentinel_recovery_router.py /secure/bundle.json --format ics`) en exacte GitHub-receiptververser (`scripts/zsentinel_recovery_refresh.py /secure/bundle.json --registry projects.json --format ics`) ondersteunen precies dezelfde canonieke privacy- en identiteitsgrenzen; er is **één** agenda-implementatie, geen afzonderlijk alertmechanisme.

Het ICS-bestand is een **lokaal bestand**: geen push, geen automatische kalenderkoppeling, geen verzonden notificatie, geen nieuwe scheduler. Per huidige begrensde bronrun zijn er maximaal vijf afspraken. Ze worden uitsluitend afgeleid van dezelfde canonieke incident-ID, actie, severity en `human_escalation=true` die ook in het oorspronkelijke routerbesluit staan. Onjuiste, dubbele of ongeautoriseerde notificatieclaims leiden tot weigering in plaats van willekeurige afspraken. Elk item heeft een stabiele private UID, UTC-beoordelingsdeadline en een **optioneel door de ontvangende agenda ondersteund** 15-minuten-voorafsignaal. Na import kan een agenda-aanbieder de privéactiegegevens verwerken; deel het bestand niet onbedoeld. Een lege source sample produceert geen fictieve afspraken en betekent niet dat alle projecten probleemvrij zijn. Verstreken reviewdeadlines worden niet stilzwijgend verlengd of op een andere datum gezet. Project-/run-/CI-dekking, betaalautorisatie, eigenaarschap en productieherstel blijven onbewezen waar de bron dat niet bevestigt.


## 19 oktober · Privé incidentenbalie · brongebonden menselijke opvolging

De bestaande zSentinel-router kan nu een **volledig lokale interactieve incidentenbalie** maken. De ingang gebruikt een werkelijk routerbesluit, dus er worden géén extra GitHub-, Notion-, VPS- of queue-vragen gestart. Een incident blijft brongebonden aan `incident_id`, project, ernst, eerste/laatste waarneming, oorspronkelijke beoordelingsdeadline en feitelijke routerstatus. De balie sorteert P0 vóór P1 en maakt het verschil tussen `human_action_required` en `read_only_diagnosis_required` zichtbaar.

```sh
# Gebruik bestaande echte bron/queue/GitHub-readers en schrijf alleen een privé offline HTML.
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --project cloud --format desk \
  --desk-output /secure/zsentinel-incident-desk.html

# Zelfde product is ook beschikbaar via:
#   zsentinel_portfolio_overview.py --database ... --format desk
#   zsentinel_recovery_router.py ./actual-observation.json --format desk
#   zsentinel_recovery_refresh.py ./actual-observation.json --registry projects.json --format desk
```

De operator kan zoeken op project/probleem/eigenaar, filteren op P0–P3 en expliciete menselijke noodzaak, en binnen de gekozen kaart precies **probleem / actie / bronstatus** bekijken. Een eigen opvolgstatus (nog niet beoordeeld, eigenaar benaderen, in behandeling bij eigenaar, extern geparkeerd, opnieuw verifiëren) en maximaal 800 tekens vrije notitie blijven *uitsluitend in het huidige tabblad*. Privéoverdrachten ondersteunen de volledige maximale incidentenset (200 bronincidenten) met een harde 1 MiB UTF-8 bestandsgrens voor export en beide importpaden, zonder notities stilzwijgend af te knippen tijdens dezelfde-bronexport.

**Verdere opvolging over meerdere runs:** naast een exact dezelfde-bronimport bestaat een afzonderlijke, expliciet bevestigde import van *oudere* privénotities. Alleen notities met identieke incident-ID worden gekopieerd; oude eigenaar-/herstelstatussen worden niet meegenomen en de lokale status wordt altijd **bron opnieuw verifiëren**. Meerdere regels in notities blijven behouden, ongeldige controltekens worden geweigerd. Een oude bron zonder overlap wordt geweigerd in plaats van een vals herstelbericht te maken.

**Bestandsveiligheid:** voor `--format desk` is verplicht `--desk-output /bestaande/privémap/nieuw.html`. Met `--format html` mag dezelfde `--desk-output` als **extra privébijlage** worden opgegeven: de normale volledige cockpit komt via stdout, en de privéincidentenbalie wordt uit precies **hetzelfde read-only bronbesluit** geschreven. Daarmee wordt een dubbele GitHub-/SQLite-/Notion-bronscan voorkomen; elke providerlimiet per run blijft intact. De tool creëert uitsluitend een **nieuw** mode-0600 bestand in een door de operator beheerde, niet groeps-/wereld-schrijfbare map. Bestaande bestanden en symlinks worden nooit overschreven; de HTML wordt volledig opgebouwd vóór de exclusieve write. Er wordt geen HTML naar de gewone stdout gelogd. De expliciete create-only file write is **uitsluitend het privé operatorbestand**, geen productie-/queue-/lease-/runnerwijziging.

**Voltooide privé-overdracht**: met één bewuste klik kan de operator een lokaal JSON-bestand downloaden dat alleen incident-ID, menselijke opvolgstatus en privénotitie bevat. De handoff is aan de SHA-256 van de volledig gevalideerde incidententabel, de oorspronkelijke tijd en het incidentenaantal gebonden. Een later geïmporteerde handoff wordt volledig geweigerd als bronhash, datum, aantal, statuswaarden of incident-ID niet exact overeenkomen; geldige import vervangt de notities in één keer. Er is geen autosave, achtergrondnetwork, externe HTML, lokale browseropslag, push of Notion-/GitHub-write. Privénotities niet delen als publiek issuecommentaar.

**Grenzen:** de ingebedde bron heeft altijd partiële dekking; weggevallen incidenten of lege filters bewijzen nooit dat CI, eigenaar, runner of project hersteld is. Lokale keuzes autoriseren geen claim, slot/release, betaling, automatische CI-retry, worker dispatch, productie deploy, of het afsluiten van een echt incident. De gegenereerde HTML verklaart ouder dan tien minuten expliciet op basis van de browserklok en meldt klokafwijking. Er is geen nieuwe QA-/regressie-/testcode geschreven of handmatig gedraaid. De reeds verplichte exacte-head CI en onafhankelijke review blijven volledig gelden.

### Eén bronlezing, twee operatorweergaven

```sh
# Vóór de run: /secure bestaat, is eigenaar-beheerd en niet groeps/world-writable.
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/zcloud.sqlite3 \
  --window-state /secure/source-windows.json \
  --format html --desk-output /secure/incident-desk.html \
  > /secure/portfolio-cockpit.html
```

De overzichtspagina en privéincidentenbalie delen dezelfde gesamplede GitHub-/SQLite-/optionele Notion-momentopname. Er wordt in deze combinatie maar één intern `assemble(...)` en één `plan(...)` gedaan; alleen de lokale render gebeurt twee keer. De HTML-baliebijlage is create-only 0600, de stdout-overzichtspagina volgt het gewone operator-redirectbeleid. Voor de balie alleen: `--format desk --desk-output ...` (geen HTML op stdout). Beide opties zijn ook beschikbaar in de afzonderlijke live-, evidence-router- en exacte-job-refresh entrypoints.

Een `--desk-output` dat naar hetzelfde bestand verwijst als private incidentledger of venstercursor wordt vóór de bronrequest geweigerd, inclusief padnormalisatie. Hetzelfde pad hergebruiken zonder verwijderen wordt ook geweigerd; maak een **nieuw** bestandsnaam per uitdraai. Er worden nooit extra GitHub workflow runs, runnerjobs, server-/queue-/lease-writes of automatische meldingen gestart.


## 19 oktober · Menselijke incidentoverdracht per eigenaar

De offline **zSentinel-incidentenbalie** toont nu ook een compacte, uitsluitend brongebonden groepering **wie moet actie ondernemen**. De operator kan specifiek op het bron-eigenaarlabel filteren, naast bestaande project-, prioriteit- en urgentievragen. Nieuw is de **reviewdoel-filter** (alle, reviewdoel verstreken, of verstreken/binnen twee uur) volgens de lokale browserklok. Per eigenaar toont de balie de geobserveerde P0/P1/verder geclassificeerde incidenten én het aantal verstreken beoordelingsdoelen, bovenaan gesorteerd op verstrijken en P0. Een verstreken doel is een **adviserende herbeoordelingsdatum**, niet een automatisch gemiste deadline, eigenaarsbevestiging, systeemstoring of bewezen herstel. Onbekende GitHub-bronnen blijven onbekend.

### Brongebonden privé Markdown-briefing

In dezelfde lokale HTML staat nu **Privé briefing per eigenaar**:

1. Filter naar de gewenste eigenaar/project/ernst. Alleen incidenten met `human_escalation=true` mogen in een briefing; een gewone CI-/runtime-diagnose is geen menselijke escalatie.
2. Kies **Voorvertoning maken** en beoordeel de volledige inhoud met SHA-256-bronidentiteit, bronmoment, bron-eigenaarlabel, afzonderlijke incident-ID's, oorspronkelijke probleem/actie/status, beoordelingsdoel en lokaal toegevoegde notities.
3. Kies daarna **Download deze briefing (.md)**. De download is gebonden aan exact dezelfde gefilterde incidenten, notities en bron. Elk gewijzigd filter (waaronder eigenaar en reviewdoel) of lokaal status-/notitieveld maakt de voorvertoning ongeldig, zodat onbedoelde oudere selectie niet wordt gedownload. De download wordt nooit automatisch verzonden.

De uitwisseling is expliciet *menselijk en privé*: bronadvies is geen bevoegdheid om CI te retriggeren, workerclaims over te nemen, betaling te autoriseren, incidenten te sluiten, een PR te mergen of het VPS-listenerproces te herstarten. Markdown-velden worden als gewone tekst ge-escaped; HTML- en Markdown-uitvoer leiden niet naar een GitHub/Notion/API-endpoint. De voorvertoning bevat mogelijk gevoelige persoonlijke operatornotities en moet vóór delen gecontroleerd worden. De briefing blijft begrensd tot maximaal 500.000 UTF-8-bytes per export, de al begrensde maximaal 200 bronincidenten en hoogstens 800 notitietekens per incident.

De oorspronkelijke privé JSON-handoff blijft beschikbaar voor latere bewerking. **Markdown-briefing is alleen bedoeld om te lezen en bewust over te dragen, nooit om automatisch terug te importeren of eigenaarschap uit af te leiden.** Bewaarde statuslabels blijven lokale operatornotities; de exacte-head release- en claimgrenzen staan los van deze rapportage.

## 19 oktober · Historische herstelachterstand blijft zichtbaar

De bestaande privéstaat in `zsentinel_recovery_router.py` bewaarde al een incident met de status `not_observed_needs_reconciliation` wanneer een volgend, bewust beperkt GitHub-bronvenster dezelfde job niet kon terugvinden. Dit werd voorheen **alleen als totaal** gemeld; het individuele P0/P1-incident verdween uit de zichtbare actieachterstand.

Nu bewaart de router voor ieder **nieuw bronbevestigd incident** alleen minimale `source_context` (canoniek project, incidentreden, P0/P1, historisch human-only label) naast de al bestaande exacte GitHub-run/job/SHA-identiteit. De `load_state`-toelating controleert deze velden en of ze de exacte jobidentiteit tegenspreken. Oudere privéledgers zonder broncontext blijven leesbaar: als er toch een **eerder gevalideerde exacte GitHub-run/job/SHA-bronidentiteit** bewaard is, kan die nog project en reden aantonen. Zonder die identiteit blijft project/oorzaak expliciet **onbekend**, in plaats van achteraf verzonnen. Geen enkele oude identiteit bewijst de huidige eigenaarsstatus.

De nieuwe `scripts/zsentinel_historical_backlog.py` toont maximaal **30 historische, nog niet gereconcilieerde bronincidenten** uit maximaal 200 ledgerrecords, eerst P0, dan historisch owner-only en langere afwezigheid. De volledige achterstand blijft zichtbaar als teller en afkapping wordt afzonderlijk gemeld. Elke kaart toont bronstatus `not_observed_needs_reconciliation`, laatste echte observatietijd, verstreken tijd, en waar beschikbaar het **exacte eerder geobserveerde GitHub run-/jobnummer met HEAD-SHA**. De enige veilige aanbeveling is de bron en eigenaar opnieuw te laten verifiëren.

**Productinterfaces:** de Nederlandse zSentinel herstelcockpit en de privéhuman-incidentenbalie tonen de nieuwe afzonderlijke sectie *HISTORISCH · GEEN ACTUELE BRON*. Ook de algemene portfoliotabel toont per canoniek project hoeveel nog niet gereconcilieerde incidenten uit maximaal 30 historische kaartjes in het **privéledgersample** zichtbaar zijn. Een project met zo'n rij krijgt als read-only advies *historische bron door eigenaar opnieuw verifiëren*, nooit automatisch vrijgeven. De CLI-tekst toont de volledige historische teller en waarschuwt wanneer de beperkte historische kaarten zijn afgekapt. Dit is niet hetzelfde als de actuele GitHub-incidentsample. De bestaande portfolio-HTML bevat automatisch dezelfde sectie omdat deze de gedeelde `zsentinel_recovery_view.html_view` insluit. CLI `--format json` voegt `historical_unresolved` toe, tekst-output toont bronidentiteiten en waarschuwing. Er zijn **geen nieuwe live watch-alerts, gefingeerde actuele incidenten, taakclaims, retries, releases of worker-autorisaties** gemaakt uit historische data. Een oud incident verlaat de onveranderde bron-incidentledger uitsluitend via de al bestaande exacte succesvolle jobreconciliatie of routeherclassificatie, niet omdat het deze run buiten beeld viel.

Gebruik bestaande `--state /secure/incidents.json` bij een geautoriseerde read-only operatorrun. Zonder dezelfde privéledger is er geen betrouwbare incidenthistorie: **historische teller nul betekent dan niet dat het project eerder gezond was**. Geen nieuwe geautomatiseerde QA, regressiesuites, workflow triggers of handmatige tests toegevoegd of gestart; bestaande eigenaar/CI/review/release-/toestemmingsgates blijven leidend.

### Optionele historische exacte GitHub-hercontrole (geen retry)

De reeds bestaande `scripts/zsentinel_recovery_refresh.py` kan nu op uitdrukkelijk operatorverzoek **`--recheck-historical-jobs`** ontvangen. Met een vertrouwde bestaande observatiebundle, canonieke registry en het *eigen* privéledger leest deze optie maximaal **drie verschillende historische exacte GitHub run-/job-/SHA-pins** uit de nog niet gereconcilieerde incidenten. Hij hergebruikt de bestaande `refresh()`-providerroute: per bronjob hoogstens **twee GET-verzoeken** (run en job), nooit een workflow-dispatch, rerun, merge of herstart. Actuele, al in de observatiebundle gepinde jobs worden **niet vervangen**. Het totale plafond blijft acht jobs per refresh-invocatie; indien alle acht plaatsen al bezet zijn, wordt de optionele hercontrole geweigerd.

```sh
python3 scripts/zsentinel_recovery_refresh.py ./existing-observation.json \
  --registry ./projects.json \
  --state /secure/zsentinel-incidents.json \
  --recheck-historical-jobs \
  --format html > /secure/zsentinel-exact-reconciliation.html
```

De vlag vereist het bestaande ledger; zonder exacte oude GitHub-bron, canoniek project of een vrije projectbronpositie volgt een expliciete afwijzing zonder GET-verzoeken of staatwijzigingen. De bestaande `plan()` voert uitsluitend de **bestaande** bronbewezen overgang `exact_job_completed_source_reconciled` uit wanneer dezelfde run, job en HEAD volgens een vers geverifieerde exacte GET echt succesvol zijn voltooid. Zelfs dat is **geen algeheel bewijs van runner- of workerherstel**. Een nog lopende/queued historische job blijft geblokkeerd, en een ontbrekende bron blijft onbekend. Bestaande private `--state`-JSON schrijft hoogstens het geautoriseerde deduplicatieledger bij; geen productiewachtrij of Notion-document.

De optionele selectie beperkt zich tot de eerste 30 brongebonden historische kaarten in de privéachterstand en maximaal drie vrije canonieke projecten; dat is een **bewuste partiële broncheck**, niet een uitputtende statuscontrole van alle 200 mogelijke ledgerincidenten. Oude eigenaren worden niet geclaimd of automatisch toegewezen. Geen QA-tests geschreven of handmatig uitgevoerd; verplichte bestaande CI en toestemmingseisen blijven in stand.

### Privé-incidentledger: geen verloren updates bij gelijktijdige operatorruns

Alle vier bestaande zSentinel CLI-ingangen (`zsentinel_recovery_router`,
`zsentinel_recovery_refresh`, `zsentinel_live_cockpit` en
`zsentinel_portfolio_overview`) gebruiken voor `--state` nu dezelfde
exclusieve **nonblocking OS-ledgerlock** op het stabiele naastgelegen
`<state>.lock`-bestand. De lock wordt **vóór lezen** genomen en pas na
volledige bronscan, renderen en atomisch privéledger-schrijven vrijgegeven.
Een tweede operator kan dus nooit tegelijk dezelfde historische
bron-incidentstate lezen en later de eerste update stilzwijgend overschrijven.
Een conflicterende run krijgt een directe foutmelding, zonder wachtrij,
retry of wachtlus.

De lock wordt create-only/owner-mode-0600 beschermd tegen symlinks,
hardlinks en vreemde inode-eigenaars. De operator moet een bestaande,
eigenaar-beheerde map gebruiken die niet groeps-/wereld-schrijfbaar is
(bijvoorbeeld `/home/ubuntu/.local/share/zsentinel/` mits die
geautoriseerd is). Een onveilige map of bestaande zwakke lock wordt
geweigerd, niet gerepareerd via elevated rights. De portfolio-CLI houdt
zijn afzonderlijke privécursorlock daarnaast gewoon vast.

Deze exclusiviteit geldt **alleen voor alle vier meewerkende lokale
zSentinel-operatoringangen met hetzelfde `--state`-pad**. Ze biedt
geen globaal transactie-/leasebewijs voor externe schrijvers, scheduler,
GitHub, Notion, de productiequeue of de VPS-runner; dat vergt bestaande
owner- en CI-gates. Zonder `--state` ontstaat ook geen persistent
historisch geheugen of ledgerlock. Geen QA-/regressiesuites toegevoegd
of handmatig uitgevoerd.

### Release boundary

The script is useful offline immediately after a source checkout. It is **not running autonomously in production** until an authorized integration owner connects a complete read-only source collector, controls private state storage, coordinates conflict-free live claim acquisition, and promotes the reviewed, exact-head CI-approved change through the existing serialized zCloud release gate. Self-hosted runner [P0 #1326](https://github.com/Zennay/zCloud/issues/1326) and private hosted account admission [#1523](https://github.com/Zennay/zCloud/issues/1523) remain separate human-controlled incidents; neither is fixed or bypassed by this script. No QA suite was created or manually executed for this change.


### Product-PR's eerst en echte hosted startweigering onderscheiden (19 oktober)

De bestaande PR-afrondingsdesk gebruikt nu de daadwerkelijke sortering van de
maximaal 20 laatst bijgewerkte PR's per expliciet gekozen project. Bij dezelfde
titel-P0/P1 krijgt het **recentst bijgewerkte** productwerk voorrang; een lager
PR-nummer is geen reden om ouder werk te blijven herkiezen. Een uit de bron
expliciet herkende QA-only titel (`test/qa/quality-validation`), branchprefix
(`qa/`, `test/`, `tests/`) of QA-only GitHub-label wordt niet automatisch
geselecteerd. Onbekende metadata verleent nooit eigendoms- of productbewijs.
Dit verandert geen bestaande expliciete `--pr PROJECT:NUMBER`-keuze of
GitHub issue-status.

De exact-head check-run-bron herkent nu tevens een **expliciet door GitHub
gemelde** hosted-admissionweigering ("job was not started" samen met een
betalings-/bestedingslimietmelding) in het bestaande check-run outputveld.
Wanneer de bron die boodschap niet bevat, wordt facturatie **niet** geraden
op grond van een algemene failure/statuscode. Om ook de afzonderlijke GitHub
check-annotations van hoogstens één falende exacte HEAD-check per geselecteerde
PR te raadplegen, is de optionele read-only opdracht beschikbaar:

```sh
python3 scripts/zsentinel_pr_delivery.py \
  --pr cloud:1563 --inspect-hosted-no-start --format md
```

Deze opt-in voegt maximaal **één extra GET per geselecteerde PR** toe
(maximaal drie extra GETs bovenop 5×3 basiscalls). Een 403/limietweigering,
ontbrekende exacte checkidentiteit of te grote/onbetrouwbare bron toont
onbekende annotatiedekking; de gewone PR-status blijft bruikbaar.
Berichtteksten, annotatiepaden, tokens en accountgegevens worden nooit
door de operator-export opgenomen. De herkenning biedt een **administratief
actiepad** alleen wanneer uit een volledige check-steekproef precies één
checkstartweigering blijkt en er geen andere falende klassieke status bestaat;
bij meerdere/ontbrekende checks blijft de generieke niet-groene CI-route
van toepassing. Geen automatische facturatie-aanpassing, retry, review,
workerclaim, release, branch-protectionbypass of productieactie.

De al bestaande verplichte `critical-runner-flows`-workflow kan op dezelfde
PR nog falen op historische asserties die onassigned jobs zonder geverifieerd
self-hosted label als runnerincident verwachten. De veilige productclassificatie
is bewust **niet** afgezwakt om oudere aannames passend te maken; onafhankelijke
CI-/review-eigenaren moeten de vereiste exacte-head gate afhandelen.

### 19 oktober 2026 — blocker-first werkbord, zonder claimovername

De reeds bestaande complete portfolio-CLI heeft nu één extra, **eerste**
operatorsectie boven de productissues: `1 · Eerst vastgelopen werk vrijmaken`.
De bron is **dezelfde reeds gemaakte** read-only `summary`, incidentbeslissing
en productissueshortlist; er zijn **nul aanvullende GitHub- of Notion-aanroepen**
en geen tweede queue-transactie nodig.

**Productgedrag:**

- Nieuwe module `scripts/zsentinel_unblock_board.py` bouwt maximaal 12
  herstelkaarten, apart voor een *actueel afgebakend P0/P1-incident*, een
  *alleen historisch nog ongereconcilieerd* incident en *echte SQLite
  claim-churn/verlopen-lease druk*. Historische en queueobservaties zijn
  **nooit** voldoende bewijs voor een actuele outage.
- Actieve P0's gaan voor P1's; historische ledgerhouders blijven zichtbaar
  als **eerst te verifiëren**, inclusief een eerlijke telling wanneer de
  30-historische-bronsteekproef of de canonieke registry niet alles toont.
  Geen afwezig-van-het-venster-is-herstel-aanname.
- Elke kaart heeft een expliciete volgende eigenaaractie,
  `worker_resumed_verified=false`, `permission_to_recover=false` en
  `owner_claim_verified=false`. De gehele uitvoer behoudt
  `claims_taken=false`, `retries_performed=false`,
  `deployment_authorized=false` en `writes_performed=false`.
- **Daarna onafhankelijk werk** toont maximaal vijf strikt geadmitteerde
  bestaand-taken-suggesties wanneer actuele owner-/pad- en resourcebronnen
  werkelijk compleet zijn. Anders laat het alleen begrensde echte
  open-GitHub-productissues voor **inspectie** zien, zonder dispatchrecht.
- Bestaande `--format html`, `--format json` en `--format text` gebruiken
  dezelfde bronvolgorde; JSON voegt `blocker_first_handoff` toe zonder
  bestaande velden te wijzigen. ICS en menselijke incidentdesk zijn
  ongewijzigd.
- Exact gebonden historische GitHub run-/job-/commitreceipts krijgen een
  **directe maar nadrukkelijk historische** GitHub-joblink voor bevoegde
  bronreconciliatie. De kaart beweert niet dat die job nog vastloopt.
  Onafhankelijke open productissue-kandidaten linken alleen naar het
  canonieke GitHub-repository van hun project; cross-repo verwisseling
  wordt geweigerd. De offline HTML-oplossing doet zelf geen netwerkverzoek.
- Ook **bestaande PR-afrondingsgates** kunnen nu vóór de issuebacklog worden
  getoond: bij optioneel reeds ingeschakelde `--inspect-pr`,
  `--inspect-referenced-prs` of `--discover-open-prs` toont het eerste
  werkbord een directe bestaande PR-link en de concrete brongebonden
  vervolgactie op dezelfde exacte-head check-/reviewsteekproef. Het
  vermeldt nadrukkelijk wanneer bron of required-check dekking onvolledig
  is. Een PR-verwijzing neemt nooit een actieve eigenaar over en opent
  geen tweede PR. De reeds bestaande opt-in GET-limieten blijven gelijk.

Dit productoverzicht vervangt **niet** PR #1565, waarin een aparte eigenaar
de echte `server.py` workerprompt voor blocker-first volgorde bouwt.
De huidige code routeert noch start feitelijk een zCloud worker; voor de
lopende portfolioherstelrol blijven exacte file-level claims,
verplichte CI, onafhankelijke review, runner/billingrechten en serialized
productie-release leidend. Zonder verse bron kan de kaart **geen** complete
blockerinventaris claimen. Productbron en operatorhandleiding worden op de
bestaande, niet-gemergede PR #1563 geleverd, zonder nieuwe QA-/regressiesuites.

Directe operatorinvoer op geautoriseerd bestaand SQLite-pad:

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /path/to/existing/portfolio.sqlite \
  --format html > /private/path/zsentinel-blocker-first.html
```

De CLI maakt de database niet aan en opent deze `mode=ro`. De optionele
private `--state`-ledger gebruikt zijn bestaande exclusieve 0600 lock.
Rapporteer apart bronfix, hoofdbranchmerge, runtimebewijs en echte
workerhervatting; het ene volgt niet automatisch uit het andere.


### 19 oktober — GitHub-only noodmodus bij ontbrekende SQLite-wachtrij

Wanneer de lokale VPS `portfolio_queue.sqlite` ontbreekt, niet leesbaar is
of zoals aangetroffen slechts een leeg 0-byte bestand is, **open dat bestand
niet als bewijs dat er geen werk of blockers zijn**. De complete
`zsentinel_portfolio_overview.py` kan nu expliciet zonder database
starten:

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 --discover-open-prs \
  --format html > /private/operator/zsentinel-github-only.html
```

- `--github-only` is een bewuste operatorselectie, geen stille fallback na
  SQLite-falen. Laat in deze modus `--database` weg. Zonder de vlag blijft
  de bestaande `--database` verplicht. Een operator die de database per
  ongeluk verkeerd opgeeft krijgt een harde fout in plaats van een schijnbaar
  gezonde nulmeting.
- De producer retourneert `source=explicitly_unobserved_no_database`,
  `database_read_only=false`, `queue_inventory_complete=false`,
  `totals={}`, geen projectgroeptellingen en **geen verzonnen observatietijd**.
  De echte router ontvangt `queue_pressure=None` en meldt terecht
  `coverage=not_supplied`; de portfolio-rijen tonen `ONBEKEND`.
  Het eerste HTML-paneel waarschuwt expliciet dat queue-/worker-/claim-/lease
  en eigenaarstatus **niet** bekend zijn.
- GitHub wordt nog steeds slechts voor maximaal drie expliciete canonieke
  projecten in het gekozen venster bekeken en de bestaande API-begroting
  blijft intact. Optioneel Notion Claimed/Running toont alleen projectbrede
  ondergrenzen, geen file-level leasebewijs.
- De lokale `--observe-vps-capacity` optie is met `--github-only`
  incompatibel: zonder daadwerkelijk aangewezen database-mount is een
  schijnbaar geldige disk-/resource-admission onbetrouwbaar.
- Offline HTML/JSON/tekst en blocker-first PR-werkbord blijven bruikbaar,
  maar `claims_taken=false`, `worker_resumed_verified=false`,
  `deployment_authorized=false`. Geen jobdispatched, betaalde workflow,
  scheduler, SQLite-writer of productie-mutatie.

Dit is een **operationeel alternatief voor brongebrek**, niet het herstel
van de VPS-wachtrij of het daadwerkelijk vrijgeven van vastgelopen workers.
De bestaande source PR #1563 wacht nog op verplichte exacte-head CI,
onafhankelijke review en geserialiseerde releasegoedkeuring.


**Directe productuitvoering op verbonden zSSH / geïsoleerde source checkout
(19 oktober 2026, circa 20:08 UTC):** éénmalig de échte portfolio-CLI
aangeroepen met `--github-only --window 0 --skip-ci-discovery
--skip-github-issues --format text`. Exitcode **0**. Concrete uitvoer
`QUEUE: NIET WAARGENOMEN (--github-only)`, `queued=ONBEKEND`,
`owned=ONBEKEND`, `lease=ONBEKEND`, en geen ongefundeerde
worker-/herstelclaims. De commandovoering gebruikte geen bestaand
`portfolio_queue.sqlite` als bron en activeerde door de expliciete
`--skip-*` opties **geen** live GitHub CI-/issuebron. Dit bewijst alleen
dat de productingang zonder SQLite werkelijk werkt; het bewijst
geen actuele GitHub/P0-inventaris, runnerherstel of productiedeployment.
De tijdelijke geïsoleerde GitHub source checkout werd na uitvoer opgeruimd.


### Snelle single-project herstelcockpit zonder SQLite

Dezelfde expliciete `--github-only` modus is beschikbaar in de bestaande
`scripts/zsentinel_live_cockpit.py`. Een werkend mini-voorbeeld zonder
externe provideractie is:

```sh
python3 scripts/zsentinel_live_cockpit.py \
  --project cloud --github-only \
  --skip-ci-discovery --skip-github-issues --format text
```

Direct uitgevoerd vanaf de bestaande source-PR op de verbonden VPS
(19 oktober 2026): **exit 0**. De uitvoer gaf
`Read-only queue pressure: not_supplied`,
`Claim coverage incomplete: routing suppressed` en
`WORKER FILE-LEVEL CLAIMS: UNKNOWN`. Deze optionele skip-invocatie is
**geen** GitHub-job/issue- of workerherstelbewijs. De HTML-variant
geeft bovendien een opvallende waarschuwing dat de lokale queue, claims,
leases en workerstatus **niet gelezen** zijn. Wie een echt bestaand
SQLite-pad heeft, gebruikt de onveranderde `--database` route en
krijgt de oorspronkelijke éénmalige read-only inspectie.

Voor beide CLI-ingangen geldt: `--github-only` en `--database` mogen
niet tegelijk worden opgegeven. De vlag is nooit een stille databasefout-
fallback; geen fout wordt tot een gezonde nulstatus opgewaardeerd.


### 19 oktober — exacte P0-issues vastzetten, ook buiten de GitHub-steekproef

Het venster met de oudste en recentst bijgewerkte open issues is bewust
begrensd; een ouder P0-incident mag daarom niet verdwijnen uit de cockpit
en ook niet fictief als hersteld gelden. Beide zSentinel-operingangen hebben
nu dezelfde optionele **maximaal drie exacte GitHub issue GETs**:

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 --skip-ci-discovery \
  --pin-issue cloud:1326 --pin-issue cloud:1523 --format text

python3 scripts/zsentinel_live_cockpit.py \
  --project cloud --github-only --skip-ci-discovery \
  --pin-issue cloud:1326 --pin-issue cloud:1523 --format html
```

- Elke `--pin-issue PROJECT:NUMBER` moet een **uniek** canoniek
  project in het gekozen venster zijn. Ongeldige, herhaalde,
  buiten-venster of meer dan drie referenties falen **vóór** een GitHub
  netwerkverzoek. De functie is niet toegestaan met
  `--skip-github-issues`.
- Extra bron: exact `GET /repos/{owner}/{repo}/issues/{id}`, met
  geverifieerde URL, nummer, open/gesloten status en brontijd.
  Pull requests kunnen niet als issue worden ingelezen, QA-only werk
  blijft uitgesloten; assignees zijn GEEN workerclaims.
- Exact gepinde open P0's hebben voorrang bij de router die maximaal acht
  bestaande issue-kandidaten toont. Zichtbare eigenaar-only issues
  verschijnen **in de eerste blocker-sectie**, geen automatische
  `attempts` reset, workerclaim, betalingswijziging of runneractie.
- Bij `closed` meldt de bron uitsluitend dat het **issue** gesloten
  is; een runner, job of worker is daarmee niet bewezen hersteld.
  Bij 403/rate-limit, foutieve identiteit of brononbeschikbaarheid
  wordt **geen gesloten-status geconstrueerd**. HTML, tekst en JSON
  tonen de echte afgebakende `pinned_issue_receipts` / bronfoutstatus.
- `max_github_gets` telt deze maximaal drie optionele GETs mee.
  Alle bronantwoorden blijven read-only en bron/operationeel bewijs
  worden uitdrukkelijk onderscheiden.

**Werkelijke operatoruitvoering op de verbonden VPS via geïsoleerde
GitHub source-checkout:** CLI met twee gepinde cloud P0-issues en een
expliciet overgeslagen CI-discovery, `--format text`, **exitcode 0**.
De actuele GitHub-issuebron rapporteerde exact `cloud#1326: EXACT OPEN`
en `cloud#1523: EXACT OPEN`; beide stonden als afzonderlijke P0's
boven het reguliere P1-productwerk. Dit is bewijs van de actuele
**open issue-status** en de werking van de interface, niet van de
onderliggende VPS-/GitHub-billingstatus of herstelde workers.


### 19 oktober 2026 — canonieke lokale queue en geclaimde, gepauzeerde worker

**Broncorrectie:** de losse bestanden `portfolio_queue.sqlite` en
`portfolio_queue.db` in de VPS-checkout waren 0 bytes; dat zijn **niet**
de canonieke actuele wachtrij. De daadwerkelijke zCloud-control-plane
gebruikt `server.py: DB = ROOT / 'history.db'`. Op de verbonden VPS
bevat `history.db` de tabellen `portfolio_queue`, `runner_events`,
`runner_workers`, `ai_global_slots` en `runner_targets`. Gebruik
op die host dus expliciet die bestaande database als bron:

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /home/ubuntu/zennay-cloud/history.db \
  --worker-events-database /home/ubuntu/zennay-cloud/history.db \
  --window 0 --format html > /private/operator/zsentinel-live.html
```

De `--github-only` modus blijft bewust bestaan voor een host zonder
toegankelijk SQLite-bestand, maar mag **niet** als reden worden gebruikt
om een aanwezige canonieke queue tot `ONBEKEND` of nul weg te schrijven.
Zonder expliciete `--worker-events-database` worden workerclaims en
browservoortgang in de operatorsectie niet als bekend verondersteld.

**Nieuwe concrete productroute:** de add-only
`scripts/zsentinel_worker_progress_observer.py` leest rechtstreeks
`mode=ro` met `PRAGMA query_only=ON` en geïndexeerde
`runner_events` metadata. Het vergelijkt, binnen één afgebakende
SQLite-leestransactie, voor maximaal drie projecten en acht slots per
project: het laatste browser-event/progress-at, de gewenste status uit
`runner_workers`, de **werkelijke** globale toewijzing uit
`ai_global_slots` en de bijbehorende actuele
`portfolio_queue` status plus getoetste leasetijd.

De veilige blocker-first categorisering is:

- `desired_state=paused`, maar **geen** globale toewijzing of actuele
  queueclaim: informatief, **geen** op te heffen workerincident.
- `desired_state=running` maar **niet** toegewezen: oude configuratie;
  **geen** actief vastgelopen browserworkerbewijs.
- `desired_state=running`, daadwerkelijk globaal toegewezen, en
  een ontbrekende/verouderde gebeurtenis: read-only P1-kandidaat
  voor onafhankelijke eigenaar-/claimcontrole; geen auto-retry.
- `desired_state=paused` **én** werkelijk toegewezen globale slot
  **én** dezelfde huidige, niet verlopen `claimed/running/verifying`
  queuelease: **P0 eigenaarbeoordeling**. De controller mag niet zomaar
  de pauze opheffen, de queuelease vrijgeven of een worker overnemen.

Dezelfde bron is in `scripts/zsentinel_live_cockpit.py` én
`scripts/zsentinel_portfolio_overview.py` geïntegreerd.
Beide tonen brongebonden status in HTML/tekst/JSON, zonder raw queue-ID,
gesprek-URL, prompthistorie, foutbericht of clientreason door te geven.
Geen nieuwe QA- of regressiesuite gebouwd of handmatig gestart.

**Directe uitvoering via de verbonden zSSH op bestaande canonieke
`history.db` (19 oktober, rond 20:45 UTC):** de echte read-only
queue-observer retourneerde `active_items=68`, `queued_items=60`,
`owned_items=8`, `high_claim_attempt_items=24`,
`expired_owner_lease_rows=0` en `max_claim_attempts=14775`.
Die attemptteller is een *claimpogingenteller*, geen bewezen foutteller.
De complete product-CLI met live GitHub-issuebron en beide SQLite-bronnen
eindigde met exitcode **0**. Zichtbare P0:
`cloud` / global worker-slot **1**, `runner_workers.desired_state=paused`
terwijl dezelfde slot een actuele geldige **claimed** queuelease heeft.
Een eerder oud event alleen zou **geen** P0 zijn; het is de brongebonden
*pausestatus + toewijzing + queuelease* die deze afwijking maakt.
De P0-kaart vereist daarom expliciet een **menselijke runtime-/queue-eigenaarbeslissing** (`requires_human=true`, `next_gate=authorized_claim_pause_conflict_resolution`); de module kan deze vrijgave niet zelf autoriseren.
De daadwerkelijk geobserveerde FTMO-worker 1 toonde generatievoortgang,
maar werkafronding blijft **NIET** bewezen; oude niet-toegewezen FTMO-
slots 2/3 en Fuckball-slot 1 werden niet als actieve incidenten aangemerkt.

**Onafhankelijke eigenaaractie:** controleer het gewenste pauzebeleid,
de lopende exacte workerclaim, gesprekstoegang, en de reden voor eventuele
pauze, voordat de bevoegde runtime-eigenaar een hervatting of veilige
herroutering overweegt. Het rapport **neemt geen claim over**, wijzigt
geen `desired_state`, lease of database, en dispatcht geen CI/worker.
De andere externe P0's [#1326](https://github.com/Zennay/zCloud/issues/1326)
en [#1523](https://github.com/Zennay/zCloud/issues/1523) blijven
bevoegd-eigenaar-only. Exact-head verplichte CI, onafhankelijke review,
beschermde merge en geserialiseerde deploy voor PR #1563 blijven intact.


## Vergelijk twee opeenvolgende blockerborden (broncontinuïteit, zonder herstelclaim)

De bestaande one-shot portfolio-opdracht heeft `--format json` en levert de
`blocker_first_handoff` samen met de originele broninformatie. Sla twee
**verschillende** uitvoermomenten in nieuwe privébestanden op (zonder overwrite)
en vergelijk vervolgens rechtstreeks die canonieke JSON-bestanden:

```bash
python3 scripts/zsentinel_portfolio_overview.py --github-only --format json --window 0 --output /private/zsentinel-before.json
# Op een later, bewust gestart observatiemoment:
python3 scripts/zsentinel_portfolio_overview.py --github-only --format json --window 0 --output /private/zsentinel-after.json
python3 scripts/zsentinel_blocker_continuity.py \
  --before /private/zsentinel-before.json \
  --after /private/zsentinel-after.json \
  --format html --output /private/zsentinel-continuity.html
```

Gebruik een **eigen bestaande privémap** voor de voorbeeldpaden; de tool maakt
nooit een map, overschrijft nooit een outputbestand, en schrijft een expliciet
gevraagde nieuwe output met mode `0600`. `--format json` of `text` werkt
ook (zonder `--output` naar stdout). Er worden geen credentials, browsertekst
of ruwe queue-IDs uit eerdere source-bestanden toegevoegd.

Het vervolgscherm onderscheidt *nieuw waargenomen*, *nog waargenomen* en
*niet opnieuw gezien*. De laatste categorie is **geen afgesloten issue, geen
runnerherstel en geen hervatte worker**: elk portfolio-venster is bewust
begrensd, kan uitvallen en is niet noodzakelijk even vers als een ander venster.
Ook een verschoven exacte PR-HEAD of veranderde checkobservatie is alleen een
gewijzigde bronobservatie; het is geen voldane review/mergegate. Alle
bronmomenten/leases/claims moeten vóór een echte herstelactie opnieuw door
een bevoegde eigenaar worden geverifieerd.

De helper `scripts/zsentinel_blocker_continuity.py` is een zelfstandig, offline
productenoverzicht; de bestaande productiedienst, worker, runner, Notion,
GitHub en SQLite worden **niet aangeroepen of gewijzigd**. Geen achtergrondtaak,
scheduler, autoresume, notificatie of veronderstelde releaseautorisatie.


### In één nieuwe portfolio-run de vorige bron vergelijken

Dezelfde hoofdopdracht kan nu het eerdere JSON-bronmoment direct meenemen.
Dit is een normale **eenmalige operatoraanroep**, geen scheduler of herstelactie:

```bash
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --format html --window 0 \
  --compare-with /private/zsentinel-before.json \
  --output /private/zsentinel-with-continuity.html
```

De HTML bevat nu direct de continuïteit boven het actuele herstelbord;
`--format json` levert tevens `blocker_continuity` en
`--format text` een Nederlandstalige bronvergelijking.
Het bestaande zelfstandige `zsentinel_blocker_continuity.py` blijft beschikbaar
om twee reeds opgeslagen rapporten zonder nieuwe netwerkbronopvraag te
vergelijken, inclusief verandering in exacte PR HEAD en checkobservaties.
`--output` accepteert alleen nieuwe `.html`, `.json` en voor tekst `.md`
in een bestaande map; bronbestand, ledger en runtime-database mogen niet als
output gebruikt worden. Zonder expliciete output blijft de CLI stdout gebruiken.


### Herhaald terugkerende blockers over 3–8 bronmomenten

De broncontinuïteits-CLI is nu een *tijdlijn* in plaats van alleen een paarvergelijking.
Maximaal zeven chronologisch aangeleverde oude operator-JSON-rapporten en één
huidig rapport zijn toegestaan. Dit is geen nieuwe poller, notifier of autonome
worker: er worden uitsluitend reeds aanwezige bronmomentopnames verwerkt.

```bash
# Maak onderstaande rapporten op verschillende momenten met de hierboven
# beschreven eenmalige --github-only --format json --output procedure.
python3 scripts/zsentinel_blocker_continuity.py \
  --before /private/zsentinel-1.json \
  --before /private/zsentinel-2.json \
  --before /private/zsentinel-3.json \
  --after /private/zsentinel-4.json \
  --format html --output /private/zsentinel-history.html
```

De hoofdopdracht biedt precies dezelfde workflow, maar haalt de *huidige*
momentopname in dezelfde aanroep op:

```bash
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 --format html \
  --compare-with /private/zsentinel-1.json \
  --compare-with /private/zsentinel-2.json \
  --compare-with /private/zsentinel-3.json \
  --output /private/zsentinel-current-history.html
```

Resultaten tonen herhaald waargenomen P0/P1-incidenten, het aantal steekproeven
waarin dezelfde exacte blocker-id voorkomt, de meest recente aaneengesloten
observatiereeks, opnieuw verschijnen na een ontbrekende bron, source-bound
GitHub-links en de bestaande eigenaar/volgende controlepoort. Maximaal 96
unieke historische blockerkaarten worden als JSON opgenomen (maximaal 48 op het
compacte HTML-scherm); overschrijding wordt expliciet gemeld.

Een P0 die bij **drie opeenvolgende echte gerapporteerde bronmomenten**
nog zichtbaar is, wordt hoogstens **kandidaat voor menselijke beoordeling**.
Hiervoor zijn per rapport geldige, strikt stijgende UTC-rapporttijdstempels
vereist. Bij ontbreken ervan wordt géén herhaalde-P0-aandachtkandidaat geteld;
niet-chronologische of toekomstige bronmomenten worden geweigerd. Let op:
een rapporttijdstempel bewijst niet dat alle externe GitHub-/Notion-bronnen vers
waren. Andere projectvensters en onvolledige bronrechten blijven zichtbaar als
onzekerheid; nooit een herstelclaim. Er wordt niet automatisch gewaarschuwd,
geclaimd, herstart, gemerged, gedispatcht of gedeployed.

Bestaande historie-momentopnames blijven read-only. De zelfstandige CLI accepteert
alleen nieuwe exclusieve outputbestanden (mode 0600). De hoofdopdracht blijft
de bestaande private outputhelper en actieve ledger-/claimgrenzen respecteren.
