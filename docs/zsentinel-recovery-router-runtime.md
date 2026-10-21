
### 21 oktober · volledige routegeschiedenis over meerdere bestaande bronvensters

De bestaande `scripts/zsentinel_portfolio_overview.py --compare-with <pad>`
mag maximaal zeven chronologisch opgeslagen privéportfolio-JSON-bronnen
lezen. Eerder werd uitsluitend de laatste oude bron gebruikt voor
routeringsveranderingen. Vanaf deze uitbreiding toont HTML, tekst,
Markdown-`handoff` en JSON (`routing_history`) bij **ten minste twee
vorige rapporten** óók een brongebonden P0-eerst tijdlijn over alle
bronvensters. De normale laatste-paarvergelijking blijft behouden.

De tijdlijn onderscheidt `route_changed`,
`appeared_in_later_sample` en `not_reobserved`. De laatste
twee beschrijven **alleen de beperkte steekproef**; een taak kan
onzichtbaar zijn door een ander GitHub-/Notion-/taakvenster en is dus
niet automatisch nieuw, opgelost, vrijgegeven of overgenomen.
Overgangen zijn alleen bewezen tussen **opeenvolgende** bronmomenten
waarin de taak in beide voorkomt. Bij drie of meer **opeenvolgende
waarnemingen** met dezelfde HOLD-reden voegt zSentinel bovenaan een
`HERHAALDE HOLD`-eigenaaractie toe. Dit signaleert terugkerend vastgelopen
P0/P1-werk (maximaal twaalf zichtbare kandidaten) zonder te beweren dat
de taak tussendoor onafgebroken vastzat. Dezelfde herhaalde HOLDs zijn
beschikbaar in de read-only privé-CSV, met afzonderlijke kolommen voor
het eerste en laatste waargenomen bronvenster en het aantal
opeenvolgende waarnemingen (dus geen misleidend vensternummer). Herhaalde HOLD-kaarten en
gewone routewissels staan in HTML, Markdown en CSV **in één gezamenlijke
P0-eerst sorteervolgorde**, zodat een herhaalde P2-blokkade nooit boven
een nieuwe P0-bronwaarneming verschijnt. Alleen actuele, exacte eigenaar-/
lease-/gatebronnen kunnen daarna daadwerkelijk werk vrijgeven.
Alle opgeslagen bronmomenten moeten
strikt oplopen (maximaal 72 uur tussen buren), dezelfde eerder
geobserveerde opaque SQLite-queueidentiteit hebben en mogen geen
toekomstige broninformatie bevatten. Incomplete/oude/verschillende
bronnen leveren ONBEKEND, geen gefingeerde routewissels. De actuele
portfolio-opdracht eist een huidige routerbron jonger dan tien minuten.
Een historische offline herhaling krijgt altijd HISTORISCHE REPLAY.

Ook de zelfstandige `zsentinel_route_continuity.py` heeft nu optioneel
tot zes extra `--older <pad.json>` argumenten **in chronologische
volgorde**, vóór de verplichte `--previous` en `--current`:

```sh
python3 scripts/zsentinel_route_continuity.py \
  --older /eigenaar/prive/bron-1.json \
  --older /eigenaar/prive/bron-2.json \
  --previous /eigenaar/prive/bron-3.json \
  --current /eigenaar/prive/bron-4.json \
  --format html \
  --output /eigenaar/prive/herstelgeschiedenis.html
```

Met `--older` bevat JSON zowel de laatste-paarvergelijking
(`latest_pair`) als `routing_history`; de CSV is dan een P0-eerst
**tijdlijnexport** met bronvenster, route vóór/na, vervolgstap en
expliciete false autorisatievelden. HTML/Markdown voegen de
meer-venster eigenaarsectie toe. Bij slechts twee bronnen blijft de
oude outputstructuur behouden. Alle bronnen worden met de bestaande
owner-private read-only no-follow-inodecontrole geopend. De uitvoer
is uitsluitend een nieuwe mode-0600 create-only operatorfile;
geen actieve provider-, runner-, claim-, scheduler- of queuewrite.

### 21 oktober · veilige routeringsovergangen tussen operatorvensters

#### Zelfstandig offline vergelijken zonder live bron of runner

Bij onbereikbare CI/runner kan de bevoegde operator twee **reeds gemaakte**
privé portfolio-JSON-exports vergelijken, zonder nieuwe provider-, queue- of
SQLite-leesacties. De nieuwe stand-alone CLI heeft verplicht aparte, bestaande
`--previous` en `--current` bronpaden en een **nieuwe** `--output`-naam:

```sh
python3 scripts/zsentinel_route_continuity.py \
  --previous /eigenaar/prive/zsentinel-oud.json \
  --current /eigenaar/prive/zsentinel-nieuw.json \
  --format html \
  --output /eigenaar/prive/zsentinel-routing-vergelijking.html
```

`--format json` schrijft een nieuwe `.json`, `--format text` een nieuwe
`.md` en `--format csv` een nieuwe `.csv` met een importeerbare
**menselijke herstelwerklijst**. HTML, tekst en CSV gebruiken dezelfde strikt **P0-eerst** gesorteerde
eigenaaracties: binnen een prioriteit komen routewijzigingen, nieuwe
waarnemingen, verdwenen-niet-opgeloste en pas daarna ongewijzigde taken.
Ook de standalone offline HTML toont leesbare, mobielvriendelijke
drie-regelige kaarten zonder JavaScript of externe requests.

De CSV houdt P0/P1-voorstellen, wijzigingen,
niet meer waargenomen taken en bronvolledigheid uit elkaar. Iedere rij
bevat opnieuw expliciet `claim_authorized=false`,
`dispatch_authorized=false`, `merge_authorized=false` en
`task_released_verified=false`. Er komen geen bestandsscope,
claimtokens, geheime logregels of automatische GitHub- of Notion-mutaties in
de export. De CSV is informatief, niet een workerimport/uitvoeropdracht.

Beide portfolio-bronnen moeten bovendien met exact dezelfde **opaque
lokale SQLite-bronidentiteit** zijn geobserveerd om taaktransities met
elkaar te vergelijken. Bij ontbrekende identiteit, GitHub-only of
verschillende SQLite-bestanden krijgt de operator `queue_source_identity_unknown_or_changed`
met UNKNOWN, zonder kunstmatige taak- of herstelovergangen. Het digest zelf
blijft privé en wordt niet in menselijke HTML-, tekst- of CSV-rijen
uitgeschreven. Ook indien bestanden minder dan tien minuten oud zijn,
noemt de **standalone** offline vergelijking iedere getoonde overgang
HISTORISCHE REPLAY; zij heeft geen live claim- of eigenaarbewijs.

Bestaande `write_private_output` gebruikt directory-gebonden
create-only opslag en mode 0600; input/output en hun geresolveerde paden
moeten verschillend zijn. De reader weigert symlinks, ongeldige niet-reguliere
bronbestanden, duplicate JSON-keys en meer dan 6 MiB per input. Geen
standalone uitvoer gaat automatisch naar stdout of externe apps.

Het offline pad mag archiefdata vergelijken maar labelt oude huidige
snapshots expliciet **HISTORISCHE REPLAY**, zonder actuele routeerbaarheid
te suggereren. De normale portfolio `--compare-with` blijft strenger:
huidige bron ouder dan tien minuten geeft ONBEKEND. Ontbrekende snapshots,
afgekapt scopebewijs en verdwenen kandidaten mogen nooit als oplossing,
vrije claim, gesloten issue of geautoriseerde job worden behandeld.


Bestaande `scripts/zsentinel_portfolio_overview.py --compare-with <privé-vorige-portfolio.json>`
vergelijkt nu, naast bestaande blockercontinuïteit, de feitelijke brongebonden
routeringsstatus van dezelfde queue-taken. Nieuwe module
`scripts/zsentinel_route_continuity.py` hergebruikt de al veilig geopende
privé-JSON zonder extra GitHub-, Notion- of SQLite-aanroepen. Het
`routing_transition`-veld in portfolio JSON en dezelfde sectie in HTML/tekst
laten zien: nieuw waargenomen kandidaten, onveranderde kandidaten,
overgangen tussen `incident_dependency_hold`, `gate_hold`,
`shortlist_hold` en `advisory_suggestion`, plus kandidaten die niet opnieuw
voorkomen. De vorige snapshot moet chronologisch vóór de huidige liggen
en maximaal 72 uur oud zijn. De huidige routerbeslissing mag bovendien
niet ouder zijn dan tien minuten. Bij een oude of onvergelijkbare bron
blijft de overgang ONBEKEND, zonder nieuw werk of vrijgave te suggereren.

De vergelijking houdt de verschillende bronklokken van de lokale
SQLite-waarneming en de later samengestelde routerbeslissing uit elkaar.
Oude exports zonder routerbron blijven bruikbaar met expliciet ONBEKEND.
Totaalvelden en maximaal twaalf voorbeeldrijen worden begrensd en
source-/taakidentiteit, veldovergangen en permissievlaggen gecontroleerd.
Een verschijning als `advisory_suggestion` is **geen** nieuwe claim.
Verdwenen items kunnen door een ander GitHub-projectvenster, verouderde
bron of bounded voorbeeldlimiet ontbreken: de status heet uitdrukkelijk
`not_reobserved_not_resolved`; er wordt nooit automatische herstel- of
vrijgaveclaim aan ontleend.

De nieuwe bronovergangen zijn passieve operatorinformatie. Er is geen
nieuwe poll, test-/QA-run, queueclaim, retry, owner-/workflow-/CI-mutation
of release/deploy toegevoegd. De gebruikelijke exacte-head CI, onafhankelijke
review en huidige eigenaars- en mergegates blijven vereist.

### 21 oktober · eerlijke P0-eerst onafhankelijke werkselectie

De bestaande runtime-router selecteerde maximaal vijf veilige queue-kandidaten op
`(prioriteit, projectnaam, taak-ID)`. Eén druk P0-project kon zo alle vijf
plekken innemen en andere P0-projecten onzichtbaar maken. De brongebonden selectie
geeft nu **binnen elke prioriteitsklasse** ieder project één beurt voordat
een tweede kandidaat van hetzelfde project aan bod komt. Alle P0-kandidaten
blijven vóór P1/P2/P3; deze verdeling is geen opdrachttoewijzing.

Overlappende voorgestelde bestandsscope en kandidaten buiten de vijf
shortlistplekken verdwijnen niet langer stil: `routing_selection_holds` biedt
een begrensd privé-overzicht met `overlapping_suggested_scope` of
`shortlist_limit_reached`, totaaltelling en maximaal acht voorbeelden.
De bestaande CLI-tekst en offline HTML-cockpit tonen de reden, veilige
volgende operatoractie en HOLD-status. Geen bestandsnamen of claimtokens
worden in deze HOLD-kaarten gezet. Onvolledige/verouderde taak- of
claiminventaris geeft expliciet `incomplete_or_stale`, nooit een bewezen
lege werkvoorraad.

Deze verbetering geldt alleen voor kandidaten die de **bestaande** claim-,
dependency-, branch-, gate- en capaciteitsadmission al passeerden. Er is
geen claim/dispatch/retry/merge/deploy, automatische eigenaarovername,
nieuwe QA-suite of releasegate-uitzondering toegevoegd. Op main en live
alleen na de onafhankelijke PR-review, exacte-head verplichte CI en
bevoegde release door de huidige eigenaren.

### 21 oktober · inodezichtbaarheid in bestaande privé schijfwerkbank

De bestaande read-only workspace-hotspotscanner neemt nu bij hetzelfde
statvfs-moment ook inode-totalen, vrije inodes en percentages in de
private disk-receipt op. Standalone Markdown/HTML en de compacte
gefilterde eigenaar-HTML tonen inodebeschikbaarheid naast vrije bytes.
De compacte brondecoder valideert alle optionele inodetellingen en
weigert tegenstrijdige percentages. Historische receipts zonder inodeveld
blijven bruikbaar met expliciet ONBEKEND; zij kunnen geen inode-herstel,
veilige cleanup of toestemming voor nieuwe jobs bewijzen. Geen extra
recursieve scan of bestandswijziging is toegevoegd.

### 21 oktober · inode-druk voorkomt onveilige nieuwe self-hosted jobs

De opt-in zsentinel_vps_resource_observer.py leest voortaan tegelijk
met vrije bytes op dezelfde database-filesystemmount ook f_files en
f_favail via Linux statvfs. Private JSON toont inode_total,
inode_available, inode_unavailable_percent en inode_source.
Tegenstrijdige tellers worden geweigerd. De filesystemidentiteit komt uit één nonblocking O_NOFOLLOW geopend
regulier databasebestand, met fstat + fstatvfs op dezelfde fd.
Veranderend pad en symlinks worden geweigerd; er worden geen databasebytes
gelezen of SQLite-transacties gestart. Filesystems zonder
bruikbare inodetelling krijgen unsupported_or_unknown_not_zero;
onbekend betekent NOOIT vrije inodes.

De herstelrouter markeert minimaal 90% niet-beschikbare inodes als
overloaded, ook wanneer vrije schijfbytes hoog zijn. Dit blokkeert
uitsluitend nieuwe adviserende self-hosted werksuggesties, nooit
bestaande claims of trainingen. Oude snapshots zonder inodes zijn
unavailable; actuele onafhankelijke CPU/swap/disk-overbelasting
blijft in dat geval als overloaded zichtbaar. De bestaande eigenaaroverdracht maakt voor onvolledige
inodebronnen nu ook een P1-hermetingskaart aan; onbekende brondata
blijven niet stil achter het dashboard verborgen. De mobiele cockpit
vertaalt deze redenen. De private runtime-eigenaaroverdracht toont
de inode-druk als P1 VPS-capaciteit bij #1326. Geen cleanup,
serviceherstart, dispatch, queuewrite, deploy of releaseautorisatie.

# zSentinel · Runtime incident router (product-only)

## 20 oktober · GitHub CI zonder klassieke statuscontexten

De exacte-head PR-desk onderscheidt nu drie situaties die eerder voor dezelfde
"pending"-melding konden worden aangezien:

- **Moderne check-runs aanwezig, klassieke statussen leeg:** GitHub kan voor
  een lege klassieke statuslijst `state=pending` teruggeven. Dat is geen
  daadwerkelijk lopende klassieke status. De PR- en issue-cockpit, tekst en
  Markdown tonen daarom "geen klassieke statussen (GitHub API: pending zonder
  context)". Moderne checks en eventuele review worden afzonderlijk
  beoordeeld.
- **Geen moderne check-runs én geen klassieke statussen:** er is helemaal geen
  actueel CI-bewijs. De volgende stap wordt
  `wait_for_first_ci_receipt`; dit is **niet** klaar voor merge of
  automatische reviewvrijgave.
- **Minstens één bestaande klassieke status pending:** blijft een
  echte wachtende status, ook wanneer moderne check-runs voltooid zijn.

De feitelijke GitHub-status blijft in JSON onder
`legacy_status_contexts.combined_observed` behouden. De bron geeft apart
`implicit_pending_without_contexts` aan, zodat operatoren en vervolgtools
het onderscheid kunnen reconstrueren. Niets in deze wijziging verifieert
verplichte branch-protection-contexten, claims, reviewbeleid, merge of deploy.
Bestaande exacte-head CI blijft verplicht; er zijn geen suites toegevoegd of
handmatig gestart.

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

A task suggestion requires **separate fresh (within 5 minutes)** `claim_observed_at` and `task_observed_at` timestamps plus explicitly true `claim_inventory_complete` and `task_inventory_complete`. Missing or stale timestamps suppress all suggestions, even when GitHub jobs are newly refreshed. A task suggestion requires every positive field: `queued`, `eligible=true`, `qa_only=false`, `human_gate=false`, `active_owner=false`, an explicit bounded file scope and an explicit set of required gates. Claim inventory must be declared complete and every active claim must have a known scope. A missing/unknown active claim scope suppresses **all** suggestions. Independently eligible suggested tasks must also have **non-overlapping scopes with one another**: the highest-priority candidate wins a collision; the operator must refresh claims when choosing actual work. Overlapping ancestor/descendant paths are not routed. Dependencies are **strict 32-character incident identities**; duplicates or malformed IDs are rejected. A task with a dependency is eligible only when every exact ID is explicitly `exact_job_completed_source_reconciled` in the existing private incident ledger; a job that merely vanished from the bounded current window, a still-blocked incident, and a route-reclassified runner event do **not** satisfy it. Unresolved dependencies hold only their own queued tasks and appear as bounded, non-authorizing `incident_dependency_holds` in JSON, text and offline cockpit; tasks in unrelated free scopes may still be suggested.

**Dependency coverage is separate from the hold count.** The router emits `coverage=complete` only when the task and active-claim inventories are both explicitly complete, fresh (within five minutes), and all active claim scopes are known. Otherwise coverage is `incomplete_or_stale`: the observed hold count is not a total, zero does **not** mean there are no dependent tasks, and no dependency is cleared. The default cockpit, text output, and central HTML/Markdown/JSON owner handoff display **UNKNOWN** for incomplete or legacy decisions that lack this coverage field. An owner must recheck current source, file scope and claims before starting anything.

