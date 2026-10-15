# zCloud deploy-ops — checkout credential persistence: no-new-debt ratchet (2026-10-15)

## Motivatie en eigenaarschap
GitHub Actions \`actions/checkout\` schrijft standaard een credential helper naar de working tree en maakt die later weer schoon. Als onbetrouwbare PR-code, een gecompromitteerde jobstep of een self-hosted runner de checkout kan lezen, is die tijdelijke credential onnodig risico. Dit is uitsluitend een **nieuwe source-only preventie**, niet een autorisatie voor productie, en raakt geen bestaand deploy-, runner-, browser-, SQLite- of workflow-eigenaarbestand. Scope: #633.

Eigen branch: \`deployops/checkout-credential-ratchet-20261015-w1\`.
Eigen nieuwe bestanden: deze documentatie, één offline Python-scanner, zijn real-Git-tests en één GitHub-hosted PR-workflow.

## Regel
- Vergelijk twee volledige, immutable 40-karakter Git commit-SHA's, niet branch-/PR-tekst.
- Bekijk relevante \`.github/workflows/*.yml/.yaml\` en \`.github/actions/**/action.yml/.yaml\` uit de Git trees. Andere paden zijn uitgesloten. Symlinks, gitlinks, non-UTF-8 en onbegrensde blobs worden geweigerd.
- Elk **nieuw of gewijzigd** relevant bestand met \`actions/checkout@<40 lowercase hex>\` moet **per checkout-step** exact één \`with.persist-credentials: false\` hebben. Dynamische waarden, \`true\`, ontbrekende waarden, duplicaten en dubbelzinnige checkout-syntax worden afgewezen.
- Bestaande ongewijzigde workflowblobs blijven uitsluitend als bekende historische schuld toegestaan. Een bewerkt legacybestand moet voldoen of onafhankelijke review krijgen.
- De guard kan worden uitgevoerd in GitHub-hosted Actions (\`ubuntu-latest\`, alleen \`contents: read\`) zonder VPS labels/secrets. Geen \`workflow_dispatch\`, \`push\`, \`workflow_run\` of \`pull_request_target\` schrijfroute.
- De workflow controleert repository/herkomst, canonical main, PR-head, exact SHA vóór **en na** tests; de uitgevoerde PR-checker is zelf PR-code en daarmee **geen onafhankelijke trusted security authority**. Eigenaar/review- en main-protection zijn vereist vóór integratie.

## Gerichte regressies
Tijdelijke Git-repositories met echte commits testen pinned en expliciete false; missing/true/expressie/duplicate; meerdere checkout-steps; quoted-key, folded en floating checkout; ongewijzigde legacy vs gewijzigde legacy; composite actions; comments en docs; binaire workflow; symlink; SHA-ongeldigheid en geredigeerde denial-output. Normale zCloud hosted tests horen ook groen te worden gecontroleerd op exact HEAD.

## Grenzen
Deze scanner is een conservatieve lexical detector en **geen YAML parser, semantische GitHub Actions evaluator of runtime-proef**. Anchors, impliciete YAML merges, template-gegenereerde workflows en shell/dynamic expressions kunnen aanvullende review vereisen. Checkouts buiten de geselecteerde directories zijn niet gedekt. Een PR-maker kan de PR-eigen scanner aanpassen; slechts onafhankelijke beoordeling en beschermde main hebben gezag. Historische risico's worden niet automatisch gefixt. De output bevat geen broninhoud of filename.

## Geen toestemming voor live actie
Een groene broncheck impliceert nooit een verbonden self-hosted runner, een inzetbare deploy-lock, werkende Firefox-sessie of toegewezen workers. #1326 (VPS listener), #1447 (writer-locks), #922 (main security), #580/PWQ-41/#1089 (serialized release) blijven aparte voorwaarden. **Niet zonder review mergen; geen VPS-dispatch/rollback/service/SQLite/worker/queue/runner of productie-update**.
