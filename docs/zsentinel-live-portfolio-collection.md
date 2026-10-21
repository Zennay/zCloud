# zSentinel · éénmalig live portfolio-productbord

## Productfunctie
`scripts/zsentinel_portfolio_workboard.py --collect-live` verzamelt in één expliciete
operatoractie de **bestaande open GitHub productissues en daarbij aangetroffen
bestaande PR's** voor de geregistreerde projecten. Dezelfde bestaande collectors
en conservatieve exact-HEAD-PR-reader worden per maximaal drie canonieke
projecten gebruikt. De bestaande portfolio-aggregator presenteert P0 vóór P1
met een eerlijke projectverdeling, observatiemoment en expliciete brondekking.

Voorheen moest een operator voor elk venster eerst zelf een JSON-bronbestand
genereren en daarna 1–6 afzonderlijke bestanden combineren. Dat blijft als
volledig offline route beschikbaar; live is een **opt-in** extra bronroute.

## Operatorgebruik

```sh
# Alleen vanaf een reeds geautoriseerde eigen projectcheckout. Private parent bestaat.
# Laat GITHUB_TOKEN weg voor publiek beschikbare brongegevens.
umask 077
python3 scripts/zsentinel_portfolio_workboard.py \
  --collect-live \
  --format html \
  --output /private/operator-only/zsentinel-portfolio-actueel.html

# Beperk de reikwijdte bewust tot twee geregistreerde projecten:
python3 scripts/zsentinel_portfolio_workboard.py \
  --collect-live --project cloud --project supa \
  --format json \
  --output /private/operator-only/zsentinel-cloud-supa-actueel.json

# Bestaande volledig offline import blijft apart:
python3 scripts/zsentinel_portfolio_workboard.py \
  --snapshot /private/operator-only/bron-1.json \
  --snapshot /private/operator-only/bron-2.json \
  --format md \
  --output /private/operator-only/zsentinel-opvolging.md
```

Elke `--output` is een **nieuwe** reguliere privéfile met veilige
`0600`-permissies via de gedeelde create-only writer, zonder bestaande rapporten
te overschrijven. Live verzameling zonder expliciet privé-outputpad wordt
geweigerd, evenals live + handmatige bronbestanden in dezelfde aanroep.
Gedeeltelijke/onbereikbare GitHub-bronnen krijgen een onvolledige status en
geven een niet-nul afsluitstatus, maar zijn geen lege backlog.

## Merge-eerst PR-inbox zonder extra GitHub-verzoeken

Het productbord toont nu boven de issuekaarten een afzonderlijke lijst met de
**reeds open product-PR's die de issuecollector toch al ophaalt**: maximaal de
acht recentst bijgewerkte open PR's per projectvenster. Expliciet QA-only
werk wordt gefilterd op titelprefix, bekende QA-only labels en
`qa/`, `test/`, `tests/` of `quality-validation/` als branchprefix. De tool
wijzigt geen bestaande QA-PR en start geen QA-/regressiewerk.
Daardoor verdwijnen bestaande PR's niet uit beeld wanneer hun gekoppelde issue
ouder is dan de begrensde eerste/laatste-issue-steekproef. Dezelfde lijst is
opgenomen in privé JSON en Markdown, ook bij de bestaande offline-snapshotroute
wanneer de bronsnapshot dit veld bevat.

De bestaande **exact-HEAD PR-lezer** gebruikt nu ook de overgebleven
ruimte binnen zijn vaste limiet van drie PR's per projectvenster om PR's te
inspecteren die niet naar een gesampled issue verwijzen. Eerst komen urgente
issue-gerelateerde PR's, daarna reviewklare open product-PR's eerlijk over
projecten verdeeld, en daarna drafts. Dit kost hoogstens dezelfde maximaal
15 bestaande PR-GETs per venster. De kaart toont de werkelijk waargenomen
vervolgactie als de PR-HEAD niet veranderde; zonder herlezing blijft de bron
uitdrukkelijk onbekend.

