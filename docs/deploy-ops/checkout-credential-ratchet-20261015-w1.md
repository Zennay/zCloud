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

## Technische betrouwbaarheid en regressies (15 oktober, vervolg)
De oorspronkelijke Git-objectlezer begrensde uitvoerbytes maar kon vastlopen op de eerste gebufferde pipe-read, nog vóór `wait(timeout)` werd aangeroepen. De nieuwe source-scanner gebruikt `selectors` met monotone per-Git-call deadlines van maximaal 15 seconden, plus één gedeelde totale scanslot van 120 seconden. Elke afgekeurde of vastgelopen Git subprocess wordt gekilled en gereaped, stdout gesloten, en er wordt geen stderr of untrusted bron gepubliceerd. Een producer die oneindig wacht vóór de eerste byte kan hierdoor niet meer onbegrensd blijven hangen. Gedwongen oversized stdout stopt zodra de eerste byte over het outputbudget wordt ontvangen; blobinhoud blijft maximaal 512 KiB per geselecteerd sourcebestand.

Naast checks op de verplichte `.github/workflows`-directory wordt nu ook de optionele `.github/actions`-tree expliciet op type gecontroleerd. Zowel een symlink op de actions-root als een symlink ergens onder de lokale composite-action-directory wordt geweigerd. Een echte action-directory met een veilige pinned checkout blijft toegestaan. De source-reader is uitsluitend read-only; deze extra afwijzingen muteren geen bestaande Git-workflow, runner, VPS of worker.

Nieuwe adversariële echte Git-tests dekken hard gestalde stdout, outputflood, cleanup/reaping, booleans als ongeldige limieten, verlopen totaaldeadline, oversized workflows en lokale Action symlink-ancestry. De trigger blijft iedere PR naar canonical main en de hele gate blijft GitHub-hosted zonder bevoegdheid tot merge of deploy. Echte CI-resultaten moeten altijd op de exacte broncommit gecontroleerd worden; een groen resultaat op een vorige SHA is niet toereikend.

## Grenzen
Deze scanner is een conservatieve lexical detector en **geen YAML parser, semantische GitHub Actions evaluator of runtime-proef**. Anchors, impliciete YAML merges, template-gegenereerde workflows en shell/dynamic expressions kunnen aanvullende review vereisen. Checkouts buiten de geselecteerde directories zijn niet gedekt. Een PR-maker kan de PR-eigen scanner aanpassen; slechts onafhankelijke beoordeling en beschermde main hebben gezag. Historische risico's worden niet automatisch gefixt. De output bevat geen broninhoud of filename.

## Geen toestemming voor live actie
Een groene broncheck impliceert nooit een verbonden self-hosted runner, een inzetbare deploy-lock, werkende Firefox-sessie of toegewezen workers. #1326 (VPS listener), #1447 (writer-locks), #922 (main security), #580/PWQ-41/#1089 (serialized release) blijven aparte voorwaarden. **Niet zonder review mergen; geen VPS-dispatch/rollback/service/SQLite/worker/queue/runner of productie-update**.

## Beveiligingsfix — YAML-mapping overschrijft checkout credentials (15 oktober)
De oorspronkelijke broncontrole accepteerde een eerste veilige `with.persist-credentials: false`, terwijl een tweede `with: {}` of `with: *alias` onopgemerkt kon blijven. De laatst geldige YAML mapping kan zo de veilige waarde overschrijven en standaard credential-opslag opnieuw activeren. Dit is een **geverifieerde detectieomzeiling op bronniveau**, niet bewijs van een productie-incident of echt tokenlek.

### Nieuwe afwijzingen op nieuw of gewijzigd bronmateriaal
- Duplicate met `with: {}`, `with: *alias`, quoted `"with": {}` of een tweede normale `with`.
- Expliciete YAML key `? with`, YAML merge `<<: *defaults` en tagged keys zoals `!!str with` en `!<tag:yaml.org,2002:str> with`.
- Unicode-geëscapete sleutelvarianten binnen een checkout-step, ook wanneer de volledige naam niet lexicaal herkenbaar is.
- Eén inline `with: {persist-credentials: false}` is conservatief niet geautoriseerd: deze standalone detector is geen YAML-interpreter.
- Normale acties zonder checkout en hun eigen inline `with` blijven toegestaan; een ongewijzigde legacyworkflow wordt slechts op exact dezelfde Git-blob geëxcuseerd.

### Regressies, afbakening en bewijs
Adversariële real-Git-tests dekken deze varianten, zonder gebruik van productie, VPS, worker, Firefox of SQLite. De eerste hosted check werd rood door een verkeerd ge-escapete regex, daarna een beperkte foutredenen-prioritering. Beide testgedreven fouten zijn gerepareerd zonder een onveilige configuratie toe te laten. Op SHA `b159f0b57cfd0064c5b341fdece418a21273293a` heeft de gerichte GitHub-hosted CI 48/48 tests gehaald. Voor deze documentatiecommit en elke latere SHA moet opnieuw exact-head CI worden beoordeeld.

Deze ratchet is PR-eigen source code en biedt geen onafhankelijke release-autorisatie. Review/main-bescherming #922, runner #1326, writers #1447 en serialized release #580/PWQ-41/#1089 blijven intact.


## Minder foutpositieven: echte YAML-checkout versus shell-voorbeeld (15 oktober, vervolg)

Een bronworkflow kan in een GitHub Actions `run: |`, `run: |-` of `run: >-` shellcode de tekst `actions/checkout@...` tonen zonder daadwerkelijk een checkout-step uit te voeren. De eerdere lexicale check interpreteerde deze inert documentatietekst als een riskante workflowwijziging. De nieuwe scanner markeert de *body* van een eenvoudig en volledig herkend YAML run-blockscalar als inert; bij dedent gaan alle echte YAML-regels weer door de beveiligingscontrole.