The machine decision also exposes `routing_inventory_complete` for the combined, freshness-checked task/claim gate. The legacy `claim_inventory_complete` field remains as an alias for compatibility; it must not be read as a claim-only source declaration. Text output names both task and claim coverage when routing is suppressed. The dependency report also carries bounded reason codes for incomplete declarations, missing/stale observation timestamps, and unknown active-claim file scopes. The default cockpit and central private HTML/Markdown handoff translate those codes into owner-readable actions without exposing incident identities. A complete hold count covers only otherwise-routable queued candidates; already claimed/running, ineligible, human-gated, or unscoped tasks are outside that candidate count. A runner outage prevents suggestions requiring `self_hosted`. Never infer a claim is free from a missing PR alone; proof of source completeness and fresh claims is the upstream operator/collector's responsibility.

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
Hiervoor zijn per rapport geldige, strikt stijgende UTC-rapporttijdstempels,
hetzelfde volledig herleidbare geselecteerde projectvenster en drie herhaalde
**vers-geclassificeerde bronincidenten** nodig. Louter drie keer dezelfde
historische incident-ledgerkaart of open infrastructuurissue zien is nooit
huidig runnerherstel of nieuwe P0-escalatie. Bij ontbrekende onderbouwing
wordt géén herhaalde-P0-aandachtkandidaat geteld;
niet-chronologische of toekomstige bronmomenten worden geweigerd. Let op:
een rapporttijdstempel bewijst niet dat alle externe GitHub-/Notion-bronnen vers
waren. Andere projectvensters en onvolledige bronrechten blijven zichtbaar als
onzekerheid; nooit een herstelclaim. Er wordt niet automatisch gewaarschuwd,
geclaimd, herstart, gemerged, gedispatcht of gedeployed.

Bestaande historie-momentopnames blijven read-only. De zelfstandige CLI accepteert
alleen nieuwe exclusieve outputbestanden (mode 0600). De hoofdopdracht blijft
de bestaande private outputhelper en actieve ledger-/claimgrenzen respecteren.


## Self-hosted zSentinel: expliciete eigenaarstart op vertrouwde main

[Dedicated workflow](../.github/workflows/zsentinel-vps-owner-reviewed.yml):
`runs-on: [self-hosted, zcloud, vps]`, **geen GitHub-hosted job** in deze
zSentinel-workflow. Werkt uitsluitend op de bestaande permanente
`zcloud-vps-1` runner op `vps-bb300bba`, niet op het actieve dev-checkout
of een PR-branch. Op de VPS is de echte bron
`/home/ubuntu/zennay-cloud/history.db`; de bestanden
`portfolio_queue.db` en `portfolio_queue.sqlite` waren op 19 oktober
beide leeg en mogen niet gebruikt worden als live queue. De echte
`history.db` bevat `portfolio_queue` en `runner_events`. Broninspectie
was `sqlite3 mode=ro` + `PRAGMA query_only`, zonder mutatie.

**GitHub-branchebescherming heeft een automatische hourly self-hosted trigger
voor nieuwe workflows expliciet afgekeurd.** In plaats van die vereiste
security gate te omzeilen is dit bewust een **owner-only
`workflow_dispatch` op exact current `main`**, met verplichte bevestiging
van de 40-karakter `confirm_sha`. Nooit `pull_request`, geen cron,
geen runtime command vanaf een ongereviewde PR en geen verborgen scheduler.
Een echte automatische uurtrigger zou apart door de bevoegde runtime-/policy-
eigenaar goedgekeurd moeten worden. Er is nu nog geen automatische
uuraudit ingesteld.

Na gewone merge met alle bestaande CI/reviewgates:

1. Open `Zennay/zCloud → Commits → main`; kopieer de **volledige actuele**
   commit-SHA (geen afgekorte 7/12-karakterversie).
2. Open `Zennay/zCloud → Actions → zSentinel owner-confirmed VPS read-only
   audit → Run workflow`; kies de branch **main**, vul exact de SHA in bij
   `confirm_sha` en kies `window=auto`.
3. Controleer in de uitgevoerde job `runner_name=zcloud-vps-1`,
   `runner_id > 0`, en het read-only zSentinel-operatoroverzicht. Een
   luisterende `Runner.Listener` of groene hosted CI is geen bewijs dat
   die nieuwe job al is uitgevoerd.

De workflow doet eerst een pinned checkout zonder persistente credentials
en **direct daarna** de bestaande
`python3 scripts/zcloud_vps_runner_guard.py --json` identiteitspoort.
Daarna verifieert hij repository, actor, runnernaam, hostname, exacte lokale
`GITHUB_SHA`, handmatig `confirm_sha` en actuele remote-main-SHA vóór
zSentinel-broncode wordt uitgevoerd. Ook de load-/geheugenpoort voorkomt
een extra run bij <1,5 GiB available RAM of load > 2,5×CPU-aantal. Hoge druk
leidt tot een zichtbare uitstelling, **niet** tot een runner-restart.

De read-only audit leest de queue en workerhistorie uit de bestaande
`history.db`, plus begrensde GitHub-issues/PR-checks uit de GitHub API
met `contents:read, actions:read, issues:read, pull-requests:read`.
De bronoutput wordt alleen met create-only `0600` tijdelijk in
`RUNNER_TEMP` opgeslagen en in dezelfde bronopdracht verwijderd via
een shell EXIT-cleanup; GitHub job summary bevat uitsluitend minimale
aantallen, geen queue-ID's, secrets of volledige workerdata.

De workflow doet **geen** queue-/SQLite-write, worker-claim/retry,
GitHub-dispatch, runner-restart, Notion-write of productie-deploy.
Actuele Notion ownership zonder expliciete bron blijft UNKNOWN.
De bestaande PR-review, exact-head CI, security/privacy-/releasegates,
P0 #1326 en accountbeheerder P0 #1523 blijven gescheiden.
Andere workflows met `ubuntu-latest` zijn hiermee **niet** omgeschakeld.


### Live runnerprocessen naast GitHub-jobtoewijzing

De bestaande operatoropdracht biedt nu
`--observe-local-runner`, naast `--observe-vps-capacity`. Hiermee leest
`scripts/zsentinel_local_runner_observer.py` op **`vps-bb300bba`**
alleen een begrensd `/proc/<pid>/cmdline` en `stat`-beeld. De bron telt
uitsluitend exacte executables onder de bekende
`/home/ubuntu/actions-runner-zcloud/bin/`-map; processen van FTMO/HaxLab
worden bewust niet als zCloud-werk aangemerkt. Gelezen argumenten, PID's,
tokens en process commandlines worden **niet** geëxporteerd. Een proces
dat tijdens de waarneming verdwijnt resulteert in zichtbare onvolledige
brondekking; geen stilzwijgende `idle`-conclusie.

Het momentrapport toont een werkelijk waargenomen listener, workerprocessen,
de beperkingen van de proc-inventaris en expliciete
`github_registration_verified=false`,
`github_job_assignment_verified=false`,
`safe_idle_authorized=false`,
`runner_restart_authorized=false`. Een listenerproces bewijst immers
nooit dat GitHub een job `runner_id>0` heeft toegekend.
Bij ontbrekende read-capability of afwijkende host faalt deze opt-in
waarneming gesloten; zonder vlag blijft het gedrag ongewijzigd.

De [owner-bevestigde VPS-workflow](../.github/workflows/zsentinel-vps-owner-reviewed.yml)
voegt `--observe-local-runner --observe-vps-capacity` aan dezelfde
read-only bronopdracht toe en plaatst uitsluitend aantallen en dekking in
GitHub Actions `GITHUB_STEP_SUMMARY`. Er wordt **niets** herstart, beëindigd,
geretryd, herverdeeld of gedeployed; de werkelijke GitHub-jobtoewijzing
moet onafhankelijk uit de exacte Actions jobreceipt blijken.

De geërfde regressie verwacht dat een >=30 minuten niet-toegewezen
`queued`-job als *operatorincident* zichtbaar is, ook bij onbekende
route. `scripts/zsentinel_diagnose.py` toont daarom nu wél de feitelijke
jobblokkade met oorzaak `github_job_unassigned`, maar met een **andere
deduplicatiesleutel voor route onbekend** en een verplicht
`niet concluderen dat VPS stuk is / niet herstarten`-advies. Een
`waiting`/`pending`-approval blijft een intentievolle wait, geen
runneruitval. Regressietests/QA zijn niet gewijzigd of handmatig gestart.


### Twee onafhankelijke signalen: lokaal proces versus GitHub-job

Een lokaal `Runner.Listener`-proces en een GitHub `runner_id` zijn **andere
soorten bewijs**. Bij een expliciet gepinde en exact opgehaalde
`run_id + job_id + head_sha` uit de bestaande GitHub collector noteert
zSentinel daarom apart
`canonical_zcloud_runner_assignment_observed` met de numerieke
`runner_id_observed`, en alleen `true` wanneer de GitHub Actions
jobgegevens een toegewezen `zcloud-vps-1` **én**
`runner_route=self_hosted` aangeven. De operatorkaart linkt rechtstreeks
naar die exacte GitHub-job. Een lokaal aanwezig listenerproces kan dit veld
nooit op `true` zetten.

De observatie is een read-only **moment-/historisch jobbewijs**, geen bewijs
dat de runner nú ingelogd, veilig idle, permanent gezond of geautoriseerd voor
een nieuwe productie-deploy is. Mislukte of onvoldoende metadata blijft
ONBEKEND, zonder restarts of onterechte activiteitsclaims. In een autoritatief
herstelrapport blijft `worker_resumed_verified=false` tenzij een bevoegde
runtime-owner daadwerkelijke herstart en nieuwe taakuitvoering onafhankelijk
bevestigt.


### 20 oktober · feitelijke jobtoewijzing en listenerproces zijn twee bronnen

De zSentinel-runnerdiagnose ondersteunt nu naast absolute uitvoerpaden ook
relatieve vormen zoals `./bin/Runner.Listener` en `bin/Runner.Worker`.
De lokale waarneming controleert de effectieve proceswerkmap tegen precies
`/home/ubuntu/actions-runner-zcloud/bin/`. Een even genoemde executable
onder de FTMO/HaxLab-installatie telt **niet** mee. Verdwenen of onleesbare
processen maken de bron onvolledig; `listenerprocessen=0` is geen
GitHub-status, geen veilig-idle-bevestiging en geen herstartbevoegdheid.

Voor exact geïdentificeerde GitHub Actions-jobs rapporteert de bestaande
read-only discovery/refresh nu afzonderlijk een **toewijzingsfase**:
`queued_without_runner_assignment`,
`terminal_without_runner_assignment`,
`no_runner_assignment_observed`,
`job_with_runner_assignment`,
`terminal_with_runner_assignment`, of
`runner_assignment_missing_from_source`. Een boolean runner-ID wordt
niet als numerieke ID geaccepteerd. De Nederlandse herstelkaart toont
de fase naast de jobroute, de runnerlabels en de exacte joblink.
Een self-hosted **onverdeelde** job geeft de bevoegde eigenaar alleen het
advies runnerregistratie, repositorytoegang en labels te controleren;
een reeds toegewezen job is geen bewezen gezonde listener of werkhervatting.

Het portfolio-risicobord classificeert een incident
`github_job_unassigned` nu als een **oningedeelde GitHub-job met nog te
bevestigen route**, niet automatisch als defecte zCloud-VPS-listener:
een ander GitHub-bronvenster kan anders een onterechte P0-oorsprong creëren.
Ook `completed` zonder runner is niet automatisch een billingconclusie.
Lokaal procesbewijs en GitHub-toewijzingsbewijs worden niet samen
gepromoveerd tot herstel, CI-succes, claim-/releaseautorisatie of safe idle.