Het offline HTML- en Markdown-overzicht onderscheidt het aantal PR-kaarten
waarvoor exacte-HEAD-bronnen zijn waargenomen van PR's die nog niet zijn
geïnspecteerd of sindsdien gewijzigd. **Geen van beide aantallen is
merge-gereedbewijs**: review-/CI-/branchregels, actuele exclusieve schrijvers
en de bevoegde productie-eigenaar blijven afzonderlijk verplicht.
Expliciete QA-only-titels, labels en bronbranch-prefixen worden zowel uit de
PR-inbox als uit de issue→PR-productroutering geweerd; geen QA is gestart.

Daarnaast verschijnen maximaal **drie** oudere open product-PR's per venster
wanneer ze via een issue-link zijn geselecteerd en op hun exacte HEAD opnieuw
zijn gelezen. De kaart vermeldt expliciet dat het om een oudere issue-linkbron
gaat; zo'n PR hoeft niet in de acht recentste listings te staan. De maximale
portfolio-PR-presentatie blijft begrensd tot **162 kaarten** over maximaal
achttien projecten. Geen extra netwerkverzoeken of QA-rondes voor deze kaarten.

Elke kaart toont project, titel, GitHub-PR-link, draft/open en exact de SHA
*zoals gezien in die PR-lijst*. De koppeling aan een gesampled issue wordt
alleen positief gemarkeerd wanneer die in dezelfde bron is aangetroffen.
Afwezigheid van een koppeling betekent **niet** dat de PR geen issue heeft.
Deze lijst is geen opnieuw opgehaalde actuele HEAD, verplichte CI, approval,
filelease, eigenaarsclaim, merge- of deploytoestemming. Het venster kan afgekapt
zijn en oudere open PR's missen. Voor echte integratie blijven de onafhankelijke
PR-gates van toepassing; het werkbord start nooit zelf een merge.

### Betrouwbare gedeeltelijke dekking bij GitHub-limieten

Een bevestigde globale GitHub-ratelimiet stopt **volgende** projectvensters
zonder opnieuw verzoeken te sturen. Al verzamelde onafhankelijke deelvensters
blijven zichtbaar in het private rapport; de nog niet opgevraagde projecten
verschijnen uitsluitend als `unavailable` / `ONBEKEND`, niet als een lege
backlog of geslaagde scan. De CLI eindigt met een niet-successtatus voor
onvolledige dekking. Ook een recent bronmoment met geweigerde issue-/PR-bron
zet `all_projects_currently_read` niet op true.

## Vergelijk actuele en eerder opgeslagen PR-bronnen

Gebruik een **zelf opgeslagen private JSON-portfolio** uit dezelfde projectselectie
om veranderingen in het werkelijke merge-eerstoverzicht terug te vinden. De
vergelijking is offline, maakt geen GitHub-, Notion-, VPS- of queue-opvragen en
wijzigt nooit een claim of PR.

```sh
python3 scripts/zsentinel_portfolio_workboard.py \
  --collect-live --previous-portfolio ./vorige-prive-portfolio.json \
  --format html --output ./huidige-portfolio-met-verschillen.html

# Ook bij uitsluitend opgeslagen bronvensters, zonder netwerk:
python3 scripts/zsentinel_portfolio_workboard.py \
  --snapshot ./venster-1.json --snapshot ./venster-2.json \
  --previous-portfolio ./vorige-prive-portfolio.json \
  --format md --output ./verschillen-handmatig-opvolgen.md
```

De private vergelijking leest de vorige bron **éénmalig** als begrensd
regulier bestand met `O_NOFOLLOW`, controleert identiteit vóór en na de
lezing, weigert dubbele JSON-velden en bindt de vergelijking aan de SHA-256
van de exact ingelezen bronbytes. De eerdere bron moet dezelfde geordende
canonieke projecten bevatten, dezelfde niet-autoriserende status, een geldige
oude tijdsperiode en maximaal 162 PR-vermeldingen.