Deze beperkte contextverbetering maakt echte checkout-adoptie niet vrij: een echte `- uses: actions/checkout@...` na het codeblok blijft onder de strenge expliciete `with.persist-credentials: false`-eis vallen. Niet herkende blokscalarvormen blijven conservatief te reviewen. De decoder voert geen scripts uit en geeft geen deploy-/mergebevoegdheid.

Gerichte real-Git regressies onderscheiden een letterlijk en gevouwen shellvoorbeeld, een voorbeeld met YAML-escape in tekst en echte onveilige checkout-steps na de blokscalar. Ook na deze wijziging blijven onafhankelijke beveiligingsreview en main-protection verplicht.



## Ondubbelzinnige Unicode YAML-bron (extra verificatie)
De ongewijzigde code van een bestaande Git-blob blijft toegestaan als legacy-schuld, maar bij een nieuw of gewijzigd GitHub-workflowbestand mag de broncode niet afhankelijk zijn van onzichtbare Unicode-stuurtekens of afwijkende regelscheiding. Python `splitlines()`, YAML-interpreters en menselijke reviewers kunnen onderling verschillen over bijvoorbeeld U+0085, U+2028/U+2029, bidi-override U+202E en zero-width-formatters.

De bronlezer weigert daarom na strikte UTF-8-decodering C0-tekens behalve tab/newline/carriage return, DEL, Unicode Cf/Cs en expliciete ambiguë regelseparatoren met vaste reden `ambiguous_source_control`. Normale zichtbare Unicode blijft bruikbaar; regressies toetsen een toegestane naam met `café` naast geweigerde null, next-line, line-separator en bidi-override. De scanner verandert nog altijd geen lokale of productiebron; dit is een PR-only fail-closed preflight, geen trusted YAML-parser of live VPS-acceptatie.


## Regulier geneste GitHub Actions checkout-stappen en dubbelzinnige uses-keys
De oorspronkelijke scanner kon alleen `- uses: actions/checkout@<sha>` als veilige stap herkennen. Werkelijke GitHub Actions-workflows gebruiken ook een stap waarin `- name: Checkout`, `- id: cloned` of `- if: success()` de lijstentry introduceert, met `uses:` en `with:` als twee direct ondergeschikte YAML-velden. Zulke *normaal uitgeschreven, gepinde en credential-vrije* stappen worden nu toegelaten, zodat een ontwikkelaar geen bestaande nette codevorm hoeft te herschrijven om de beveiligingsgate te halen.

De scanner laat alleen expliciete `name`, `id` of `if`-lijstentries als directe ouder toe; een `uses:` op een fout indentniveau of binnen `env` is nooit voldoende. Meerdere `uses:`-sleutels in één checkout-lijstitem worden afgekeurd als `ambiguous_step_uses`, zelfs als het eerste voorkomen gepind en veilig is. Dit verhindert verschillen tussen YAML-parsers over de geldige laatste waarde van dezelfde sleutel. Een tweede checkout-step moet ook afzonderlijk `with.persist-credentials: false` krijgen.

Nieuwe echte Git-committests dekken veilige named/id/conditional checkout, ontbrekende/ingeschakelde credential-opslag, nesting/inspringing, dubbele `uses` vóór/na de checkout en lokale composite Actions. Op exacte testcommit `d7a07bcf9c9ab440677345c056b922edebd3ef25` slaagden **74/74 gerichte GitHub-hosted regressietests** in [CI #38779389867](https://github.com/Zennay/zCloud/actions/runs/38779389867) (job `116575334424`); voorafgaande wijziging `28f6334ce8c28305007a0485cbe74fd32d4677ee` had **68/68 PASS** in CI #38779203069. Deze documentatiecommit zelf moet opnieuw exact-head succesvol zijn voordat het geheel als review-ready geldt. Geen van deze controles kan zelf productionele runner- of release-bevoegdheid bewijzen.


## YAML escaped solidus en samengevoegde regel — aanvullende bronbescherming
GitHub Actions/YAML ondersteunt backslash-escapeverwijzingen in dubbelgequote scalars. Een checkout werd eerder lexicaal gezocht als de aaneengesloten tekst `actions/checkout@`; een YAML-string als `"actions\\/checkout@..."` of een string met `\\` direct voor een fysieke regelovergang bevatte die tekst niet letterlijk, terwijl een YAML-decoder de referentie weer kan reconstrueren. Nieuwe bronbestanden konden daarom worden beoordeeld als *zonder checkout*, waardoor de verplichte `persist-credentials: false` niet werd afgedwongen.

Deze omzeiling is nu **fail-closed** behandeld: de scanner weigert `\\/` en de herkende samengevoegde dubbele-quote-regel als `opaque_escaped_yaml`. Controle blijft beperkt tot YAML-bronregels die werkelijk gescand worden: onschuldige voorbeelden binnen een veilig herkend letterlijk `run: |`-blok blijven data en mogen blijven staan. Echte Git-repository-regressies testen escaped solidus, YAML-regelsamenvoeging en volledig gecodeerde/tagged YAML-keys naast een inert shellvoorbeeld. Een nieuwe afwijzing is bewijs van de broncontrole, **geen bewijs dat ooit werkelijk een GitHub-token is gelekt**.

De eerste CI op deze aanpassing was rood door één oude test die een andere naam voor dezelfde deny-categorie verwachtte; alle 78 testinvoeren hadden het juiste toestaan/weigeren-gedrag. De assertions zijn aangepast en de detectie van tabs na de line-continuation-backslash verfijnd. Definitieve acceptatie vereist opnieuw een succesvolle volledige én gerichte CI op exact dezelfde nieuwe commit.
