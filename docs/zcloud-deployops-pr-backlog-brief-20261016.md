# zCloud deploy-ops — volledige reviewwachtrij (16 oktober 2026)

## Doel
Open pull requests inzichtelijk maken zonder te beweren dat een eerste pagina, een groen CI-vinkje of een leeftijdsscore een deployment autoriseert. Dit is uitsluitend een lees-/rapportagelaag voor de onafhankelijke reviewer in de deploy-ops lane. Volg de releaseprocedure bij #1009 en de conflictreconciliatie bij #1204; deze tool vervangt geen van beide.

## Gebruik via GitHub Actions
Na afzonderlijk goedgekeurde integratie op canonical `main`:
1. Open **Actions → zCloud deploy-ops public PR backlog brief**.
2. Kies **Run workflow** op **main**, als eigenaar `Zennay`.
3. Lees de job **Read-only complete PR backlog brief** en de GitHub Actions Step Summary.

De workflow is uitsluitend handmatig; er zijn geen automatische production-, VPS- of PR-triggers. De preflight weigert niet-main, niet-eigenaar en retries; checkout staat vast aan het event-SHA, gebruikt `persist-credentials: false` en draait uitsluitend op GitHub-hosted `ubuntu-latest`. Er zijn geen schrijf-/deployrechten of secrets nodig.

## Lokale (alleen-lezen) controle
```bash
python3 -m unittest -v tests.test_zcloud_deployops_review_backlog_20261016
python3 scripts/zcloud_deployops_review_backlog_20261016.py --output /tmp/zcloud-review-backlog.md
```

De CLI gebruikt publieke, onbevoorrechte `GET`-verzoeken naar `api.github.com/repos/Zennay/zCloud`, zonder token. Er worden maximaal 12 pagina's van 100 PR's gelezen, met maximaal 5 MiB per pagina. De tweede volledige uitleesronde moet exact dezelfde PR-identiteiten, updates en head/base-SHA's geven; de canonical main-SHA wordt ook voor en na beide ronden gecontroleerd. De workflow levert daarnaast `--expected-main-sha` zodat een gewijzigde main nooit bij een ouder checkout hoort.

Onvolledigheid, twijfelachtige of gemanipuleerde velden, niet-GitHub-URL's, dubbele nummers, veranderend main, pagina-cap, HTTP-fouten, redirects, schemafouten of API-rate-limits leiden tot afsluiten met exitcode 2 en **geen gedeeltelijk betrouwbaar overzicht**. Bij een failure mag de operator handmatig overnieuw lezen; niet zomaar meer API-rechten of productiebevoegdheid toekennen.

## Wat het rapport wel en niet bewijst
Het toont de hoeveelheid open PR's op een moment, het aantal concepten, niet-main-targets en PR's die minstens zeven dagen niet zijn bijgewerkt. Veroudering is een **signaal voor review**, niet voor diskwalificatie of automatisch sluiten.

Het rapport valideert **niet**: volledige GitHub reviewdiscussies, branch-eigenaarschap, PR-loze branches, volledige gewijzigde bestandslijsten, daadwerkelijke CI-status, security checks, runnerbeschikbaarheid, productieproces, SQLite-toestand, Firefox-continuïteit of releaseautorisatie.

Voor iedere afzonderlijke PR moet de eigenaar alsnog beoordelen: exact HEAD vs canonical main, reviews, twee-zijdige gewijzigde bestanden en PR-loze branches, branch-mismatch/achterstand, de juiste hosted CI, en de serialized gate van #580/PWQ-41 + #1089. De incidenten #1326 (VPS-runner) en #1447 (productie-writerlocks) blijven afzonderlijk owner-only.

## Handoff
PR met de feature: [#1498](https://github.com/Zennay/zCloud/pull/1498). **Draft totdat de laatste bron-HEAD volledige CI en workflow-privilegecheck groen zijn, gevolgd door onafhankelijke review**. Tests op PR zijn geen bewijs dat de handmatige workflow daadwerkelijk op main is gestart, of dat de VPS is hersteld. Geen dispatch of merge zonder aparte release-authorisatie.