De vergelijker bekijkt naast de globale rapportdatum ook **elke eigen
projectvensterdatum**. Een globaal nieuwere rapportage kan immers een
project bevatten dat eerder of op hetzelfde tijdstip is gelezen. Voor zulke
projecten markeert het rapport
`current_project_window_not_newer_NO_DELTA_INFERRED`, vergelijkt het geen
zogenaamd nieuwe HEAD en trekt het ook uit ontbrekende PR-vermeldingen geen
sluitingsconclusie. Die projectnamen worden apart getoond in HTML en Markdown.

De reviewvolgorde is vervolgens: eerst daadwerkelijk **waargenomen gewijzigde
PR-HEADs** (actuele CI/review opnieuw opvragen), daarna projectvensters die
niet nieuwer zijn, daarna andere bronwijzigingen en pas daarna PRs die alleen
nieuw in een beperkte steekproef verschijnen. Alles blijft human-only.

De drie onderscheiden signalen zijn:

- **Waargenomen PR-HEAD gewijzigd:** een bestaande PR-vermelding heeft in
  de nieuwere beperkte bron een andere HEAD; exacte-head checks en review
  moeten opnieuw worden bekeken.
- **Nu wél in de beperkte steekproef:** dit bewijst NIET dat de PR nieuw is.
  De vorige bron kan dezelfde PR eenvoudig buiten de acht recente gevonden
  vermeldingen hebben gelaten.
- **Eerder gezien maar nu niet gesampled:** dit bewijst uitdrukkelijk NIET
  dat de PR is gemerged, gesloten, dat een actieve claim vrij is gekomen
  of dat een runner is hersteld. Bronbereikbaarheid en paginering kunnen
  zijn veranderd. Bij geweigerde huidige GitHub-bron wordt de status
  `current_source_unavailable_NO_CLOSURE_EVIDENCE`.

Het verschilrapport zit als `pr_delta_since_previous_portfolio` in de private
JSON-uitvoer, en als beperkte klikbare bronsectie in HTML/Markdown. De
volledige lijsten zijn maximaal 162 huidige en 162 historisch verdwenen
PR-vermeldingen; de lange HTML/Markdown-lijsten zijn ingekort om de
operatorinterface leesbaar te houden. Geen enkele kaart verleent
merge-/release-/eigendomsautorisatie. Verplichte huidige CI, onafhankelijke
review en productie-eigenaar blijven de enige geldige autoriteitsroute.

## Bewuste grenzen
- Maximaal **18 canonieke repositories**, in maximaal **zes niet-overlappende
  vensters** van drie projecten; per venster maximaal 24 productissues.
