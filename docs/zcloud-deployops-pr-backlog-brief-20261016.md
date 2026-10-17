# zCloud deploy-ops — open issues eerst, daarna volledige PR-reviewwachtrij (16 oktober 2026)

## Doel
Open issues als eerste tonen en vervolgens open pull requests inzichtelijk maken zonder te beweren dat een eerste pagina, een groen CI-vinkje of een leeftijdsscore een deployment autoriseert. Dit is uitsluitend een lees-/rapportagelaag voor de onafhankelijke reviewer in de deploy-ops lane. Volg de releaseprocedure bij #1009 en de conflictreconciliatie bij #1204; deze tool vervangt geen van beide.

## Gebruik via GitHub Actions
Na afzonderlijk goedgekeurde integratie op canonical `main`:
1. Open **Actions → zCloud deploy-ops issue-first and PR backlog brief**.
2. Kies **Run workflow** op **main**, als eigenaar `Zennay`.
3. Lees de job **Read-only complete issues and PR backlog brief** en de GitHub Actions Step Summary.

De workflow is uitsluitend handmatig; er zijn geen automatische production-, VPS- of PR-triggers. De preflight weigert niet-main, niet-eigenaar en retries; checkout staat vast aan het event-SHA, gebruikt `persist-credentials: false` en draait uitsluitend op GitHub-hosted `ubuntu-latest`. Er zijn geen schrijf-/deployrechten of secrets nodig.

## Lokale (alleen-lezen) controle
```bash
python3 -m unittest -v tests.test_zcloud_deployops_review_backlog_20261016
python3 scripts/zcloud_deployops_review_backlog_20261016.py --include-issues --output /tmp/zcloud-review-backlog.md
```

De workflow toont eerst **open issues** en pas daarna PR's. Het verslag vermeldt expliciet het **UTC-startmoment van de scan**, omdat de GitHub-API geen atomair snapshot over meerdere pagina's levert. Een oude Actions-samenvatting mag niet als actuele situatie of releasebesluit worden gebruikt. De bron is `/issues`, waar GitHub ook open PR's in opneemt: de scanner filtert die expliciet uit, controleert dat ieder PR-nummer uit `/pulls` precies overeenkomt met de PR-markers in de aparte issue-inventaris en herhaalt die volledige issue-inventaris tweemaal. Na de issuescan wordt de volledige PR-inventaris een derde keer gelezen, zodat gewijzigde PR-head-SHA, base, titel, conceptstatus of updated-timestamp die tussen de afzonderlijke PR- en issuepassages verandert, een **fail-closed** afwijzing veroorzaakt. Dit is geen atomair GitHub-snapshot en geeft geen releaseautoriteit. Als aantallen/head-identiteit of beide rondes afwijken, weigert het programma het totaaloverzicht in plaats van open werk over het hoofd te zien. Expliciete P0/P1-titels en labels zijn alleen **niet-onafhankelijk geverifieerde triagesignalen**, nooit bewijs van ernst, eigenaarschap of uitvoerbaarheid. In de eerste tabel verschijnen maximaal 20 open issues, gerangschikt naar bronprioriteit en daarna oudste update. De volledigheidstelling betreft **alle** issues, niet alleen de twintig getoonde. GitHub-assignees worden getoond ter coördinatie. **Niet toegewezen** betekent nadrukkelijk niet dat het werk vrij is: actieve branches, Notion-claims of andere workers kunnen eigenaar zijn.

De CLI gebruikt publieke, onbevoorrechte `GET`-verzoeken naar `api.github.com/repos/Zennay/zCloud`, zonder token. Zowel PRs als GitHub issue-/PR-records hebben elk een limiet van maximaal 12 pagina's van 100 records, maximaal 5 MiB per pagina. Inclusief issues zijn dit bij twaalf pagina's per stream maximaal 62 publieke GET-verzoeken per run (drie PR-passes, twee issue-passes, twee main-reads); de huidige backlog kost minder verzoeken. Anonieme GitHub API-rate limits kunnen fail-closed afbreken. De CLI vraagt geen verhoogde GitHub-toegang. De tweede volledige uitleesronde moet exact dezelfde PR-identiteiten, updates en head/base-SHA's geven; de canonical main-SHA wordt ook voor en na beide ronden gecontroleerd. De workflow levert daarnaast `--expected-main-sha` zodat een gewijzigde main nooit bij een ouder checkout hoort.

Onvolledigheid, twijfelachtige of gemanipuleerde velden, niet-GitHub-URL's, gesloten bronrecords in een `state=open`-inventaris, dubbele nummers, veranderend main, pagina-cap, HTTP-fouten, redirects, schemafouten of API-rate-limits leiden tot afsluiten met exitcode 2 en **geen gedeeltelijk betrouwbaar overzicht**. Bij een failure mag de operator handmatig overnieuw lezen; niet zomaar meer API-rechten of productiebevoegdheid toekennen.

## Wat het rapport wel en niet bewijst
Het toont eerst de volledige hoeveelheid open issues (zonder dubbele PR-records), gevolgd door de hoeveelheid open PR's op een moment, het aantal concepten, niet-main-targets en PR's die minstens zeven dagen niet zijn bijgewerkt. Daarna volgt een **read-only reviewer-aandachtslijst van maximaal 12 recent bijgewerkte, niet-concept-PR's gericht op main**. PR's met een andere basisbranch krijgen een **aparte lijst van maximaal 20 items**, inclusief de exact opgegeven base-branch (als veilige platte tekst); deze mogen niet stilzwijgend als rechtstreeks naar main mergegereed verschijnen. Die lijst is uitsluitend bedoeld voor beoordeling door de bestaande eigenaar; er is geen check op head-CI, reviewgoedkeuring, gewijzigde paden, PR-loze branches of releasegates uitgevoerd en het is nadrukkelijk geen aanbeveling om te mergen. Veroudering is een **signaal voor review**, niet voor diskwalificatie of automatisch sluiten. Onbetrouwbare GitHub-titels en assignee-namen worden als gewone Markdown-tekst weergegeven met escaped opmaaktekens, zodat bijvoorbeeld een nagebootst **MERGED**-label of een externe link niet als een goedkeuring kan verschijnen.

Het rapport valideert **niet**: volledige GitHub reviewdiscussies, branch-eigenaarschap, PR-loze branches, volledige gewijzigde bestandslijsten, daadwerkelijke CI-status, security checks, runnerbeschikbaarheid, productieproces, SQLite-toestand, Firefox-continuïteit of releaseautorisatie.

Voor iedere afzonderlijke PR moet de eigenaar alsnog beoordelen: exact HEAD vs canonical main, reviews, twee-zijdige gewijzigde bestanden en PR-loze branches, branch-mismatch/achterstand, de juiste hosted CI, en de serialized gate van #580/PWQ-41 + #1089. De incidenten #1326 (VPS-runner) en #1447 (productie-writerlocks) blijven afzonderlijk owner-only.

## Handoff
PR met de feature: [#1498](https://github.com/Zennay/zCloud/pull/1498). **Review-ready uitsluitend als de laatste exacte bron-HEAD volledige CI en workflow-privilegecheck groen zijn; onafhankelijke review en afzonderlijke mergebeslissing blijven verplicht**. Tests op PR zijn geen bewijs dat de handmatige workflow daadwerkelijk op main is gestart, of dat de VPS is hersteld. Geen dispatch of merge zonder aparte release-authorisatie.