Bron: de bestaande open [zSentinel PR #1563](https://github.com/Zennay/zCloud/pull/1563).
De command-line reports blijven one-shot en niet-destructief.
Geen QA/regressiesuites toegevoegd of handmatig gestart.
Verplichte exact-head CI, onafhankelijke review, beschermde
integratie- en productie-releasegates blijven nodig.


### 20 oktober · GitHub-bronweigering zonder portfolio-stilstand

Een geweigerde GitHub-repository blokkeert niet automatisch de andere
projecten. De collectors voor CI, productissues, exact gepinde P0/P1-issues
en bestaande PR-checks onderbreken herhaalde calls voor de betroffen
repository binnen dezelfde uitvoering. Onafhankelijke repositories behouden
hun eigen begrensde bronvenster. Reeds werkelijk waargenomen items worden
behouden, maar vormen nooit bewijs van een complete of actuele backlog.

HTTP 401 krijgt de categorie `authentication_required`. HTTP 429 of
een 403 met exact `X-RateLimit-Remaining: 0` wordt
`rate_limit_confirmed`. Andere 403-responses blijven
`forbidden_or_rate_limit_unknown`: geen automatische billing- of
runnerconclusie. Alleen een bevestigde provider-rate-limit onderbreekt
overige projecten in dezelfde collector. De historische
`github_permission_or_rate_limit`-fouttekst blijft beschikbaar voor
compatibiliteit; responsebody, headers, credentials en tokens worden niet
naar het operatoroverzicht gekopieerd.

Het offline dashboard toont bezochte en overgeslagen CI-vensters met
een veilige eigenaaractie. Het portfolio noemt 401 expliciet een
authenticatiepunt, een bevestigde limiet een reden om niet te blijven
retryen en vrij productwerk door te zetten, en een onduidelijke 403
een bevoegd repositoryonderzoek. Brondekking is geen toestemming
tot claim, workflowdispatch, merge, deploy of runnerherstart.

Wijzigingen zijn uitsluitend broncode op open PR #1563.
Er zijn geen nieuwe QA-/regressiesuites geschreven of handmatig gestart.
Exacte-head verplichte CI, zelfstandige review en release-ownership
blijven ongewijzigd.


### 20 oktober · Auth-gate in blocker-first productwerkbord

Een geobserveerde GitHub API **401** op een geselecteerde actuele
CI-bron wordt nu als brongebonden P1-kaart zichtbaar in
`scripts/zsentinel_unblock_board.py`. De kaart noemt uitsluitend de
bevoegde GitHub repository-/toegangseigenaar, geen systemd/runner,
betaling, workflowretry, workerclaim of release. Het is bewijs van een
**API-authenticatieprobleem bij die bron**, niet van een product- of
runnerstoring. De andere projecten blijven beschikbaar voor vrij
productwerk. Een onduidelijke 403 of bevestigde rate-limit krijgt
geen onterechte menselijke auth-claim: daarvoor gelden de eigen
gedegradeerde bronlabels en het portfolio-advies.

De offline blocker-continuity adapter bewaart de canonieke
`source-auth:<project>`-identiteit tussen twee expliciete momentopnames,
zonder een eerdere bronfout als opgelost te markeren wanneer een nieuwe
GitHub-steekproef onvolledig is. Er wordt niets automatisch verstuurd
naar horloge, account of externe service. De lokale operator kan eerst
bronbewijs en autorisatie controleren.

De zelfstandige productissue-inbox toont bovendien per repository
401/403/rate-limit-dekking en maakt niet langer de onjuiste claim dat
iedere 403 alle latere projectrequests onderbreekt.


### 20 oktober · oudere vastgelopen GitHub-jobs en gerichte human-only briefing

De read-only GitHub Actions discovery bekijkt per statusvenster eerst maximaal
vijf geretourneerde workflowruns en leest de meest recente plus de
verst terugliggende **geldige** run uit dat begrensde venster. Per
geselecteerde run leest de collector maximaal 25 jobs uit de eerste
jobpagina (voorheen 10). Daarmee kunnen langdurig wachtende
self-hosted jobs achter nieuwere workflows en meerdere eerdere jobs
beter gevonden worden **zonder méér GitHub API-calls**. Niet-uitgelezen
tussenliggende runs, oudere pagina's of jobs na de eerste 25 zijn
expliciet onvolledige bronobservaties, geen groene runnerdiagnose.
De CI-samenvatting behoudt concreet geobserveerde jobs bij gedeeltelijke
dekking en legt in de Nederlandse operatorinterface uit welk deel van
de steekproef bekend is.

Het blocker-first operatoroverzicht heeft nu ook een uitklapbare,
kopieerbare **privébriefing voor alleen noodzakelijke menselijke acties**.
Dezelfde briefing kan zonder extra HTTP-requests handmatig als Markdown
worden uitgeschreven:

```sh
umask 077
# Deze map moet vooraf bestaan en uitsluitend voor de operator leesbaar zijn.
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 \
  --format handoff \
  --output /private/operator-only/zsentinel-human-handoff.md
```

`--format handoff` hergebruikt de bestaande blockerkaarten uit **dezelfde
begrensde bronrun** en exporteert alleen geclassificeerde menselijke
eigenaaracties: geobserveerde human-only incidenten, bestaande open
owner-only P0/P1-issues, actuele API-authenticatiegates, een werkelijk
geobserveerd pauze-/leaseconflict of bestaande PR's waarvoor een
menselijke bron-, review- of releasebeslissing nodig is.
Gewone CI-pending, claimchurn, historische nog-niet-gereconcilieerde
incidenten en enkel stale workerevents worden niet ten onrechte
als urgente hulpvraag aangemerkt. De briefing vermeldt zowel de
deelsteekproef als de afkapping bij meer dan twaalf blockerkaarten.

Dit is een handmatige lokale uitvoer, **geen notificatie**, automatische
calendarwijziging, claimovername, merge-autoriteit of bewijs van herstel.
De standaard `--output`-writer weigert bestaande doelbestanden en
schrijft uitsluitend create-only `0600` Markdown; zonder outputparameter
gaat de tekst naar stdout. Geen verandering aan verplichte bestaande
CI, review, research, security en releasegates; nieuwe QA-suites niet gemaakt.


### 20 oktober · P0/P1-projectspreiding en menselijke escalaties zonder valse alarmen

Bij meer dan twaalf blockerkaarten bewaart het zSentinel-bord de prioriteit
P0 vóór P1 én de bestaande bronklassevolgorde. Binnen één identieke
prioriteit/klasse verdeelt het de zichtbare kaarten beurtelings over
projecten in plaats van een drukke repository alle twaalf plaatsen te
laten vullen. Het overige aantal blijft expliciet afgekapt; projectspreiding
bewijst geen opgelost werk of vrije claim.

De menselijke handoff heeft dezelfde bronkaartidentiteit als het offline
bord, zodat een eigenaar een verzoek tussen verschillende momentopnames
kan terugvinden. Alleen een daadwerkelijk noodzakelijke onafhankelijke
PR-review, gevraagde reviewcorrectie, bevoegde releasebeslissing of
vastgestelde account-billing-startweigering komt uit PR-data in de
menselijke briefing. Generiek PR-brononderzoek, ontbrekende CI, een
automatisch oplosbare codefout, een gewone draft-PR en historische
onzekerheid worden niet als onmiddellijke menselijke interventie
aangemerkt. P0/P1 owner-only issuegates en concrete auth-/leaseconflicten
behouden hun bestaande brongebonden plaats.


### 20 oktober · exacte bestaande GitHub-job buiten het automatische venster

Een bekend vastgelopen workflowjob hoeft niet meer te wachten totdat de
kleine GitHub-runs-steekproef hem automatisch vindt. De portfolio-CLI
accepteert dezelfde streng begrensde **exacte run/job/SHA-pin** als de
bestaande live cockpit, en toont dat bronbewijs in het volledige
blocker-first bord (maximaal drie unieke geselecteerde projecten):

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 \
  --job 'cloud:RUN_ID:JOB_ID:40_LOWERCASE_HEX_HEAD_SHA' \
  --format html \
  --output /private/operator-only/zsentinel-exact-job.html
```

Vervang de voorbeeldvelden uitsluitend door de daadwerkelijk op GitHub
waargenomen numerieke run-/job-ID's en de volledige 40-karakter lowercase
commit-SHA. Kies het portfolio-bronvenster waar `cloud` in staat,
anders wordt het bronverzoek vóór de GitHub-call geweigerd.
De input wordt verder via de bestaande `parse_jobs` en canonieke
`refresh` geverifieerd: repository, identieke run/job-koppeling,
HEAD-SHA, labelroute en runnerassignment. Geen job wordt opnieuw
gestart en er vindt geen claim of queue-write plaats.

Een exact succesvol gelezen pin telt nu mee als **beperkte**
GitHub-waarneming op de portfolio-rij, ook wanneer die job niet in de
automatische discovery-lijst voorkomt. De operatorpagina en tekstmodus
tonen hoeveel expliciete jobs zijn gepind. Andere, niet opgevraagde
CI-/reviewchecks blijven onbekend; zelfs een toegewezen runner-ID
bewijst geen actuele listenergezondheid, werkhervatting of releaserecht.


### 20 oktober · meerdere brongebonden GitHub-jobs per project

Een bronvenster kan meerdere geblokkeerde jobs tonen, terwijl de
herstelrouter slechts één exact geverifieerde job per project mag
behandelen. De GitHub-discovery bewaart daarom naast de primaire job
maximaal **vijf andere gevalideerde jobs** als afzonderlijke
read-only bronlinks. Deze selectie blijft binnen de bestaande
begrensde calls naar GitHub; geen extra workflows worden gestart
of opnieuw uitgevoerd. Elke referentie bevat alleen run-ID, job-ID,
volledige HEAD-SHA, jobstatus, runnerroute en GitHub-link.

Het operatoroverzicht laat de primaire én aanvullende joblinks zien
in één uitklapbare bronlijst. Bij elke job staat een exact kopieerbare
`--job PROJECT:RUN_ID:JOB_ID:HEAD_SHA` selector waarmee de operator
die bestaande job gericht opnieuw kan laten uitlezen via
`scripts/zsentinel_portfolio_overview.py`. De CLI accepteert
**maximaal één exact gepinde job per geselecteerd project per run**;
om meerdere jobs van hetzelfde project te inspecteren, kies één
andere pin per handmatige uitvoering. Links zijn gebonden aan de
canonieke projectrepository en numerieke GitHub-jobidentiteit.
De oorspronkelijke GitHub-jobdata wordt nooit gekopieerd als
shellcommando, token, jobstapinhoud of gebruikersinput.

Het portfolio-overzicht bevat nu de kolom **GitHub-jobs (steekproef)**:
minstens zoveel geldige jobrecords als werkelijk in het beperkte
venster zijn gevonden, en een afzonderlijke ondergrens voor
wachtende self-hosted jobs. Onuitgelezen jobpagina's, tussenliggende
workflows en extra jobs buiten de vijf zichtbaar getoonde links
blijven expliciet onbekend. Een rechtstreeks gepinde job telt
ook mee als beperkte bronwaarneming als de automatische
CI-discovery voor dat project niet is uitgevoerd.

**Bronfix:** `github_ci_source_stop_reason` wordt in het
portfolio voortaan uit `ci_discovery.coverage` gelezen, niet langer
uit het aparte `github_jobs.refreshed_projects`-object. Daardoor
worden GitHub API 401/403/rate-limit-/netwerkstops daadwerkelijk
zichtbaar in de portfolio-rij en ontvangt de eigenaar het juiste
vervolgadvies. Deze data is nooit een release- of herstelautorisatie.

Geen QA- of regressiesuites geschreven of uitgevoerd, geen runner
herstart, GitHub job retrigger, workerclaim, productiequeue-write,
Notion-claim of deployment uitgevoerd. De bestaande exact-head CI,
review, toestemming en releasegates blijven verplicht.


### 20 oktober · extra wachtende self-hosted jobs in blocker-first opvolging

De begrensde GitHub-bron kan bij hetzelfde project meerdere jobs met een
exacte self-hosted `runner_route`, een `queued`-status en zonder
toegewezen runner aantreffen. Eerder werd alleen de primair geselecteerde
job een blokkaderouteringskandidaat. De overige geldige bronjobs staan
nu afzonderlijk als **P1 read-only opvolgkaarten** op het blocker-first
overzicht, na geverifieerde incidenten en eigenaargebonden P0-gates.
De kaart is expliciet *nog niet exact herlezen*, dus **geen bewezen
lopende VPS-storing**, werkhervatting, actieve claim of herstarttoestemming.

Elke opvolgkaart bevat een rechtstreeks gevalideerde GitHub-job-URL en een
kopieerbare `--job PROJECT:RUN_ID:JOB_ID:HEAD_SHA`. De operator kan
precies die job via het bestaande read-only CLI-pad opnieuw ophalen.
Er gaat niets automatisch naar GitHub, Notion, SQLite of de runner.
De maximaal twaalf zichtbare blockerkaarten behouden P0 vóór P1,
bronklasseprioriteit en eerlijke projectsprijding. Andere aanvullende
jobs in de GitHub-pagina blijven in het uitklapbare bronoverzicht staan;
als de lijst afgekapt is, noemt het portfolio dit expliciet.

De offline blocker-continuity reader herkent de stabiele identiteit
`job:<project>:<run-id>:<job-id>`, valideert de herleeslink en
selectie-SHA, en vermeldt verdwijnen uit de volgende steekproef
uitdrukkelijk **niet** als herstel. Menselijke noodbriefings sluiten
deze nog niet exact herlezen read-only jobs bewust uit: broninspectie
is zelfstandig productwerk, geen verplichte mensactie.

## Browserworker-herstel op basis van een echte begrensde eventtijdlijn (20 oktober)

De zelfstandige read-only workerobserver en het reeds bestaande zSentinel
Live Cockpit `--worker-events-database` tonen nu, naast de huidige status,
een **maximaal twaalf recente geïndexeerde browser-events per worker**.
Alleen bestaande metadata uit `runner_events` worden gebruikt: een veilige
eventcategorie (onbekend → `other_event`), leeftijd in seconden, of het
event na de laatst waargenomen `prompt-sent` valt, en browser
`generating/sending` signalen. Geen gesprekken, prompts, fouten,
toegangstokens, payloads, URLs, queue-ID's of claimidentiteiten.

De volledige bediening van de reeds bestaande Live Cockpit blijft gelijk:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \
  --github-only \
  --project cloud \
  --worker-events-database /private/authorized/history.db \
  --format html > /private/authorized/worker-herstel.html
```

Het door de bevoegde operator opgegeven pad is een voorbeeld. Gebruik alleen
een bron waarvoor read-only toegang bestaat, zonder VPS-mutatie. De
workerbron gebruikt bestaande SQLite-indexen en een mode=ro/query_only
snapshot; er wordt geen aanvullende server-GET, browseractie,
queueclaim, leaseverlenging of scheduler-aanroep uitgevoerd.

De cockpit toont **eigenaarcontrole eerst**: een toegewezen gepauzeerde
worker met nog geldige queueclaim; daarna herhaalde recente
blokkadesignalen na de laatste prompt; daarna een enkele recente blokkade
en pas daarna verouderde/onvoldoende voortgang. Een uitklapbare tijdlijn
maakt zichtbaar welke events en leeftijden daadwerkelijk zijn waargenomen.
De `safe_followup` in JSON en de tekstversie adviseren alleen inspectie
en veilige werkcontinuïteit, geen automatische recovery.

**Grenzen:** de twaalf recente events vormen geen volledige eventhistorie.
Een latere gebeurtenis wist een eerdere fout niet, maar één oud
blokkeerevent bewijst evenmin een *actuele* storing. Browser-events zijn
niet cryptografisch of transactioneel aan een actuele queue-claimepoch
gebonden. `generation-finished` bewijst geen productcommit, en een geldig
leaseveld is **geen toestemming** om claims over te nemen. Daarom blijven
`owner_claim_verified=false`, `worker_resumed_verified=false`,
`repair_authorized=false` en `actions_performed=false` in alle uitvoer.
Deze read-only observatie **vervangt niet** de nog zelfstandige
control-plane implementatie van claimgebonden eventhistorie (#1342).
Voor alle werkelijke wijzigingen blijven actuele runtime-ownerclaims,
verplichte CI, onafhankelijke review en bestaande VPS/releasegates leidend.

### Actuele eigenaar-inbox, tekst en JSON

Het read-only `zsentinel_worker_progress_observer.py` bronresultaat
heeft nu een aanvullend `owner_followup_candidates`-venster
(maximaal 3 × 8 geselecteerde workers). Dat venster wordt op basis van
waargenomen status en het beperkte eventvenster geordend; het bevat
alleen project/worker-slot, reden, veilige vervolgrichting, aantal
recente blokkade-/voortgangssignalen en bestaande lease-indicatie.
De Live Cockpit toont deze inbox **boven de gedetailleerde
workereventkaarten**, en de tekstuitvoer zet dezelfde operatoracties
vooraan. Met `--format json` blijft dit machineleesbaar in
`worker_progress_observation.owner_followup_candidates`.

Alle voorgestelde acties zijn alleen instructies voor de bevoegde
eigenaar om de actuele claims, rechten, controlepanelen en echte
productvoortgang te inspecteren. Nergens wordt `repair_authorized`,
`recovery_authorized` of `continuation_verified` op `true` gezet.
Deze beperkte lijst is niet volledig en vervangt geen scheduler,
automatische recovery, owner review of echte claimgebonden
control-plane eventhistorie.

### Achterwaartse compatibiliteit van eerder opgeslagen bronmomenten

De nieuwe eventtijdlijn is een additieve uitbreiding van het bestaande
`zsentinel-worker-progress-observation-v1` schema. Een historisch
opgeslagen JSON-bronmoment zonder `timeline` of
`owner_followup_candidates` blijft daarom **leesbaar** in dezelfde
HTML-/tekstweergave, maar wordt zichtbaar als `HISTORISCHE BRON` getoond.
Het scherm claimt dan nooit "nul blokkades", "lege actuele owner-inbox"
of "werkhervatting bevestigd": voor de nieuwe velden blijft de status
ONBEKEND totdat de gebruiker bewust een verse read-only SQLite-observatie
maakt. Oude source-bestanden worden niet aangepast of herschreven.

### Een foutsignaal is niet eeuwig een actuele blokkade

De huidige bron had de status `recent_client_block_event_owner_review`
kunnen blijven afgeven nadat hetzelfde worker-slot alweer een
`generation-progress` of `generation-finished` event had gemeld. De
observer maakt dit onderscheid nu expliciet: wanneer de nieuwste
aan de laatste prompt gerelateerde browserblokkade gevolgd wordt door
een **jonger, recent generatie-event**, toont hij
`post_block_progress_signal_owner_verification_required`.
Het oude foutsignaal blijft zichtbaar in de afgekorte tijdlijn;
de actie wordt **controleer de werkcontinuïteit bij de eigenaar** en
niet "oude blokkade automatisch herstellen". Als er geen recent later
signaal bestaat blijft de originele blokkadeclassificatie intact.

Een browser `generation-finished` of `generation-progress` kan niet
bewijzen dat een GitHub-commit, codebuild, Notion-afronding, queuelease
of andere productoutput tot stand is gekomen. Deze nieuwe categorie
heeft daarom dezelfde read-only/human-review grenzen; hij neemt
geen enkele claim over en doet geen herstel- of continue-actie.

## 20 oktober · Bestaande Notion-connectorquery als privé snapshotbron

De eenmalige live-cockpit ondersteunt optioneel --notion-claims-export PRIVATE.json,
naast de bestaande token-gebaseerde Notion-read. Beide tegelijk is verboden.
Deze modus accepteert uitsluitend een door een operator **bewust lokaal
opgeslagen** respons uit de verbonden Portfolio Work Queue Notion query,
en rapporteert positieve actieve statussen als aanwijzingen, NIET als
echte claims, actuele lease of toestemming om werk over te nemen.

De operator maakt een privébestand met exact deze wrapper:
- schema: zsentinel-notion-connector-snapshot-v1
- observed_at_utc: de werkelijke, tijdzonebewuste UTC-verzameltijd
- data_source_id: 86e406fd-2c99-4ef5-8058-363c1004b3eb
- response: het ongewijzigde Notion-connectorresponseobject met
  results (maximaal 100 actieve rijen), has_more=false en
  data_source_ids=[dezelfde UUID]. Dit is GEEN bewering dat rijen
  ontbreken. Gebruik nooit een leeg voorbeeld als echte waarneming.

De bronrij bevat de originele Work ID, Status (Claimed/Running) en
Project-relatie uit de connector. Alleen bestaande canonieke project-URL's
uit projects.json worden naar projectnamen vertaald. Titels, medewerkers
en vrije tekst worden niet als eigenaar geïnterpreteerd of weergegeven.

Voorbeeld voor een privé-opgeslagen Notion query uit de connector:
    umask 077
    python3 scripts/zsentinel_live_cockpit.py --github-only \
      --project cloud --skip-ci-discovery --skip-github-issues \
      --notion-data-source 86e406fd-2c99-4ef5-8058-363c1004b3eb \
      --notion-claims-export /private/operator-only/notion-query.json \
      --format html > /private/operator-only/zsentinel-claims.html

De adapter accepteert alleen een reguliere bronfile zonder symlink met
maximaal 128 KiB, geldige UTF-8/JSON zonder dubbele keys, een exacte
data-sourceidentiteit, géén onvolledige paginering, maximaal 100 unieke
positieve Work IDs, uitsluitend actieve statussen en een echte UTC-datum
niet ouder dan tien minuten of meer dan 60 seconden in de toekomst.
Onbekende projectrelaties blijven ONBEKEND. Een ongeldige bron degradeert
alleen de optionele Notion-claimweergave; nooit worden fictieve nulclaims
of herstelverificatie gerapporteerd.

BELANGRIJK: een privé-JSON-bestand kan handmatig worden vervalst. De cockpit
merkt de bron aan als user-supplied / origin unverified / not live, toont
alleen canonieke projectlabels en Work IDs (geen titels, medewerker,
privétokens of onnodige relationele bronmetadata). Zelfs een positieve
waarneming bewijst geen huidige claimhouder, file-ownership, volledige
Notion-dekking, leasevrijgave, runnerherstel of dispatchrecht.

Er zijn geen Notion- of queue-writes, workeracties, CI-dispatches, 
productiemutaties, nieuwe QA/regressiesuites of omzeilde veiligheids- en
releasegates. Exact-head CI, reviewer, protected-main en P0-runner/billing
blijven verplicht vóór echte runtime-integratie.

### Ook in het bestaande productissue-werkbord

Dezelfde bewaarde snapshot is nu beschikbaar voor de zelfstandige
scripts/zsentinel_issue_delivery.py. Daarmee verschijnt het werkelijk
waargenomen positieve aantal actieve Notion-werkregels naast de actuele
GitHub-productissues en hun bestaande PR's. Geen project met 0 treffers
wordt daarom als vrij verklaard.

Voorbeeld (opgeslagen snapshot uit de verbonden query, NIET live):
    python3 scripts/zsentinel_issue_delivery.py --project cloud \
      --notion-data-source 86e406fd-2c99-4ef5-8058-363c1004b3eb \
      --notion-claims-export /private/operator-only/notion-query.json \
      --format html --output /private/operator-only/zsentinel-productbord.html

Het outputbestand blijft create-only en privé onder de bestaande
zSentinel-bestandsrechten. De optionele bron beïnvloedt uitsluitend
statusvermelding en menselijke eigenaarcontrole, nooit automatische
claimbesluiten, work-dispatch, CI, merge of deployment. Ook bij een
volledige opgeslagen connectorrespons retourneert de zelfstandige
issue-CLI geen volledige live-claimdekking en behoudt deze haar
degraded/onbevestigde bronstatus.

### Portfolio-overzicht: actief is positief, afwezig is ONBEKEND

Het portfolio-brede scripts/zsentinel_portfolio_overview.py ondersteunt nu
eveneens --notion-claims-export PRIVATE.json. De optionele snapshot wordt
alleen gebruikt voor positieve projecttellingen uit dezelfde afgesloten,
privé opgeslagen bron. Voor een project zonder waargenomen actief Work ID
wordt de menselijke actieve-statusondergrens expliciet ONBEKEND in plaats
van het vroegere misleidende getal nul. Dit geldt ook voor de bestaande
token-gebaseerde Notion-optie. Een ontbrekende telling verleent dus geen
nieuw werkpakket, filescopeclaim of dispatchrecht.

Voorbeeld voor het huidige canonieke projectvenster:
    python3 scripts/zsentinel_portfolio_overview.py --github-only \
      --window auto --skip-ci-discovery --skip-github-issues \
      --notion-data-source 86e406fd-2c99-4ef5-8058-363c1004b3eb \
      --notion-claims-export /private/operator-only/notion-query.json \
      --format text

Ook dit overzicht houdt echte Notion-eigenaars, actieve queueleases en
workerherstelstatus ONBEKEND. Geen workflowdispatch, bronwrite of runtime
owner-overname, en alle bestaande CI/review/releasegates blijven vereist.

### Boven de portfolio-tabel zichtbaar: het verschil tussen LIVE, PRIVÉ en ONBEKEND

De portfolio HTML-kop toont nu nadrukkelijk of Notion-werk is:
1. met de bestaande beperkte API-read geobserveerd (nog steeds geen file-owner);
2. uit een handmatig opgeslagen connectorbestand afkomstig, inclusief waarschuwing
   dat de snapshot NIET live en de herkomst NIET geauthenticeerd is;
3. geheel niet beschikbaar: geen nulclaim of vrijgavebewijs.

Dit onderscheid is ook zichtbaar wanneer een positief projectaantal in de
portfolio-tabel staat. Afwezige projecten blijven ONBEKEND; geen scheduler,
lease, worker, CI of productie-autorisatie volgt hieruit.

### 20 oktober · Bezet maar geen verifieerbare leaseklok

De éénmalige, alleen-lezen wachtrij-observer onderscheidt drie toestanden
voor `claimed` / `running` / `verifying`:
- **Lease aantoonbaar verstreken**: geldige UTC-vervaltijd op of vóór
  bronmoment; expliciet `expired_owner_lease_rows`.
- **Leaseklok niet verifieerbaar**: ontbrekende, lege, niet-UTC of anders
  ongeldige vervaltijd; aparte `unverifiable_owner_lease_rows`. Dit is
  géén bewijs van een verlopen lease, vrije claim of ongeldige eigenaar.
- **Geen waargenomen tijdsprobleem**: leesbare toekomstige vervaltijd;
  geen uitspraak over bestandseigendom of huidige worker-activiteit.

De onafhankelijke operatorproducten tonen nu de nieuwe toestand in
`zsentinel_recovery_router.py` (samenvatting/bronvalidatie),
`zsentinel_recovery_view.py` (gele waarschuwingskaart + tabel),
`zsentinel_portfolio_overview.py` (per project gescheiden teller en
volgende stap), en `zsentinel_issue_delivery.py` via de bestaande
`zsentinel_workboard_queue.py` bronadapter (HTML/Markdown werkbord).

Dit voorkomt dat een bezette rij met ontbrekende leaseklok en weinig
claimpogingen onzichtbaar blijft in de menselijke opvolging. Verlopen en
onverifieerbare leaseklokken zijn wederzijds uitsluitend; beide vereisen
**een geautoriseerde eigenaarcontrole**. De voortgangsrouter mag hierdoor
**niet** auto-retryen, vrijgeven, herclaimen, workers starten of
SQLite/Notion/runnerstatus muteren. Broninventaris volledig betekent nog
steeds niet dat eigendom of herstel geverifieerd is.

Geen QA/regressiesuites toegevoegd of handmatig gedraaid. Bestaande
exact-head CI, onafhankelijke review, protected-main, P0 runner #1326,
billing #1523 en serialized runtime release blijven bindend.

### 20 oktober · Schijfbezetting als harde veiligheidswaarschuwing

De optionele, alleen-lezen Linux-VPS-capaciteitsmeting toont voortaan behalve
vrije GiB op **de daadwerkelijke database-mount** ook het percentage blokken
dat niet beschikbaar is voor de normale runtimegebruiker. Dit gebruikt
`statvfs(f_bavail/f_blocks)`; een root-reserve of overige niet beschikbare
blokken worden niet als gratis worker-capaciteit gerekend. Ongeldige
filesystemcounters worden geweigerd.

De bestaande herstelrouter geeft nu `overloaded` terug zodra de
database-mount **minstens 90% niet beschikbaar** is, zelfs wanneer er meer
dan 2 GiB absoluut vrij blijft. Andere bestaande drempels voor CPU, RAM,
swap en vrije GiB blijven ongewijzigd. De Nederlandse HTML-cockpit toont
de bronoorzaak, bijvoorbeeld **Schijf minstens 90% niet beschikbaar** en
**OVERBELAST: GEEN NIEUW WERK**.

Oudere resourcebronnen zonder `disk_unavailable_percent` blijven
leesbaar, maar krijgen bij afwezigheid van andere overbelasting expliciet
**`state=unavailable`** en reason
`old_snapshot_disk_occupancy_unknown_not_release_authorization`.
Hiermee worden zelfs uitsluitend geadviseerde self-hosted werksuggesties
fail-closed achtergehouden totdat een actuele volledige meting beschikbaar
is; een oude snapshot vormt nooit bewijs van veilige startcapaciteit. De bron is opt-in, lokaal, kort geldig en alleen advies;
geen shell, opgeruimde VPS-schijf, gerepareerde runner, claim, dispatch,
automatische herstart of productie-release is uitgevoerd of toegestaan.

Rechtstreekse verbonden zSSH read-only waarneming op 20 oktober:
`/dev/sda1` circa **94% vol**, **6.6 GiB beschikbaar**, swap
**7772 / 8191 MiB in gebruik**, hoge load rond **15.45**. Deze
waarneming bewijst geen bestaande scheduleractivatie; exacte-head
CI, onafhankelijke review, protected-main en bevoegd runtime-eigendom
blijven vereist. Geen QA-/regressiesuites geschreven of handmatig gedraaid.

### 20 oktober · geïntegreerde privé schijfruimtewerkbank bij VPS-druk

Een **nieuwe uitvoerbare operatorfunctie** `scripts/zsentinel_disk_hotspots.py`
brengt op verzoek grote daadwerkelijke toegewezen schijfblokken onder één
expliciet gekozen bestaande projectworkspace in kaart. Ze helpt de P0
runner-/capaciteitsbeheerder concrete overmaatbronnen te vinden voordat
die een eventuele, afzonderlijk geautoriseerde opruimactie besluit.

Werking:
- Alleen Linux-bestandsmetadata via `os.scandir` en
  `stat(follow_symlinks=False)`. Geen file-content-reads, shell, symlink
  traversal, andere mounts, service-, worker-, queue- of GitHub-writes.
- Standaard hard begrensd op 20.000 entries, diepte 4, 5 seconden,
  bestanden van minstens 64 MiB, vijftien grootste kandidaten;
  hardlinks tellen één keer. Het resultaat bevat toegewezen ruimte,
  niet alleen de misleidende logische bestandsgrootte.
- Gevoelige namen (.ssh/.git/.env/secrets/token/password/etc.) worden niet
  betreden of in kandidaatnamen getoond. Onvolledige coverage
  (afgekapt, diepte, ontbrekende toegang, uitgesloten privénamen) is
  expliciet **partial**; observatiebytes zijn uitsluitend ondergrenzen.
- Het losse HTML-rapport is uitsluitend te schrijven met de bestaande
  create-only private `write_private_output` (mode 0600, nooit overwrite).
  Ook een oud of groot bestand is **geen bewijs** dat verwijderen veilig is.
  Er bestaat geen delete-, truncate- of automatisch herstartpad.

Eénmalig standalone:
```sh
python3 scripts/zsentinel_disk_hotspots.py \
  --root /home/ubuntu/zennay-cloud \
  --format html \
  --output /home/ubuntu/zennay-cloud/.operator-private/disk-report.html
```
De operator maakt de besloten, bestaande uitvoermap zélf vooraf aan.
De opdracht schrijft alleen het nieuwe HTML-rapport; bij bestaande
bestandsnaam, verkeerde rechten of ongeldige bron stopt ze.

**Ook geïntegreerd in de bestaande zSentinel live cockpit**:
```sh
python3 scripts/zsentinel_live_cockpit.py \
  --database /path/to/existing/portfolio_queue.db \
  --project cloud \
  --disk-hotspots-root /home/ubuntu/zennay-cloud \
  --disk-hotspots-output /home/ubuntu/zennay-cloud/.operator-private/disk-report.html \
  --format html > /private/existing-operator-location/cockpit.html
```
Daar verschijnt **alleen een padvrije brongebonden samenvatting**:
gevonden minimumruimte, aantal grootbestandkandidaten, dekking en
niet-doorzochte mappen. De bestandsnamen/absolute workspace blijven
uitsluitend in het aparte privérapport; ook JSON en text bevatten geen
bestandspaden. Dit is geen autonome cleanup of runnerfix. De job draait
niet vanzelf en mag geen actieve claims of geserialiseerde releases raken.

Directe verbonden zSSH observatie op 20 oktober: `/dev/sda1` 94% bezet
met 6.6 GiB vrije ruimte; top-level `history.db` ongeveer 656 MiB;
dit laat nadrukkelijk **niet** zien dat die database veilig weg kan of
een P0 runnerincident daardoor is opgelost. Alleen besloten eigenaar-
inspectie kan een daadwerkelijke live-retentie- of schrijversituatie
vaststellen. Bestaande exact-head CI, onafhankelijke review,
protected-main, P0 #1326 en billing #1523 blijven bindend.
Geen QA/regressiesuites geschreven of handmatig gestart.

### 20 oktober · Portfolio en mapgerichte schijfanalyse afgerond

De bestaande éénmalige portfolio-CLI ondersteunt dezelfde opt-in
`--disk-hotspots-root` plus `--disk-hotspots-output` als de live cockpit.
Het exacte bestandsoverzicht verschijnt alléén in het privé 0600 HTML-rapport;
de HTML/JSON/tekstportfoliocockpit bevat uitsluitend een padvrije samenvatting.
`--disk-hotspots-output` mag niet samenvallen met een bestaand rapport,
de queue-DB, workerdata, status-ledger of rotatiecursor. Zonder beide
flags wordt geen filesystemscan uitgevoerd. ICS/handoff/desk blijven
hiervan uitgesloten.

De scanner toont ook de vijftien zwaarste **waargenomen eerste-niveau
projectonderdelen**. Dat maakt veel kleinere bestanden samen zichtbaar,
naast de vijftien grote individuele bestanden. Waargenomen fileblokken
zijn door begrensde diepte, tijd, privacyfiltering, hardlinks en mogelijke
gedeelde filesystemblokken **niet** gelijk aan exacte vrij te maken ruimte.
Geen automatische cleanup kandidaat wordt ooit als veilig verklaard.

Gevoelige workspace roots en `.pem`, `.key`, `.p12`, `.pfx`, `.jks`
of `.keystore` worden geweigerd/overgeslagen. Labels worden HTML-escaped.
Zelfs een verouderd groot bestand of een lokale `history.db` is niet
zonder bevoegde eigenaar en actuele schrijvercontrole te verwijderen.
Concreet uitvoeren verandert geen services, queue, claim, Notion, runner,
GitHub Actions of productiecode.

Voorbeeld portfolio (expliciete besloten uitvoermap moet bestaan):
```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window auto --skip-ci-discovery --skip-github-issues \
  --disk-hotspots-root /home/ubuntu/zennay-cloud \
  --disk-hotspots-output /home/ubuntu/zennay-cloud/.operator-private/disk-portfolio.html \
  --format html
```
`--github-only` betekent dat actuele queueclaims en andere VPS-runtime
statussen ONBEKEND blijven, **niet** dat de gekozen lokale schijfmeting
recht geeft op een runner-, claim-, herstel- of deployactie. Geen QA-suites
toegevoegd of gedraaid. Laat de bestaande CI/review/protected-main
releasegates intact.

### 20 oktober · eerlijkere workspace-dekking en verplichte privé-uitvoer

De schijfruimtewerkbank doorzoekt nu breedte-eerst (`deque.popleft`)
zodat één diepe projectmap andere eerste-niveau projecten niet volledig
verdringt voordat het begrensde tijd-/itemsbudget op is. Onvolledige
submappen blijven expliciet onverwerkt in het verslag.

Voor de zelfstandige tool is `--output PRIVATE.{html,json,md}` nu
**verplicht**. Padrijke schijfresultaten verschijnen **nooit op stdout**:
na succesvolle create-only schrijf verschijnt alleen een neutrale
bevestiging zonder workspace- of bestandsnamen. Dit maakt de eerder
getoonde standalone HTML-opdracht een geldige standaard in plaats van
een optionele privacymaatregel. Beide cockpitintegraties vereisen apart
`--disk-hotspots-output PRIVATE.html` en vermelden namen alleen in die
besloten output.

De bronhelper weigert bovendien expliciet
credentials-/geheimenwerkspaces en skips bekende sleutelbestandsextensies.
Nog altijd geen autorisatie voor file deletion, dataretentie, herstart,
Notion/queue mutations of automatisch CI dispatch. Geen QA-suites
geschreven of gestart; enkel exacte-bron Python syntax gecontroleerd.

### 20 oktober · groei/krimp tussen twee strikt vergelijkbare privé-observaties

zSentinel heeft nu een **brongebonden historische vergelijking** naast een
momentopname. De zelfstandige `scripts/zsentinel_disk_trend.py` gebruikt
uitsluitend een eerder opgeslagen **regulier privé-JSON-bestand (0600)** en
het huidige read-only scandocument. Een symlink, te groot/leeg bestand,
ongeldig JSON of dubbele JSON-keys wordt geweigerd.

De vergelijking weigert verschillende workspace-roots, gewijzigde
scanlimieten of een niet-oplopende / meer dan 72 uur oude bronvolgorde.
Zij bewaart hoogstens 128 zwaarste waargenomen projectonderdelen per bron,
en toont verschillen uitsluitend van groepen die **in beide** snapshots
aanwezig zijn. Niet meer gezien = **ONBEKEND**, niet opgeschoond.
De scan zelf is begrensd, kan tijdens actief schrijven lopen en sluit
gevoelige paden uit. Zelfs een negatieve som is daarom **geen bewijs van
vrijgemaakte schijfruimte**.

Dit werkt voortaan in **alle drie uitvoerroutes**:
- standalone `zsentinel_disk_hotspots.py --previous OLD.json`
- bestaande `zsentinel_live_cockpit.py --disk-hotspots-previous OLD.json`
- bestaande `zsentinel_portfolio_overview.py --disk-hotspots-previous OLD.json`

Voor een startmomentopname:
```sh
python3 scripts/zsentinel_disk_hotspots.py \
  --root /home/ubuntu/zennay-cloud \
  --format json \
  --output /home/ubuntu/zennay-cloud/.operator-private/disk-T0.json
```
En later hetzelfde scanprofiel, met besloten groei/krimp-HTML:
```sh
python3 scripts/zsentinel_disk_hotspots.py \
  --root /home/ubuntu/zennay-cloud \
  --previous /home/ubuntu/zennay-cloud/.operator-private/disk-T0.json \
  --format html \
  --output /home/ubuntu/zennay-cloud/.operator-private/disk-T1.html
```
Het padrijke T1-verslag is uitsluitend privé; de geïntegreerde cockpit
en portfolio-HTML/JSON/tekst ontvangen enkel de padvrije
**waargenomen MiB-verschilsom, aantal gedeelde mappen en
onvolledige-bronwaarschuwing**. Bij een fout is er geen stilzwijgende
"alles opgelost" status, geen claim, automatische cleanup, runnerrestart,
workflowdispatch, CI-omzeiling of release.
 
### 20 oktober · werkelijke mount-capaciteit naast momentopnameverschillen

De nieuwe onafhankelijke disk-observer legt bij iedere expliciete
privé-scan ook `statvfs` vast voor het **zelfde bestaande
projectworkspace-filesysteem**: totale capaciteit, voor de normale
runtimegebruiker werkelijk beschikbare bytes en het percentage
niet-beschikbare blokken. Alleen in het privé JSON-bronbestand
staan daarbij hostnaam en mount-`st_dev`; de portfolio/live-cockpit
toont uitsluitend de padvrije cijfers.

Twee snapshots vergelijken kan voortaan alleen wanneer **alle**
volgende bronidentiteiten overeenkomen: workspacepad, scanprofiel,
Linux hostnaam, mount-device en totale filesystemcapaciteit. Andere
hosts, een nieuw ingehangen volume en oudere bronformaten zonder
montage-identiteit worden expliciet geweigerd; maak daarvoor eerst een
nieuwe T0 JSON-bron. Een passende T0/T1-vergelijking toont zowel
gesamplede groepen als de **feitelijk via statvfs waargenomen verandering
in beschikbare GiB** op hetzelfde filesystem.

**Belangrijk:** meer statvfs-vrije ruimte bewijst alleen een verschil
tussen twee bronmomenten, niet welke bestanden veilig verwijderd zijn.
Schrijvende processen, filesystem-reserveringen en andere jobs kunnen
tussendoor veranderen. Daarom blijven `disk_freed_verified=false`,
`cleanup_authorized=false`, `runner_recovered_verified=false` en
`claims_changed=false`. Geen automatische restarts/opschoonacties.

Directoryscans gebruiken voor hun laatste padcomponent nu
`O_DIRECTORY|O_NOFOLLOW` en gesloten filedescriptors. Ook mappen met
`private` in de naam worden niet meer doorzocht. Het controleerbare
resultaat blijft een volledig privé, create-only rapport en geen
publieke bestandslijst. Geen QA- of regressiesuite gestart; uitsluitend
een kort syntactisch compile van de bronmodules.

### 20 oktober · éénmalige echte read-only VPS-scan en bewijsgrens

Met verbonden zSSH is de nieuwe, op commit gepinde standalone-scanner
**eenmalig uitgevoerd als read-only in-memory metadata-inspectie**:
`max_entries=1000`, `max_depth=2`, `max_seconds=2`, workspace
`/home/ubuntu/zennay-cloud`. Er werd geen privérapport geschreven en
er zijn geen bestanden ingekeken, verplaatst of verwijderd.

Geanonimiseerde directe bronuitvoer:
- `mount_available_gib_observed=6.66`
- `mount_unavailable_percent_observed=93.05`
- `observed_allocated_gib_min=0.66` (slechts het onderzochte venster)
- `large_file_candidates_min=1`
- `coverage=partial`, `truncation_reason=entry_budget_exhausted`
- `directories_unscanned=692`

Deze getallen zijn GEEN complete vrijruimte-/bestandsinventaris, geen
bewijs van het oorspronkelijke runnerprobleem en geen schoonmaakrecht.
De directe inspectie toonde een echte presentatiefout:
`private_report_written=true` in de padvrije samenvatting, terwijl
bewust niets was opgeslagen. De samenvatting zet nu expliciet
`private_report_written=false` en
`private_report_creation=not_verified_at_summary_time`, want ze
wordt vóór de create-only outputactie geconstrueerd. De HTML legt
deze herkomstgrens uit en vermeldt geen bestandsnamen.
Geen QA-/regressietestprogramma gestart; dit was één gerichte
functionele read-only inspectie met harde bronlimieten.


### 20 oktober · Privé open-bestandsverwijzingen vóór een bevoegd schijfherstelbesluit

De bestaande schijfdrukwerkbank heeft nu **optionele open-descriptor-inspectie**.
Dit is bedoeld om in het besloten hotspotrapport direct te herkennen
welke van maximaal 15 grote bestandskandidaten door Linux-processen
open gehouden worden. De extra bron wordt alleen gelezen als een operator
uitdrukkelijk `--inspect-open-handles` (standalone) of
`--disk-inspect-open-handles` (live cockpit / portfolio) kiest.

De begrensde `/proc/*/fd`-metadatascan onderzoekt maximaal 2.048
processen, 16.384 descriptormetingen en 2 seconden. Verwijzingen worden
gekoppeld op actuele `(st_dev, st_ino)` na no-follow-heridentificatie
van het pad. Een bestandsnaam wordt uitsluitend in de create-only 0600
privérapporten getoond; proces-ID's, procesargumenten, omgeving en
bestandsinhoud worden niet overgenomen. Publieke JSON/HTML krijgen
alleen het aantal kandidaten met een waargenomen verwijzing en de
dekkingstatus, nooit namen of PIDs.

**Dit is geen schrijversdetectie of vrijgavebewijs.** Een aangetroffen
open verwijzing bewijst geen schrijfactie. Nul verwijzingen bewijst nooit
dat het bestand ongebruikt of veilig verwijderbaar is: /proc-races,
rechten, externe hosts, nieuwe processen en scanlimieten blijven bestaan.
Het rapport gebruikt daarom consequent
`active_writer_verified=false`, `safe_to_delete=false`,
`files_deleted=false` en `processes_stopped=false`.

Voor de zelfstandige werkbank (nieuwe privé uitvoer, niet stdout):
```sh
python3 scripts/zsentinel_disk_hotspots.py \
  --root /home/ubuntu/zennay-cloud \
  --inspect-open-handles \
  --format html \
  --output /home/ubuntu/zennay-cloud/.operator-private/disk-open-references.html
```
De beide geïntegreerde operator-CLI's accepteren naast hun bestaande
`--disk-hotspots-root` en `--disk-hotspots-output` nu de expliciete
`--disk-inspect-open-handles`-optie. Zonder root plus privé-output wordt
de nieuwe bron geweigerd. Alle uitvoer behoudt de bestaande
GitHub-review, exacte-head CI, beschermde main en runtime-eigenaargates;
er is **geen** cleanup, claimherverdeling, runnerherstart of release ingevoerd.


### 20 oktober · Bestandrotatie, padveiligheid en procesprivacy

Latere broncorrecties op dezelfde PR scherpen de werkelijk gebruikte
read-only scan aan: open-verwijzingen mogen alleen als positief bewijs bij
een groot-bestandkandidaat komen wanneer diens **oorspronkelijke bij de
schijfscan gemeten `st_dev + st_ino`** nog overeenkomt. Is het bestand
intussen geroteerd, vervangen of verdwenen, dan wordt die
bestandsidentiteit ONBEKEND; nooit automatisch nul geopende schrijvers.
De begrensde scanners openen bij BFS-mapherbezoeken **iedere
padcomponent** opnieuw via `O_NOFOLLOW` en directory-FD's relatief aan
de gekozen projectroot. Een achteraf verwisselde parent-symlink kan zo
geen nieuw pad buiten de bedoelde workspace toelaten. Gevonden procesrefs
worden per kandidaat als teller bijgehouden zonder PID's in een resultaat
of persistente datastructuur te bewaren. Open fd != actieve writer;
geen open fd != veilige vrijgave.

Dit blijft broncode op de nog niet gemergede PR; afzonderlijke CI,
independent review en bevoegde runtime release blijven nodig.


### 20 oktober · Verwijderde maar nog geopende bestanden (tweede ruimtebron)

Een mapscan ziet per definitie geen bestanden meer waarvan de directory-entry
is verwijderd, zelfs wanneer een Linux-proces de inode nog geopend houdt.
Daarom heeft de bestaande zSentinel **schijfruimtewerkbank** nu de opt-in
`scripts/zsentinel_disk_unlinked_open.py`-bron. Deze bemonstert uitsluitend
`/proc/*/fd/N`-metadata van reguliere files op **hetzelfde filesystem** als
de expliciet gekozen projectworkspace, met harde maxima van 2.048 processen,
16.384 descriptors, 8.192 unieke inodes en 2 seconden.

De bron registreert alleen aantallen unieke unlinked inodes, open
verwijzingen, betrokken procescount en waargenomen toegewezen fileblokken.
Zij leest geen fd-doelpad, procesargumenten, bestandinhoud of secrets en
geeft **geen PID, inode-ID, pad, sleutel, bestandsnaam of procescommando**
in output terug. Publieke HTML/JSON toont dezelfde padvrije minimale
bron samengevat; besloten create-only 0600 HTML/JSON/Markdown toont
de uitgebreidere bronstatus. De uitkomsten blijven beperkt tot een
momentopname; niet-toegankelijke/racende `/proc`-items markeren
`coverage=partial`, niet "geen probleem".

**Gebruik standalone:**
```sh
python3 scripts/zsentinel_disk_hotspots.py \
  --root /home/ubuntu/zennay-cloud \
  --inspect-unlinked-files \
  --format html \
  --output /home/ubuntu/zennay-cloud/.operator-private/disk-unlinked.html
```
De bestaande eenmalige live en portfolio-CLI's gebruiken dezelfde
`--disk-hotspots-root` + `--disk-hotspots-output` en voegen optioneel
`--disk-inspect-unlinked-files` toe. Zonder expliciete privé scan en
output start de /proc-bron niet.

**Uitvoergrenzen:** "zelfde filesystem" impliceert niet "afkomstig uit
deze projectworkspace". Waargenomen toegewezen blokken zijn ook **GEEN
gegarandeerd terugwinbare ruimte**. Open fd's bevestigen geen actieve
schrijvers; niets gevonden bewijst geen afwezigheid. De module autoriseert
geen fd-close, SIGTERM, service-restart, bestandsherstel, opschoning,
Notion-claim, workflowrerun of merge/release. Een bevoegde runtime-owner
moet herkomst, actieve writers, herstelbaarheid en bewaarbeleid zelfstandig
vaststellen voordat een afzonderlijke operationele ingreep plaatsvindt.

Bron is op bestaande zCloud PR #1563 gecommit, niet op main gemerged.
Een read-only, exact-head in-memory uitvoering via verbonden zSSH kon
de private en padvrije operatorrapportage opbouwen. Daarbij stond de
VPS-mount op circa 93,19% niet-beschikbaar; geen positieve unlinked
matches binnen **partiële** /proc-dekking. Dit is geen herstelbewijs.


### 20 oktober · P0 self-hosted runner: exact GitHub-jobbewijs combineren met lokale listener

De bestaande eenmalige `zsentinel_portfolio_overview.py` kan nu via
`--observe-local-runner` de lokale, begrensde `/proc`-waarneming van
`zcloud-vps-1` koppelen aan een **tijdens dezelfde run** door bestaande
GitHub-read-only-collector opgehaalde exacte `Zennay/zCloud` jobreceipte.
Nieuwe module `scripts/zsentinel_runner_cross_source.py` hergebruikt de
reeds geïmplementeerde lokale `zsentinel_local_runner_observer.py` en
`zsentinel_recovery_refresh.py`; geen aparte GitHub crawler of live
runner-service writer. De offline HTML/JSON/tekst toont maximaal drie
Nederlandstalige operatorregels: PROBLEEM, ACTIE, STATUS.

- `queued` + geen runner toegekend + **complete** lokale bron met
  listenerproces: inspecteer GitHub-runnerregistratie, labels, groep
  en repo-toegang; een listenerproces is geen bewijs van registratie.
- `queued` + geen assignment + **complete** lokale bron zonder
  listener: bevoegde runtime-eigenaar inspecteert de service; nooit
  impliciet zelf herstarten.
- Bij onvolledige `/proc`, ontbrekend exact jobbewijs, hosted/custom
  labels of `waiting/pending` blijft conclusie ONBEKEND respectievelijk
  mogelijk approval/concurrency. Een workflow-terminatie of historisch
  toegewezen job bewijst evenmin herstel.

Gebruik één bekende bestaande, exacte GitHub run/job/head binnen het
geselecteerde window dat project `cloud` bevat. Een gekoppelde
GitHub-job gebruikt het bestaande formaat
`--job cloud:RUN_ID:JOB_ID:FULL_HEAD_SHA` en vereist in de portfolio-CLI
ook de expliciet bestaande `--database`-bron. `--github-only` mag
niet worden gecombineerd met `--observe-local-runner`, omdat de
runtimebron dan bewust niet beschikbaar is. Geen workflow wordt gestart
of herhaald door deze flags.

Een brongecontroleerde lokale VPS-observatie zag op 20 oktober één
listenerproces, maar `/proc` was **niet volledig** en er was voor die
uitvoering geen exact gekoppelde GitHub-job. De nieuwe kaart zegt daarom
`no_pinned_zcloud_job` / geen herstelbewijs; geen worker resumed,
dienstherstart of repo-/runnerregistatie geclaimd. In de actuele GitHub
source-head observatie stonden verplichte workflows nog queued/pending.

`registered_runner_verified=false`,
`job_resumed_verified=false`, `restart_authorized=false`,
`workflow_dispatch_authorized=false` en `claims_changed=false`
blijven onveranderlijk. Bestaande P0 [#1326](https://github.com/Zennay/zCloud/issues/1326),
billing [#1523](https://github.com/Zennay/zCloud/issues/1523),
onafhankelijke review, exacte-head CI en geserialiseerde productie-
eigenaar blijven vereisten.


### 20 oktober · Exacte VPS-runnertriage direct zonder queue-DB

Nieuwe operatorfunctie `scripts/zsentinel_runner_triage.py` gebruikt **één
reeds bestaande GitHub Actions run/job/SHA** en leest maximaal twee
GitHub GET-bronnen plus één lokale begrensde `/proc`-steekproef.
Geen queue-DB of publieke zCloud-server nodig: bruikbaar wanneer de
queuebron niet beschikbaar is, zonder hem te vervangen of te repareren.

Voorbeeld op de **bevoegde, reeds verbonden VPS**, met echte bestaande
nummers/het originele SHA uit GitHub (dit zijn placeholders):
```sh
python3 scripts/zsentinel_runner_triage.py \
  --run-id RUN_ID --job-id JOB_ID --head-sha FULL_40_HEX_SHA
```
De standaarduitvoer geeft direct **drie Nederlandstalige regels**:
probleem, volgende bevoegde eigenaaractie en status. Optioneel kan de
operator `--format html --output /private/new-report.html` of
`--format json --output /private/new-report.json` gebruiken. Deze
bestanden worden uitsluitend **create-only 0600** aangemaakt: bestaande
bestanden worden nooit overschreven. De operator moet voor elk verslag
een eigen, expliciete nieuwe private bestandsnaam kiezen. Zonder expliciet
outputpad worden geen HTML-/JSON-bestanden geschreven.

Belangrijke verbeteringen ten opzichte van een losse job/runnerstatus:
- Het exacte job-ontvangstbewijs bewaart ook de canonieke volle commit-SHA,
  `run_id`, `job_id`, GitHub-observatieklok en oorspronkelijke
  job-creatietijd. Dit voegt **geen** nieuwe API-aanvragen toe.
- De plaatselijke /proc-waarneming en GitHub-waarneming moeten elk
  maximaal **vijf minuten oud** zijn en hoogstens vijf minuten uiteen
  liggen; anders krijgt de kaart `source_clocks_not_fresh_or_coincident`
  en nooit een actuele runnerdiagnose.
- Een self-hosted job in GitHub-status `queued` zonder toewijzing is pas
  een **mogelijke vastloper** na **minimaal dertig minuten** volgens de
  oorspronkelijke job-creatietijd. Korter: `queued_recent_not_yet_stalled`.
- `waiting`/`pending`, hosted/custom labels, gedeeltelijke lokale
  procesdekking, foutieve/missende GitHub-bronnen en historische
  runner-toewijzing blijven verschillende onzekerheidsstaten.
- Een geweigerde GitHub-read krijgt expliciet
  `github_job_unavailable`; deze wordt niet langer verwisseld met
  `no_pinned_zcloud_job`.
- De HTML bevat uitsluitend een lokaal, strikt gevalideerd linkje naar
  de **exacte bestaande GitHub-job**, nooit een herstart- of retryknop.
  SHA- en bronklokken dienen uitsluitend diagnose, geen runnerbeheer.

Deze functie is alleen-lezen en geeft op geen enkele route
`runner_restart_authorized`, `workflow_dispatch_authorized`,
`job_resumed_verified` of `claims_changed` vrij. De afwijking
tussen GitHub-job en /proc-processen is **geen** bewijs van een defecte
listener, gecorrigeerde labels, werkende GitHub-registratie of automatische
herstelmogelijkheid. Autorisatie, onafhankelijke PR-review, vereiste
exact-head CI en geserialiseerde productie-release blijven buiten scope.

Het bestaande `zsentinel_portfolio_overview.py` toont de gekoppelde
operatorinformatie nu in HTML, JSON en tekst, én in de afzonderlijke
privé Markdown-hersteloverdracht (`--format handoff`). Gebruik
`--observe-local-runner` alleen wanneer de bestaande runtime-DB
uitdrukkelijk beschikbaar is. Anders de zelfstandige triage-CLI hierboven.


### 20 oktober · Eén commando voor exact-job hersteltriage (zonder werkende queue-DB)

Voor [P0 self-hosted runner #1326](https://github.com/Zennay/zCloud/issues/1326)
is `scripts/zsentinel_runner_triage.py` toegevoegd. Deze haalt met de
**bestaande** begrensde `zsentinel_recovery_refresh.refresh`-bron maximaal
één GitHub-run plus één exact GitHub-job op. Daarna volgt de bestaande lokale
`zsentinel_local_runner_observer` (alleen-lokale `/proc`-informatie).
De queue-DB of projectserver hoeft dus niet bruikbaar te zijn.

Op de gekoppelde, geautoriseerde VPS:
```sh
python3 scripts/zsentinel_runner_triage.py \
  --run-id <BESTAANDE_RUN_ID> \
  --job-id <BESTAANDE_JOB_ID> \
  --head-sha <EXACT_BESTAAND_40_HEX_SHA>
```
De standaarduitvoer is een Nederlands drieregels-operatorantwoord.
Voor een offline, uitsluitend **nieuw** 0600-privébestand:
`--format html --output /pad/naar/bestaande-prive-map/nieuwe-triage.html`,
of `--format json --output /pad/naar/bestaande-prive-map/nieuwe-triage.json`.
De bestaande `zsentinel_private_output` weigert overschrijven/symlinks.
Er wordt nooit een CI-rerun, runnerherstart of claim uitgegeven. Bronfouten
worden als onbekend en zonder geheimen in CLI-uitvoer gemeld.

Voor [P0 GitHub-hosted billing/admission #1523](https://github.com/Zennay/zCloud/issues/1523)
is daarnaast `scripts/zsentinel_hosted_gate_triage.py` op dezelfde PR
geïmplementeerd. Deze leest alleen één bestaande exacte GitHub-run/job/SHA
(maximaal twee GET's), met strikt toegestane repository-identiteiten
`--repository cloud` (`Zennay/zCloud`) of
`--repository ikea` (`Zennay/ikea-enhanced-app`):
```sh
python3 scripts/zsentinel_hosted_gate_triage.py \
  --repository cloud --run-id <BESTAANDE_RUN_ID> \
  --job-id <BESTAANDE_JOB_ID> --head-sha <EXACT_BESTAAND_40_HEX_SHA>
```
Alleen een **mislukte** terminale hosted/custom job met expliciet
bevestigde niet-gestarte stappen én zonder runner-toewijzing wordt als
`completed_without_runner_steps_possible_admission` gemarkeerd.
Zelfs dit is **geen bewijs dat billing de oorzaak is**; uitsluitend
een aanwijzing dat de bevoegde accounteigenaar GitHub Actions
billing/spending, platformannotaties en admissiebeleid moet bekijken.
Een succesvolle, geannuleerde, nog wachtende of wél uitgevoerde
job krijgt een andere status. Een self-hosted job wordt nooit ten
onrechte als GitHub-hosted billingincident aangemerkt.

Ook dit commando ondersteunt uitsluitend drie regels tekst of een
create-only privaat `--format html|json --output ...`-rapport.
Geen nieuwe betaalde jobcapaciteit, autorisatieverandering, financiering,
retries, runnerrestart, deployment of claim is onderdeel van deze tools.
De enige codewijzigingen zijn op [zCloud PR #1563](https://github.com/Zennay/zCloud/pull/1563)
gecommit; protected-main, echte exacte-head verplichte CI, onafhankelijke
review en geserialiseerde release-eigenaar blijven verplicht.


### 20 oktober · Portfolio queue claimpogingen vergelijken (#677)

Nieuwe *alleen-lezen* `scripts/zsentinel_queue_churn.py` koppelt twee
**bestaande** lokale `portfolio_queue`-bronmetingen aan hetzelfde SQLite
bestand, zonder enige write naar de queue, lease, worker of scheduler.

Eerste meting (alleen als een ECHTE queue-DB met tabel `portfolio_queue`
via het reeds bevoegde bronpad beschikbaar is):
```sh
python3 scripts/zsentinel_queue_churn.py \
  --database /bestaand/privaat/portfolio-queue.db \
  --snapshot-output /bestaand/privaat/queue-20261020-1.json
```
Latere meting + optioneel nieuw privérapport:
```sh
python3 scripts/zsentinel_queue_churn.py \
  --database /bestaand/privaat/portfolio-queue.db \
  --previous /bestaand/privaat/queue-20261020-1.json \
  --snapshot-output /bestaand/privaat/queue-20261020-2.json \
  --format html --output /bestaand/privaat/queue-trend-1.html
```
Als de live DB daarna onbereikbaar is maar **beide brongebonden 0600**
privémetingen reeds bestaan, kan de nieuwe offline route alsnog een
besloten herstelbord maken:
```sh
python3 scripts/zsentinel_queue_churn.py \
  --previous /bestaand/privaat/queue-20261020-1.json \
  --current-snapshot /bestaand/privaat/queue-20261020-2.json \
  --format html --output /bestaand/privaat/queue-trend-2.html
```
De paden hierboven zijn placeholders: verzin geen live DB, schema, eerdere
snapshot of write-bevoegdheid. De opgeslagen observaties zijn **uitsluitend
privé** en de `zsentinel_private_output`-writer schrijft create-only,
mode 0600 en weigert overschrijven en symlinks.

**Wat de functie wel/niet bewijst:**
- De reader opent SQLite als `mode=ro` en `PRAGMA query_only`, leest
  maximaal 2.500 actieve rijen en geeft een SHA-256 bronidentiteitsdigest
  van dezelfde host/pad/device/inode af. Roterende bronbestanden of een
  andere host mogen nooit als één tijdserie doorgaan. De originele
  bestandsnaam, queue UUID's, worker-identiteiten en claimtokens worden
  niet getoond in de publieke rapportage.
- De vergelijking vereist dezelfde bronidentiteit en strikt opeenvolgende
  UTC-waarnemingen binnen 72 uur. Alleen de reeds bestaande besloten
  16-hex `queue_ref`-samples die **in beide waarnemingen nog actief** zijn
  leveren bewezen waargenomen **claimpogingentellerstijging**.
- Een hogere *groepsteller*, meer gequeue'de items of extra attempts
  bewijst **geen mislukte run, automatische retry of veilig vrijgegeven
  lease**. Verdwenen/nieuwe items en ontbrekende samples blijven expliciet
  onbekend.
- HTML, JSON en Markdown bevatten uitsluitend bounded resultaten.
  De `--queue-previous`-optie in de bestaande
  `zsentinel_portfolio_overview.py` voegt een geanonimiseerde samenvatting
  toe aan portfolio HTML, JSON, tekst en de privé human-handoff. Als de
  oude bron ontbreekt of ongeldig is, toont het portfolio **onbekend** en
  gaat de overige onafhankelijke rapportage gewoon door.
- De output autoriseert geen lease release, claim, retry, signal, worker
  restart of queue-reparatie. Het menselijke vervolg hoort bij een
  bevoegde queue-owner met echte progress receipts en claim-eigenaarschap.

**Actueel bewijs en blocker op 20 oktober:** via de verbonden zSSH
read-only vastgesteld dat de twee in `/home/ubuntu/zennay-cloud`
gevonden bestanden `portfolio_queue.sqlite` en `portfolio_queue.db`
beide **geen** tabel `portfolio_queue` aanbieden. Daarom is een live
trend uit die bestanden niet mogelijk en is er ook geen claim dat een
queue-retry defect gerepareerd is. De bevoegde runtime-/queue-eigenaar
moet óf het echte read-only bestaande `portfolio_queue`-bronpad,
óf twee reeds bestaande, brongebonden 0600-observaties beschikbaar
stellen. Geen alternatieve DB aangemaakt en geen data vervalst.


### 20 oktober · Worker-eventcursors versus heartbeats vergelijken (#1342)

De bestaande `zsentinel_worker_progress_observer.py` kreeg een
**privé file-identiteit** (dezelfde host, resolved DB-path en inode,
gehashte 64-hex code; geen DB-path in publieke UI). Op de echte, reeds
bestaande `history.db` worden geïndexeerde runner-events, workerconfig
en globale slots uitsluitend gelezen via SQLite `mode=ro` en
`PRAGMA query_only`. Er worden geen user prompts, tab-URLs of
eventpayloads geëxporteerd.

Nieuwe `scripts/zsentinel_worker_progress_delta.py` vergelijkt twee
brongebonden workerobservaties en maakt het verschil zichtbaar tussen:
- een **stijgende eventcursor én progresscursor**: expliciet
  generation-progress/finished **eventsig­naal**, geen bewijs van commit,
  taakafronding of correcte claim-epoch;
- stijgende gewone eventcursor terwijl de progresscursor onveranderd
  is: wel nieuwe events/heartbeats, **geen nieuw expliciet progress-event**;
- **geen eventtoename** tijdens een gemeten >=15-minuten venster voor een
  aan een globaal slot toegewezen configured-running worker: een
  reviewkandidaat, **geen automatische stall-/recoveryconclusie**;
- gepauzeerde of drainerende workers: **eigenaarshold**, geen incident
  dat zelfstandige overname toestaat;
- lagere/verdwenen cursor: mogelijk reset/retentie/bronverandering;
  behoud onzekerheid en vraag de eigenaar om bronreconciliatie.

**Voorbeelden op een bevoegde VPS met bestaande bron:**
```sh
python3 scripts/zsentinel_worker_progress_delta.py \
  --database /home/ubuntu/zennay-cloud/history.db --project cloud \
  --snapshot-output /bestaande-prive-map/worker-1.json

# Op een later werkelijk bronmoment:
python3 scripts/zsentinel_worker_progress_delta.py \
  --database /home/ubuntu/zennay-cloud/history.db --project cloud \
  --previous /bestaande-prive-map/worker-1.json \
  --snapshot-output /bestaande-prive-map/worker-2.json \
  --format html --output /bestaande-prive-map/worker-trend.html
```
De CLI ondersteunt ook uitsluitend bestaande private observaties
(`--previous /.../worker-1.json --current-snapshot /.../worker-2.json`)
voor offline vergelijking, zolang het hetzelfde exacte host/DB-bestand,
hetzelfde geselecteerde projectvenster en twee oplopende UTC-tijdstippen
binnen 72 uur zijn. Alle bestanden zijn expliciet gekozen, create-only
0600, zonder overschrijving.

Een bestaande portfolio-invocatie kan bovendien met
`--worker-events-database /home/ubuntu/zennay-cloud/history.db`
en `--worker-previous /bestaande-prive-map/worker-1.json` een
geanonimiseerde samenvatting opnemen in HTML, JSON, tekst en
`--format handoff`. De vergelijking is **opt-in**; een beschadigde
oude bron maakt uitsluitend dit onderdeel `unavailable`, niet de
andere onafhankelijke P0/CI/queue-bronnen.

Directe read-only bronuitvoering via de verbonden zSSH op 20 oktober
vond voor `cloud` twee worker-slotvoorbeelden en één expliciete
eigenaar-followupkandidaat, zonder workerherstel- of productbewijs.
**De claim-epoch blijft onbekend; geen claim, pauze, restart, retry,
dispatch, release of productie-actie is toegestaan door deze functie.**


## VPS SQLite/WAL disk owner workbench (20 oktober 2026)

De optionele SQLite-footprint werkbank voor schijfruimteproblemen leest
alleen de eerste 100 SQLite-headerbytes (met O_NOFOLLOW en bestandsidentiteit)
en stat-only metadata van WAL/SHM op dezelfde mount. Nooit SQL, VACUUM,
checkpoint, delete, truncate, claims, runner of CI-mutaties.

Voor een enkele DB; kies zelf een al bestaande prive rapportdirectory en
een NIEUW doelbestand (create-only, 0600):

    python3 scripts/zsentinel_sqlite_footprint.py \
      --root /home/ubuntu/zennay-cloud \
      --sqlite history.db \
      --format html \
      --output /path/to/existing/private/sqlite-owner-20261020.html

Dezelfde bron is gekoppeld aan de bestaande schijfruimtescan:

    python3 scripts/zsentinel_disk_hotspots.py \
      --root /home/ubuntu/zennay-cloud \
      --sqlite history.db \
      --max-entries 1000 --max-depth 2 --max-seconds 2 \
      --format html \
      --output /path/to/existing/private/disk-owner-20261020.html

Ook bereikbaar via zowel de portfoliocockpit als de live cockpit, met
--disk-hotspots-root, --disk-hotspots-output en herhaalbare --disk-sqlite.
De private HTML bevat file-labels en metadata; de publieke samenvatting
alleen het aantal bronwaargenomen databases en de harde
reclaimable_bytes_verified=false, ZONDER namen of private paden.
JSON en Markdown zijn uitsluitend prive rapportformaten, niet voor
openbare dashboards of GitHub artifacts.

SQLite-header-pagecount/freelist tonen hoogstens het laatst zichtbare
checkpoint. Een groeiende WAL kan nieuwere logische data bevatten.
Slechts bij ongewijzigde device/inode/size/mtime tijdens de read,
consistente header-version en paginagrootte = bestandsomvang verschijnt
een indicatieve interne page-freelist (nooit diskruimtegarantie).
De eigenaar controleert actieve schrijvers, back-ups, retentie,
diskcapaciteit, exclusieve maintenance en productieleases alvorens een
eventueel apart geautoriseerd checkpoint/compactie/cleanup te plannen.
Geen actie of veilig op te ruimen bytes worden door deze tool afgeleid.

Nieuwe bron van PR #1563 moet de oorspronkelijke onafhankelijke review,
exact-head verplichte CI en protected-main eigenaar-releasepoort
doorlopen. Er is geen runtime deployment of zelfherstellende runner.

### Optionele SQLite-header in bestaande bewaakte VPS audit

De al bestaande handmatige `.github/workflows/zsentinel-vps-owner-reviewed.yml`
biedt naast het reeds verplichte exacte **current main** commitbevestiging,
actor Zennay, permanente runner-identitycheck, read-only GitHub-permissies
en resource-admission nu een standaard **uitgeschakelde** boolean
`inspect_sqlite_space`. De eigenaar kan die pas na normale code-review,
CI en protected-main integratie bewust kiezen bij het bestaande
`workflow_dispatch`-formulier. Niet in deze PR-branch starten.

Alleen bij `inspect_sqlite_space=true` én geslaagde bestaande
capaciteitsgate volgt één extra read-only header-/WAL-statting van de al
bekende `history.db`. In `GITHUB_STEP_SUMMARY` staan uitsluitend
anonieme MiB-tellingen, header-dekking en expliciet dat interne
freelistpagina's GEEN veilig vrijmaakbare bytes bewijzen.
De workflow schrijft geen nieuw rapportbestand of databasegegevens
naar artifacts; geen SQL, checkpoint, VACUUM, bestandsverwijdering,
workflowretry, claimwijziging of serviceherstart. De bestaande
VPS-bescherming, eerste-keer event-/SHA-pin en begrensde runtijd
blijven ongewijzigd. Bij onbetrouwbare bron weigert de extra stap
afgesloten in plaats van herstel te claimen.


## Koude projectbestanden naar externe VPS-Archief opslag (20 oktober)

**Geobserveerde VPS situatie:** rootvolume /dev/sda1 is 94% vol
(circa 6,2 GiB vrij); een **afzonderlijk gemount**
vpsarchive:zcloud-vps-volume is beschikbaar onder /home/ubuntu/VPS-Archief,
waar df ongeveer 5 TB capaciteit en circa 5 TB vrije ruimte meldt.
Dat is metadata op één tijdstip: geen bewijs dat het externe archief
schrijfbaar, betrouwbaar, veilig of geschikt is voor elke dataset.
Het permanent actieve geheugen/swap en databasewriters kunnen niet
zonder eigenaarstoestemming worden onderbroken.

Nieuwe zelfstandige read-only productfunctie:

    python3 scripts/zsentinel_archive_readiness.py \
      --root /home/ubuntu/zennay-cloud \
      --archive-root /home/ubuntu/VPS-Archief \
      --min-age-days 7 \
      --max-entries 5000 --max-depth 3 --max-seconds 5 \
      --format html \
      --output /path/to/existing/private/archive-owner-20261020.html

De bron bouwt voort op de bestaande begrensde grote-bestanden
inventaris; accepteert alleen een al bestaande, niet-gesymlinkte externe
directory op een fysiek anders geïdentificeerde mount. Candidate hints
komen uitsluitend van bestandsextensies van oudere grote data-/model- en
replaybestanden; de maximaal 15 grootste **apart van algemene grote
bestanden** verzamelde koude-extensiehits (vanaf 64 KiB) vormen GEEN
complete inventaris. Zo wordt een archiefshortlist niet door een groot
actief SQLite-bestand verdrongen. In privésamenvatting staan bestandsnamen en
eigenaarcontrole; publieke dashboards tonen alleen aantallen,
waargenomen opslagcapaciteit, scanbeperkingen en expliciet
files_transferred_or_removed=false.

Naast de standalone CLI is het plan optioneel te activeren in
zsentinel_disk_hotspots.py (--archive-target /home/ubuntu/VPS-Archief).
Dezelfde --archive-target en --archive-min-age-days zijn opgenomen in
zsentinel_live_cockpit.py en zsentinel_portfolio_overview.py wanneer ook
een opt-in --disk-hotspots-root en privé --disk-hotspots-output worden gebruikt.

Dit is een **werkende veilige voorselectie**, **geen kopieer-,
migratie-, opruim- of productieherstelproces**. Actieve DB/WAL,
live worker-state, realtime modelcheckpoints, secrets, private datasets,
licenties, back-upkwaliteit, encryptie, retentie, mount quota,
remote schrijfrechten en restores zijn niet onafhankelijk beoordeeld.
Elke verplaatsing moet pas na aparte eigenaarautorisatie, beschermde
integriteit-gecontroleerde overdracht en veilig herstelplan plaatsvinden.
Niet zelf verwijderen, kopiëren, schijf op nul brengen of source-prunes
starten vanuit deze observer. Geen claim/queue/runner/CI-affecterende
actie, geen QA-suite of workflow-dispatch.

De bestaande CI, branch protection, review en geserialiseerde
VPS/productie release-toestemming blijven verplicht.


### Eén privé-archiefshortlist over meerdere projectmappen

Met de nieuwe stand-alone portfolio-runner hoeft een operator geen losse
schijfrapporten per project meer samen te voegen. Een run behandelt maximaal
vier expliciet gekozen workspaces **onafhankelijk**; een ontoegankelijke map
wordt als onbekend vastgelegd en onderdrukt de andere bronnen niet.

    python3 scripts/zsentinel_archive_portfolio.py \
      --workspace /home/ubuntu/zennay-cloud \
      --workspace /home/ubuntu/Haxlab \
      --archive-root /home/ubuntu/VPS-Archief \
      --max-entries 2000 --max-depth 2 --max-seconds 2 \
      --format html \
      --output /path/to/existing/private/archive-portfolio-20261020.html

Het werkbord is privé (create-only 0600); bronpaden en mogelijke bestandsnamen
worden NOOIT naar stdout of een openbaar dashboard gekopieerd. Het publieke,
bronarme summary-model bevat alleen aantallen en meldt expliciet
files_copied_or_deleted=false / source_disk_space_reclaimed=false.

Directe echte zSSH-read-only observatie op twee bovengenoemde projectmappen
en een beperkte scan op huidige exact bronhead: twee bronnen toegankelijk,
geen archiefkandidaten in het onderzochte, **gedeeltelijke** venster.
Dat betekent nadrukkelijk NIET dat er nergens koude replays of models staan.
Zelfstandig aanpassen aan een ander vrij projectpad of langer (maximaal
5 seconden per workspace) read-only inventariseren is mogelijk. Een
feitelijke overdracht/verwijdering is NIET geautoriseerd of ingebouwd.

### Bronwaarneming na afzonderlijke koude-file inventaris (20 oktober)

De bestaande disk metadata-wandeling verzamelt nu behalve de top-15 grote
bestanden ook een **eigen** top-15 voor archiefextensies vanaf 64 KiB. De
archiefwerkbank gebruikt uitsluitend die aparte steekproef bij recent
aangepaste bron en geen enkele lijst is volledigheids- of kopieerbewijs.
De publieke archiefsamenvatting weigert onbegrensde, niet-numerieke of
onjuiste capaciteit voordat die in HTML wordt getoond.

Echte alleen-lezen VPS-bron (twee bestaande workspaces) op exacte
branchtip: 2 van 2 bereikbaar, 2.811 entries gezien, 1.419 directoryslots
niet onderzocht, geen ouder-dan-7-dagen archiefkandidaat binnen het
begrensde venster. Nul in deze onvolledige sample betekent NIET dat er
geen koude data op andere opslagplaatsen is. Geen bestand verplaatst,
gekopieerd, verwijderd, noch claim of runner gewijzigd.


### Eigenaarroute: optionele koude-data-steekproef in beveiligde VPS-audit

De bestaande handmatige `.github/workflows/zsentinel-vps-owner-reviewed.yml`
heeft nu naast `inspect_sqlite_space` de standaard **uitgeschakelde**
boolean `inspect_external_archive`. Alleen de bevoegde eigenaar kan
deze later vanaf **geïntegreerde main** inschakelen na de al bestaande
actor-, exacte-main-SHA-, runneridentiteit-, load/RAM- en omgevingstoetsen.
Er is vanuit deze branch geen dispatch uitgevoerd.

De nieuwe extra stap leest alleen de bestaande project-metadata in
`/home/ubuntu/zennay-cloud` en `/home/ubuntu/Haxlab` en controleert
de afzonderlijke mount `/home/ubuntu/VPS-Archief`. Bounded per bron:
1500 entries, diepte 2 en maximaal 2 seconden; per projectpartitie
worden ontoegankelijke bronnen expliciet onbekend. De GitHub-jobsummary
bevat uitsluitend anonieme aantallen van aangetroffen / onbekende
workspaces en koude-data hints (NOOIT private bestandsnamen, workervelden
of archiefpaden). Geen nieuwe runner-temp export, GitHub-artifact,
SQL, kopie, verplaatsing of delete. Actor en normale CI/review-protecties
blijven ongewijzigd.


## Bevoegd archiefkopie-product: eerst SHA-plan, dan expliciete COPY, nooit DELETE

Een eigenaar kan **na main-merge, permissions en onafhankelijk geverifieerd
project-/dataset-eigenaarschap** daadwerkelijk een door hem/haar geselecteerd
koud bestand eenmalig naar de externe opslag KOPIËREN. Er is bewust geen
automatische scan-naar-kopie, massamigratie, bron-verwijdering, workerstop,
databasecheckpoint of move/rename. Deze afzonderlijke tool is broncode op
zSentinel PR #1563, **niet** geïnstalleerd en **niet** op de live VPS gestart.

Eerst maakt de eigenaar lokaal één brongebonden SHA-256-plan in een expliciet
bestaande besloten rapportmap (dit leest de bestandsinhoud alleen in dit
expliciet gekozen geval, in tegenstelling tot de metadatascanners):

    python3 scripts/zsentinel_archive_copy.py \
      --mode plan \
      --root /home/ubuntu/Haxlab \
      --file 'gekozen-project/bestand.hbr2' \
      --output /path/to/existing/private/copy-plan-01.json

Daarna: geef het exacte gemeten bron-SHA-256 op, verifieer **onafhankelijk**
of het bestand niet in gebruik is, of licentie/retentie/privacy/back-up en
doelopslag dit toestaan, en maak als eigenaar zelf een bestaande private
archiefdirectory aan (eigendom van huidige UID en mode 0700) op een
**andere mount**. De archiefroot kan een bestaande FUSE/remote mount of een
subdirectory daarop zijn, maar schrijfbaarheid en duurzame opslag worden
niet afgeleid uit statvfs.

    python3 scripts/zsentinel_archive_copy.py \
      --mode copy \
      --root /home/ubuntu/Haxlab \
      --file 'gekozen-project/bestand.hbr2' \
      --archive-root /home/ubuntu/VPS-Archief/eigenaar-goedgekeurd \
      --archive-filename 'bestand.hbr2' \
      --expected-sha256 '<exacte-64-tekens-sha256-uit-prive-plan>' \
      --owner-confirm I_APPROVE_ARCHIVE_COPY_NO_SOURCE_DELETE \
      --output /path/to/existing/private/copy-receipt-01.json

Deze voorbeeldnamen zijn **niet** door de scanner als vrijgegeven files
bewezen. De tool vereist één expliciet relatief bronbestand, toegestane
koude-datasuffix, minstens zeven dagen oude owner-controlled regular file
van 64KiB tot 4GiB met een enkele hardlink, veilige nofollow-padcomponenten,
aparte mount, bestaande doelmap zonder groeps-/wereldtoegang,
voldoende gerapporteerde headroom en een **nieuwe** exclusieve doelnaam.
Er is geen batch, geen automatische goedkeuring van scanresultaten, en
er worden nooit brondata gewist. De operator krijgt na een geslaagde
kopie bron-SHA, byteomvang en onafhankelijke leesback-SHA van het
archiefbestand in een create-only 0600 JSON-receipt. Deze readback
bewijst niet dat de remote provider de bytes op lange termijn duurzaam
bewaart of dat een restore werkt.

De exacte approval-tekst is een **expliciete gebruikersbevestiging**, geen
cryptografische identiteit of vervanging van organisatie-/datatoestemming.
Bij gewijzigde source-identity, SHA-mismatch, onvolledige FUSE-write,
onveilige permissies of gewijzigde doel-identiteit weigert de tool. Alleen
de door deze uitvoering exclusief aangemaakte mislukte doelkopie mag hij
opruimen; een reeds bestaand doel en het bronbestand nooit. Als de
kopie mogelijk klaar is maar opslaan van het privé-receipt mislukt,
geeft de CLI expliciet aan NIET blind opnieuw te proberen: inspecteer
doelbestand en oorspronkelijke bron zelfstandig.

Een kopie levert **geen** vrijgemaakte VPS-schijfruimte op. Voor eventueel
bronopruimen, worker-/datasetomschakeling, herstelproef of live servicewijziging
blijven een **afzonderlijk** bevoegd onderhouds-/releasebesluit, back-ups,
eigenaarclaim, verplichte CI en protected-main gates gelden. De
zSentinel-observers, GitHub workflows en scheduler roepen dit nieuwe
copy-pad **nooit automatisch** aan.


### Later opnieuw controleren zonder het origineel te wissen

Na een **geslaagde, door eigenaar geautoriseerde** handmatige kopie kan
de operator ook op een later tijdstip het uitsluitend privé opgeslagen
kopiereceipt opnieuw gebruiken:

    python3 scripts/zsentinel_archive_recheck.py \
      --copy-receipt /path/to/existing/private/copy-receipt-01.json \
      --output /path/to/existing/private/archive-recheck-02.json

Deze uitvoering opent het vorige receipt uitsluitend als 0600 bestaand,
niet-gesymlinkt regulier bestand onder een owner-only besloten directory;
het leest hoogstens 12 KiB private JSON met dubbele sleutels fail-closed.
Daarna opent het alleen de in dat receipt genoemde eerdere gekopieerde
archiefdata via een nofollow bestandsdescriptor, strikt tegen dezelfde
size en SHA-256, met onveranderde bestandsidentiteit tijdens de bounded
read. Hij schrijft alleen het NIEUWE 0600 privé-recheckreceipt. Dit is
een real-time bestand-leesback op die bron en **geen bewezen remote
provider-retentie, herstelbaarheid of restore naar de app**.
Het oorspronkelijke bestand, queue, claim, databases en runner blijven
ongewijzigd. Nooit automatisch opruimen; een aparte owner-approved
restore- en productieonderhoudsroute blijft vereist.


## 20 oktober 2026 · Geen dubbel herstelwerk bij een recente branch zonder PR

De autonome read-only herstelrouter accepteert nu optioneel de **reeds
bestaande canonieke zCloud-branchbron** uit
`scripts/zsentinel_recent_branch_ownership.py`. Dit is geen nieuwe
GitHub-crawler, geen extra branch-protection-gate en geen automatische
workerclaim: dit is een concreet bronconflict vóór het voorstellen van een
tweede implementatie op dezelfde bestanden.

**Gebruikersroute (lokaal, voor bevoegde operator):**

```sh
python3 scripts/zsentinel_recent_branch_ownership.py \
  --project cloud > /path/to/private/current-prless-branches.json

python3 scripts/zsentinel_recovery_router.py \
  /path/to/private/canonical-bundle.json \
  --prless-branches /path/to/private/current-prless-branches.json \
  --format html > /path/to/private/owner-reviewed-recovery.html
```

De CLI leest de JSON met de bestaande stabiele regular-file, nofollow en
strikte bounded `read_json`-reader. Als het bronbestand al in de bundle
staat, wordt niet stilzwijgend een tweede bron overschreven. Voor de
`cloud`-projecttaken wordt uitsluitend een **daadwerkelijk geobserveerde**
bestands- of mapoverlap met een recente PR-loze branch onderdrukt.
De oorspronkelijke branchnaam en het conflicterende pad verschijnen
bij de overgeslagen taak in het Nederlandse tekstoverzicht en de
offline herstelcockpit. De oorspronkelijke taak wordt niet gewijzigd.

Een onvolledige branchlijst, afgekapt bestandspad, verlopen meting (>5
minuten) of ongeldige optionele bron levert **geen vrijverklaring** voor
`cloud`-taken: deze worden voor dit snapshot niet voorgesteld.
**Andere projecten blijven wel onafhankelijk selecteerbaar.** Een niet
aangeleverde bron verandert de bestaande routing niet, maar bewijst nooit
dat er geen open branches of actieve PR-eigenaren bestaan. Zelfs met
volledige broncoverage zijn claim, bestandscope, PRs en releasegates
vlak vóór een eventuele bevoegde daadwerkelijke wijziging opnieuw te
controleren.

De output bevat `recent_branch_ownership` met broncoverage,
reden, geobserveerde branchaantallen, aantal overgeslagen taken en
hoogstens acht voorbeelden; verdere voorbeelden worden als afgekapt
gemeld. `ownership_verified=false`, `claim_authorized=false`.
Er worden geen PRs, issues, worker-claims, queues, CI, VPS-services,
branches of bestanden op de doelrepo gewijzigd door deze optionele
bronroute. Beschermde main en huidige reviews blijven verplicht.
Geen QA-/regressiesuite toegevoegd of gestart.

**Bronoplevering is niet hetzelfde als live:** dit werk staat in de
bestaande open product-PR #1563 tot echte onafhankelijke review,
exact-head CI en de normale protected-main merge zijn afgerond.


### Incidentenbalie: bronvast branchconflict en eigen vervolgtaak

De bestaande `--format desk --desk-output` interactieve offline
incidentenbalie toont nu naast menselijke incidenten ook de uit dezelfde
routerbeslissing afkomstige **overgeslagen producttaken**. Per bronconflict
zijn P0/P1, taakkenmerk, reden, oorspronkelijke branch en het
overlappende bestand zichtbaar. Bij een onvoldoende verse of onvolledige
branchbron staat expliciet dat slechts de `cloud`-takensuggesties zijn
tegengehouden. Andere projecten blijven onafhankelijk behandelbaar.

Alle branchgegevens komen uit het gevalideerde read-only routerresultaat,
worden als inert HTML weergegeven en starten nooit een GitHub-fetch,
taskclaim, merge of runneractie. De gegenereerde incidentenbalie blijft
lokaal, met bestaande CSP en uitsluitend handmatig op te slaan privéhandoff.

Om te voorkomen dat een eerdere handmatige afhandeling ongemerkt wordt
teruggezet op gewijzigde scope, maakt een **daadwerkelijk aangeleverde**
branchbron nu deel uit van de incidentenbalie's bronvingerafdruk.
Handoffs van uitsluitend incidenten zonder branchbron behouden hun
bestaande fingerprint-conventie. Bronidentiteit is nog steeds géén
werk-/releaseautorisatie, alleen een garantie dat de handoff bij dezelfde
waarneming is gemaakt.

## P0: lokale GitHub Actions-runnerprocessen onderzoeken zonder restart

Het zelfstandige operatorproduct `scripts/zsentinel_local_runner_processes.py`
leest op de **bedoelde Linux VPS** één begrensde `/proc`-steekproef.
Het telt `Runner.Listener`, `Runner.Worker` en `runsvc.sh`,
herkent een rechtstreeks worker→listener-procesouderverband en groepeert
processen op lokaal gehashte werkdirectory (maximaal 32 groepen).
Geen PID, commandoregel, secret, pad, GitHub-token, repo- of runnerlabel
wordt in het rapport opgenomen. Ontbrekende cwd-toegang wordt apart
geteld; de scope is dan **partial**, niet bewezen compleet.

Zelfstandige read-only operatorinstructie vanaf een bestaande checkout
onder de toegestane VPS-workspace:

```bash
python3 scripts/zsentinel_local_runner_processes.py --format text
```

Om de bron met de bestaande live incidentenbron te combineren en alle
procesdetails uitsluitend privé op te slaan, gebruik een vooraf bestaande
eigen 0700-rapportmap:

```bash
python3 scripts/zsentinel_live_cockpit.py \
  --github-only --project cloud \
  --skip-github-issues --skip-ci-discovery \
  --observe-local-runners --format html \
  --output /private/zsentinel-runner-processes.html
```

`--observe-local-runners` is **expliciet opt-in** en eist bij de
gecombineerde cockpit een `--output` met `html`, `json` of `text`;
bestand wordt uitsluitend nieuw en mode 0600 onder een eigen map
aangemaakt. De inhoud gaat niet naar terminalstdout. De zelfstandige
read-only CLI ondersteunt daarnaast tekst/JSON/HTML met optionele
private `--output`.

De lokale procescontrole is geen GitHub API-bewijs. Eén of meerdere
`Runner.Listener`-processen bewijzen **niet** dat de juiste
`Zennay/zCloud`-repository en vereiste labels
`[self-hosted,zcloud,vps]` gekoppeld zijn. Ook een onder een listener
geobserveerde `Runner.Worker` is geen bewijs van succesvolle jobs,
voortgaande generatie, veilige idle-status of geslaagde release.
Als de cwd voor processen niet gelezen kan worden (bijvoorbeeld wegens
Linux proc-privacy), staat de lokale groep als `unknown-cwd` gemarkeerd
en mag die **nooit** als één runnerinstallatie worden geïnterpreteerd.

**Bevoegde vervolgstap voor #1326:** vergelijk handmatig de GitHub
`Settings → Actions → Runners`-registratie/repo- en groepszichtbaarheid,
labels en exacte job-toewijzing met deze actuele lokale procesindicatie.
Stop, restart, cancel, rerun, register of reconfigure **niets** op
basis van procesaanwezigheid/afwezigheid. Eén actieve Runner.Worker op
de host maakt een blinde restart bijzonder onveilig. Elke
service- of releaseactie volgt uitsluitend de bestaande
runner- en eigenaarrechten, safe-idle/CI/branch protection en
afzonderlijke releasegoedkeuring. Historische observaties zijn geen
herstelbewijs; #1326 blijft open totdat echte GitHub jobs aan de
bedoelde runner toegewezen zijn en de vereiste uitvoering is bevestigd.

De bron is code op de bestaande open product-PR, niet geïnstalleerd als
VPS-service of scheduler. Geen extra testsuites, jobs of release-hooks.


## 20 oktober 2026 · Historische P0/P1-blokkades blijven als menselijke opvolging zichtbaar

De bestaande herstelrouter behield incidenten terecht als
`not_observed_needs_reconciliation` wanneer een later GitHub-/Notionvenster
te beperkt was om dezelfde run, job of eigenaar opnieuw te vinden.
De **actuele** `watch_alert_candidates` bevat bewust uitsluitend vers
bewezen menselijke blokkades. Daardoor kon een eerder vastgesteld P0
echter uit de menselijke opvolgfeed verdwijnen, hoewel het lokale
incidentledger het nog niet als opgelost zag.

Deze productwijziging voegt daarom een **strikt gescheiden**,
maximaal vijf regels grote `historical_owner_recheck_candidates`-feed toe
met incident-ID, laatst geobserveerde P0/P1-classificatie, oorspronkelijke
project-/job-/HEAD-bron waar beschikbaar en de concrete vervolgstap:
bevoegde eigenaar moet de **huidige** exacte job, issue en gates opnieuw
controleren. De bestaande `historical_unresolved`-inventaris blijft de
autoritatieve bron; meer dan vijf menselijke regels of meer dan dertig
historische zichtbare bronregels krijgen een expliciet truncatievlag.

Ieder historisch voorstel draagt:
`current_source_observed=false`,
`notifier_delivery_authorized=false`,
`recovery_action_authorized=false` en
`owner_confirmation_required=true`.
Deze regels worden **niet** in de actuele notificatiefeed,
automatische werkverdeling of incidente-remediatie opgenomen. Geen
actuele runneruitval, herstel, claim, review, merge of deploy wordt uit
historische afwezigheid afgeleid.

De bestaande menselijke tekstoverdracht en read-only HTML-herstelcockpit
tonen de afzonderlijke historische owner-rechecks vóór de volledige
historische incidentlijst. De HTML-controle bindt iedere getoonde
owner-recheck terug aan het corresponderende bronrecord in hetzelfde
beslisresultaat; afwijkende of verzonnen rows worden geweigerd.

### Expliciet privé-agenda-export, nooit automatisch verstuurd

De gewone `--format ics` blijft **ongewijzigd** en bevat alleen de
bestaande actuele menselijke actiekaarten. Voor een *bewust gekozen*,
uitsluitend lokale kalenderexport die ook nog niet gereconcilieerde
historische menselijke blokkades toont, gebruik:

```bash
python3 scripts/zsentinel_recovery_router.py \
  /private/zsentinel-current-bundle.json \
  --state /private/zsentinel-private-ledger.json \
  --format ics --ics-include-historical-owner-rechecks \
  > /private/zsentinel-owner-review.ics
```

De operator beheert privébron- en outputpaden volgens bestaande
toegangsregels; deze CLI verstuurt **geen** agenda-uitnodiging of
notificatie, voert geen CI-retry uit, herclaimt geen werk en wijzigt
geen remote calendar. Historische ICS-items dragen
`X-ZSENTINEL-HISTORICAL-SOURCE:TRUE`, een unieke stabiele UID,
`STATUS:TENTATIVE` en `CLASS:PRIVATE`.
Hun `DTSTART` blijft de **oorspronkelijke** P0/P1-reviewtermijn
(2 respectievelijk 12 uur na de eerste bronwaarneming), die reeds in
het verleden kan liggen. Er wordt **geen** nieuwe deadline en geen
`VALARM` voor deze oude regels bedacht. De bronvalidatie vergelijkt
incident-ID, project, reden, ernst, originele waarnemingsdatum en
exacte GitHub-referentie vóór opname. Onvolledige historie blijft
als onvolledige steekproef gelabeld.

Alle wijzigingen zijn enkel broncode op de bestaande open product-PR.
Verplichte onafhankelijke review, exacte HEAD-CI, branches en
releasebevoegdheid blijven nodig voordat main of productie verandert.


### Privé incidentenbalie: brongebonden historische eigenaarbriefing

De bestaande scriptvrije bronvulling + offline JavaScript incidentenbalie
(`--format desk`) heeft een afzonderlijke sectie voor eerdere P0/P1
menselijke blokkades waarvan het actuele bronbewijs ontbreekt.
Het oude bronincident blijft in de historische lijst; de aparte
`historical_owner_recheck_candidates`-shortlist is uit precies die
oorspronkelijke privéledger-rijen opgebouwd. Bij renderen vergelijkt de
balie incidentidentiteit, project, ernst, reden, laatst gezien-moment,
exacte GitHub-run/job/SHA en alle expliciete niet-autorisatievlaggen.
Afwijkende of dubbele historische bronnen worden geweigerd.

Een operator kan de historische lijst alleen **lokaal** voorvertonen en
daarna bewust als `zsentinel-historical-owner-rechecks.md` downloaden.
De overdracht vermeldt zowel de originele incidentbronvingerafdruk als
een aparte SHA-256 over de geselecteerde historische bronregels en het
zichtbare truncatiesignaal. De Markdown noemt voor elk getoond incident
de laatst bekende project-/prioriteitsinformatie, exacte oude GitHub
run/job/HEAD (indien destijds waargenomen), en de veilige menselijke
vervolgactie om de huidige bron en eigenaar onafhankelijk te controleren.
Voor het daadwerkelijke downloaden wordt dezelfde statische
bronbriefing opnieuw gecontroleerd tegenover de voorvertoning.

**Dit is een privé, handmatige kennisoverdracht.** Het maakt geen
kalenderagenda aan, verstuurt geen e-mail of push, verandert geen
incidentledgerstatus, neemt geen werk of bestanden over, draait geen CI
opnieuw en is geen bewijs van herstel. De bestaande actuele
menselijke incidentbriefing blijft gescheiden, en de huidige incidenten
en hun eerder opgeslagen privéstatussen behouden hun bronhash.


## 20 oktober · Echte claimstart controleren, niet alleen leaseverval

De bestaande SQLite-observer voor de echte zCloud `portfolio_queue`
leest nu ook het al aanwezige `claimed_at`-veld in dezelfde
`mode=ro` + `PRAGMA query_only`-transactie. Voor actieve taken met
`claimed/running/verifying` zijn de volgende categorieën expliciet
**wederzijds uitsluitend**:

- **Onbetrouwbaar claimvenster:** een geldige vervaltijd bestaat, maar
  de start ontbreekt, niet parseerbaar is, ná het bronmoment ligt of
  niet vóór de vervaltijd ligt. Dit heeft voorrang op een schijnbaar
  verlopen claim.
- **Verlopen lease:** de bron toont een geldige claimstart vóór de
  vervaltijd, maar die vervaltijd is inmiddels verstreken.
- **Vervaltijd onbekend:** geen parseerbare vervaltijd; dit bewijst
  nooit dat de claim vrijgegeven is.
- **Opvallend veel claimpogingen:** een apart telveld, géén
  bewezen mislukte retries en geen toestemming voor nieuwe pogingen.

Het rapport bevat nu
`inconsistent_owner_lease_window_rows` in totalen en
project-/prioriteitsgroepen, plus
`owner_lease_window_inconsistent_observed` op de maximaal twintig
zichtbare, onherleidbaar gehashte risicovoorbeelden. De bestaande
routingschema-admissie controleert deze nieuwe waarden én hun
wederzijdse exclusiviteit voordat bronadviezen naar de console en
privé herstelcockpit gaan. De UI toont inconsistentie apart, inclusief
de correcte actie: **bevoegde actuele claimeigenaar controleert
beide tijdstippen; nooit automatisch herclaimen**. De bestaande
truncatie-, privacy- en brondekkingsgrenzen blijven intact.

Een directe read-only inspectie via verbonden zSSH bevestigde op
20 oktober dat de beoogde VPS-`portfolio_queue`-brontabel de
`claimed_at`-kolom daadwerkelijk heeft. Dit is geen nieuwe
SQLite-migratie, schemawijziging of runtime-reparatie en verandert
geen enkele taakstatus, workerlease, claim, retry, workflow of
productiedatabase. De functie is broncode in de bestaande open PR
en wacht op dezelfde verplichte exacte-head CI en onafhankelijke
review alvorens main of live-productie geraakt worden.


### Portfolio-first oplevering: inconsistente queueclaims per project

De bestaande `scripts/zsentinel_portfolio_overview.py` neemt de nieuwe
onderlinge lease-tijdcontrole nu op in de echte, geobserveerde
projectrijen. Het veld `inconsistent_owner_lease_windows` blijft
`null` wanneer de wachtrijbron niet beschikbaar of onvolledig is, dus
ontbrekende observatie wordt niet voor nul problemen aangezien.

Bij een complete bron verschijnt de afzonderlijke teller in de
HTML-portfoliotabel en de Nederlandse tekstregel. Wanneer een project
een onbetrouwbaar actief claimvenster heeft, is de projectactie
`request_inconsistent_claim_window_owner_review`: huidige eigenaar
moet de start- én vervaltijd controleren; géén automatische herclaim.
Dat wordt eerder getoond dan de normale verlopen-lease- of
veelvuldige-claimaantallen, tenzij een werkelijk hogere externe
GitHub-/runner-/bronautorisatieblokkade voorgaat.

Het is een **zichtbare operatorroute over bestaande projectdata**,
zonder extra CI-runs, extra databaseverbindingen of automatische
wijzigingen aan queue, claims, worker, runner, services of release.
De nieuwe velden bestaan nog uitsluitend op de bestaande open PR,
niet op main of de productie-VPS. Onafhankelijke PR-review,
exacte HEAD-CI en bestaande release-gates blijven vereist.


## Otherwise-eligible work held by execution gates or capacity

The router now distinguishes **execution-gate/capacity holds** from both
unresolved incident dependencies and recent PR-less branch collisions. After
the current task/claim inventory, explicit eligibility, declared dependencies,
active claim scopes and optional branch-overlap checks pass, a queued candidate
that cannot be suggested is retained in the bounded `routing_gate_holds`
summary when the reason is one of:

- `required_gate_blocked`: one or more of the task's declared
  `github_hosted` / `self_hosted` gates are in the current portfolio-wide
  blocked-gate set;
- `self_hosted_capacity_not_advisory_headroom`: the task requires
  `self_hosted` but the trusted read-only VPS observation is unavailable,
  stale, overloaded or otherwise not in `advisory_headroom`;
- `compute_destination_not_explicitly_routable`: the task did not explicitly
  set `requires_vps=false` and did not declare the `self_hosted` gate.

Only the first eight candidates are shown, sorted P0→P3 then project/task ID;
the full bounded count and truncation flag remain available. JSON, operator
text and the offline HTML cockpit use the same summary. The report shares the
task/claim freshness coverage: `incomplete_or_stale` is UNKNOWN, and a zero
count in that state is never an all-clear.

These are otherwise-eligible **human-only HOLDs**, not task recommendations,
claim grants or retry instructions. They intentionally exclude tasks already
held by unresolved incident dependencies, overlapping active claims, or a
recent conflicting branch. The operator must recheck the exact source and the
authorized gate/capacity owner before reconsidering the task. No queue/claim
mutation, workflow dispatch, runner restart, or deploy is performed.


## Integrated read-only worker continuity in recovery refresh

`scripts/zsentinel_recovery_refresh.py` can now combine its existing exact
GitHub run/job refresh and optional queue snapshot with indexed worker
continuity from the existing SQLite history database. Opt in with
`--worker-events-database <existing-history.db>` and one to three explicit,
distinct canonical `--worker-project <project-id>` arguments. The selected
projects must exist in the supplied canonical registry. No project is selected
implicitly, and worker evidence is not added to ICS or the incident-desk
format; when this source is requested, use `--format json`, `text` or `html`.

Examples below use a new output file in an existing directory owned by the
operator and not group/world writable. The report writer enforces mode 0600,
refuses overwrite/symlink paths and never prints the private report to stdout
when `--output` is selected. Choose the actual existing database and only the
projects you intend to inspect:

```sh
python scripts/zsentinel_recovery_refresh.py private-observation.json \
  --registry projects.json \
  --queue-database /path/to/existing-history.db \
  --worker-events-database /path/to/existing-history.db \
  --worker-project cloud --worker-project supa \
  --format html --output /path/to/private/zsentinel-recovery.html
```

The worker observer uses its existing bounded, indexed, `mode=ro`,
`query_only` read of `runner_events`, `runner_workers`,
`ai_global_slots` and, when available, `portfolio_queue`. The report
contains only bounded event metadata and owner-follow-up candidates; it does
not export prompt text, conversation URLs, queue IDs or claim tokens. The
offline HTML embeds the existing script-free worker panel; text includes the
same read-only operator follow-ups; JSON retains the structured observation.
If the explicitly requested source cannot be read or its schema is incompatible,
the output says `degraded_unavailable_no_recovery_proof` rather than omitting
the source failure or implying healthy workers.

This is still evidence, not a claim or lease proof: queue lease validity is
only what the bounded source moment observed, event history is incomplete, and
a generation-finished event is not proof of a delivered product. No queue
status change, claim acquisition/release, retry, worker resume/restart,
GitHub dispatch, service mutation or deployment is performed.


## Unified read-only Notion, queue and worker recovery refresh

The same `zsentinel_recovery_refresh.py` invocation can now optionally include
bounded Notion work-status visibility as well as the explicit worker/queue
sources above. When a Notion or worker source is requested, `--output` is
mandatory: the owner-private report is created with mode 0600 and never dumped
to stdout (to avoid leaking work/worker metadata into terminal or job logs):

- Live Notion read: `--read-notion-claims --notion-data-source <UUID>`
  (or set `NOTION_QUEUE_DATA_SOURCE_ID`) with `NOTION_TOKEN` present in the
  local environment.
- Saved connector snapshot: `--notion-claims-export <private-json>` plus
  `--notion-data-source <UUID>`. The saved source is explicitly unverified
  and cannot establish current ownership.
- Choose one Notion mode. This option supports only JSON, text or offline HTML;
  it is deliberately rejected with ICS, incident-desk and separate
  `--desk-output` formats so a requested source cannot silently disappear
  from the rendered artifact.

Example for a live, bounded read (do not put tokens in command arguments):

```sh
python scripts/zsentinel_recovery_refresh.py private-observation.json \
  --registry projects.json \
  --read-notion-claims \
  --notion-data-source "$NOTION_QUEUE_DATA_SOURCE_ID" \
  --queue-database /path/to/existing-history.db \
  --worker-events-database /path/to/existing-history.db \
  --worker-project cloud --worker-project supa \
  --format html --output /path/to/private/zsentinel-recovery.html
```

Notion results are only `Claimed`, `Running` and `Verifying` status
observations from the existing bounded status windows. A missing token, denied
query, stale/invalid saved snapshot or incomplete source is reported as
`degraded_unavailable_no_claim_authority`, not an empty claim list. HTML and
text identify the source state; JSON preserves the bounded observation. Neither
a Notion status, a worker event, nor a queue lease observation proves repository
file ownership or grants claim/retry/restart/dispatch authority.