- Maximaal **144 GitHub GET-verzoeken theoretisch** voor zes vensters
  (per project drie issue-/PR-list-GETs, per venster maximaal drie bestaande
  PR's maal vijf exacte-head-GETs). Het rapport vermeldt het waargenomen
  budget. Er is geen scheduler, webbrowser-fetch, continue monitor of retry.
- Alleen eerder aanwezige, canoniek gekoppelde PR's worden onderzocht; niet
  aangetroffen PR's blijven onbekend. Geobserveerde PR-HEAD en status worden
  niet als verplichte merge-gateautoriteit gebruikt.
- Elk venster houdt zijn eigen waarnemingstijd en dekking; een onbekende of
  verouderde bron is **niet vrijgegeven**. Zonder actuele Notion-/task_claim-
  lease-eigenaarschap kan geen taak automatisch worden overgenomen.
- Geen QA-/regressiesuite, runnerherstart, claimovername, CI-rerun, billing,
  serverwriter, workflowdispatched taak, merge of deploy. Alle bestaande
  onafhankelijke reviews, verplichte exacte-head CI, beschermd-main- en
  security-/releasevoorwaarden blijven gelden.

Dit bestand beschrijft source-codeproductgedrag op de open zCloud PR, **niet**
een reeds gemergede main-versie of live geautoriseerde scheduler.


## 21 oktober · Notion-claimimport zonder handmatige veldconversie

De bestaande `--notion-claims-export PRIVATE.json`-ingang (live-cockpit,
productissuebord en portfolioweergave) accepteert nu beide concrete
*opgeslagen* responsvormen binnen dezelfde strikte
`zsentinel-notion-connector-snapshot-v1` wrapper:

- Genormaliseerde connectorrijen met `Work ID: 433` **of**
  `Work ID: "PWQ-433"`, `Status: "Claimed" | "Running"` en de
  relationele `Project` als één canonieke URL, als JSON-lijst of
  vooraf geserialiseerde JSON-lijst. Een optionele Notion `?pvs=204`
  verwijdert de projectidentiteit niet meer.
- Notion-API paginarijen met unieke UUID `id`, exact bij de
  gekozen data source passende `parent.data_source_id`, en getypeerde
  `properties["Work ID"].number`, `properties["Status"].status.name`
  en `properties["Project"].relation`. Deze rijen hoeven niet eerst
  handmatig naar platte connectorvelden te worden omgezet. Een directe
  Notion-API queryresponse met `object: "list"`, `has_more: false`,
  `next_cursor: null` en minstens één dergelijke bronrij mag in de
  bestaande privéwrapper worden geplaatst zónder `data_source_ids`;
  de herkomstidentiteit moet dan op **iedere** pagina-rij kloppen.
  Een lege REST-respons zonder expliciete `data_source_ids` wordt
  geweigerd; afwezigheid mag nooit als vrije scope worden behandeld.
  Afgekapt/onbekend project blijft **ONBEKEND**, nooit claimvrij.

De operator bewaart de werkelijke bron-`observed_at_utc` en data-source
in de bestaande wrapper; een oud bestand opnieuw inpakken maakt de bron
niet live. Bestaande strikte eisen blijven: unieke Work IDs, alleen
Claimed/Running, geen cursor of incomplete paginering, ≤100 rijen,
≤128 KiB, ≤10 minuten oud, geen symlink, geen dubbele JSON-sleutels.
Een ongeldige of conflicterende rij degradeert de hele optionele
Notion-bron. Import is alleen een **positieve statusaanwijzing**;
werkelijke claimhouder, scope, bestandsrechten, dispatch, merge, runner
en productieherstel blijven onbewezen en ongeautoriseerd. De tool voert
geen Notion-write of GitHub/VPS-actie uit.

## 21 oktober · Notion-verificatiefase is geen vrije scope

De live read-only Notion-query en de privé opgeslagen connector- of native
API-snapshot behandelen nu **Claimed, Running en Verifying** als drie
verschillende positieve aanwijzingen van nog niet afgerond projectwerk.
Dit is een bewust conservatieve verruiming: in de werkelijke Portfolio
Work Queue staat **Status** als select-veld en **Work ID** als Notion
`unique_id` (`prefix: "PWQ"`, positieve `number`). De optionele
native import accepteert daarnaast de reeds ondersteunde legacy
`number`/`status`-vorm, maar valideert bij echte `unique_id` het
exacte prefix en bij beide vormen de bronidentiteit.

De importer bewaart aparte geaggregeerde `verifying_project_counts` en
`verifying_count`. De portfolio-HTML toont nu de ondergrens
**Verifying ≥N · eigenaar hercontroleren** bij een project met positieve
verificatiewaarneming; het tekstrapport vermeldt dezelfde eigenaar-HOLD.
De totaaltelling blijft een **observatie van werkstatussen**, géén bewijs
van een huidige lease, uitvoerende worker of bestandsclaim. Ontbrekende
projecten of onvolledige bronnen blijven **ONBEKEND**; de privé-snapshot
blijft maximaal tien minuten bruikbaar.

De bestaande P0/P1 probleemkiezer houdt een gerelateerde OPEN PR ook bij
onvolledige GitHub-dekking vóór een nieuwe implementatie. Zonder bekende
PR wordt bij waargenomen Verifying eerst een eigenaar-/reviewhercontrole
gevraagd in plaats van nieuwe scope toe te kennen. Er wordt nog steeds
**niets** automatisch geclaimd, geretriggerd, gemerged of gedeployed.
