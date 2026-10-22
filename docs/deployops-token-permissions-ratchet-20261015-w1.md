# zCloud GitHub-token permission drift — bounded source gate

## Waarom deze controle

De bestaande deploy-ops-gates bewaken workflow-events, runnergebruik, acties en shelluitvoering, maar een nieuwe of gewijzigde `permissions`-toekenning kan een read-only workflow onverwacht GitHub API-schrijfrechten geven. Deze afzonderlijke broncontrole weigert **nieuwe** write-scope risico's vergeleken met de actuele immutable `main`-tree. Bestaande schuld wordt uitsluitend voor dezelfde workflow en job én dezelfde semantisch herkenbare rechten gespaard: verplaatsen, dupliceren of uitbreiden telt als nieuw. Opaque YAML-waarden worden gebonden aan een inhoudshash, zodat wijziging van een oude onduidelijke declaratie niet stilzwijgend als veilig kan doorgaan.

## Wat wordt beschermd

- Zowel `permissions: write-all` als expliciete `id-token: write`, `actions: write`, `contents: write`, `statuses: write` en andere `*: write`-scopes tellen als write-risico.
- Opaque expressies, flow-maps, onbekende waarden en onduidelijke mapping/indentation tellen conservatief als risico; eenvoudige `read`/`none` en `read-all` blijven read-only.
- Nieuwe workflows en nieuwe jobs zonder expliciete veilige permissiegrens worden geweigerd als ze onbekende repository- of workflowstandaardrechten zouden erven. De bestaande impliciete baseline blijft alleen op dezelfde job en hetzelfde bestand toegestaan.
- Scope is gekoppeld aan workflowpad en job-id. Een nieuwe write in een andere job wordt niet als oude schuld beschouwd. Verwijderen van een bestaande expliciete read-only-declaratie wordt ook geweigerd: daardoor zou een job terug kunnen vallen op ruimere standaard- of overgeërfde tokenrechten.
- Via ongewijzigde Git-objecten wordt zowel de complete baseline als de kandidaat gelezen; symlinks, submodules, ongeldige paden, grote bestanden, NUL en non-UTF8 zijn onvolledig bewijs en dus een afwijzing.
- Tekst binnen echte YAML literal/folded `run: |`/`run: >-` scripts is geen tokenpermissie; een shellcommentaar of voorbeeld `permissions: write-all` daarbinnen mag geen false positive opleveren. De broncontrole hervat direct bij een volgende echte gedeïndenteerde YAML-key, waar een job-permissie wél wordt afgewezen.
- Ook onconventionele YAML-structuur voor `jobs` valt onder dezelfde bescherming: geciteerde `'jobs'`-sleutels, expliciete YAML-mappingsleutels (`? jobs`), aliassen, afwijkende jobinspringing, volledig ingesprongen root en dubbele jobnamen/`jobs`-blokken mogen geen nieuw jobonderdeel stilzwijgend standaard-schrijfrechten laten erven. De bestaande structuur wordt alleen als oude schuld geaccepteerd wanneer het volledige workflowblob identiek blijft.
- Onbegrepen aliases, YAML-verwijzingen en compacte/inline jobdefinities worden op de hash van het **volledige workflowbronbestand** vastgezet: een bestaande alias mag niet stilzwijgend een gewijzigde schrijfbevoegde definitie erven. Dit kan ook onschuldige wijzigingen in zulke legacy YAML weigeren; dat is bewust fail-closed.
- Een tussentijdse wijziging van canonical `main` of de PR-branchtop maakt de job rood: zowel vóór als ná de volledige bronvergelijking moeten dezelfde immutable Git-SHA's overeenkomen.
- Adversariële echte Git-fixtures testen afwijkende SHA-volgorde, symlinks, gitlinks/submodules, NUL/ongeldige UTF-8 en te grote blobs; onvolledig bewijs mag nooit als goedkeuring worden uitgegeven.
- PR-job draait uitsluitend GitHub-hosted met `contents: read`, exacte SHA-checks en herhaalde actuele `main`-/PR-head-checks, zonder secrets, VPS, SQLite, service-acties of artifactwrites.

## Leesbare GitHub Actions-uitkomst

De hosted broncontrole schrijft een **Nederlandse, begrensde job summary** voor acceptatie én afwijzing. Bij succes wordt eerst een klein machineleesbaar resultaat in een tijdelijk bestand opgeslagen; pas als **beide remote SHA-identiteiten ná de scan** opnieuw zijn bevestigd, wordt de positieve gebruikerssamenvatting geschreven. Een mislukte scan of gewijzigde SHA levert een **vaste Nederlandse afwijzing via de Bash ERR-trap**, zonder de onbetrouwbare bron of foutdetails te interpoleren. Zo claimt de samenvatting niet voortijdig een groen resultaat als de eigenlijke GitHub-job alsnog rood wordt.

De samenvatting toont alleen geverifieerde niet-negatieve tellingen, laat workflowbron en foutdetails weg en vermeldt expliciet dat release, deployment en mutatie niet zijn toegestaan. Tegenspraak tussen success-status, tellers of enige bevoegdheidsvlag wordt afgewezen. Ook een mislukte schrijfactie naar de lokale summary maakt de controle rood. De regressies bewaken zowel de echte CLI bij succes/afwijzing als het statische gehoste workflowcontract: PR-only, alleen GitHub-hosted, `contents: read`, SHA-pins en bronidentiteit vóór én na de scan.

## Nieuwe YAML-omzeilingen afgedekt (15 oktober, vervolg)

De real-Git-regressies bevatten nu expliciet **YAML-tags op mappingsleutels** (`!!str permissions`), **ankers op sleutelposities** (`&scope permissions`), expliciete complexe YAML-sleutels (`? "permissions"`, `? 'permissions'` en meerdere regels), en **Unicode-escapes binnen getagde/geankerde sleutelstrings** zoals `!!str "permiss\\u0069ons"`. Een YAML-interpreter kan die gebruiken om effectief alsnog `permissions` of `jobs` te produceren, hoewel een simpele regelmatcher de letterlijke naam niet ziet. Daarom is iedere bestaande getagde/geankerde of expliciet complexe mapping-sleutel documentgebonden *opaque debt*: alleen exact ongewijzigde hele workflow-Git-objecten kunnen die oude vorm behouden.

Naast de risicovergelijking controleert de regressiesuite nu het **eigen gehoste PR-workflowcontract**: uitsluitend `pull_request`, token `contents: read`, alleen `ubuntu-latest`, SHA-vastgezette externe acties, geen checkout-credentials, en SHA-validatie zowel vóór als na de scan. Dit beschermt tegen onbedoelde afwijkingen in dit eigen nieuwe workflowbestand, **niet** tegen opzettelijke wijziging van zowel de PR-test als het PR-script door dezelfde auteur.

**Onafhankelijke review blijft vereist:** de scanner en tests draaien vanuit de kandidaat-PR zelf. Een malafide PR zou zijn eigen controle kunnen aanpassen of verwijderen; zonder onafhankelijk vertrouwde bescherming en code-review is een groen PR-resultaat geen afdwingbare beveiligingsgrens. Deze PR wijzigt geen branch protection, publiceert geen betrouwbare main-only enforceer en geeft geen productiebevoegdheid.

## Grenzen en vrijgave

Dit is een bewust **lexicale** YAML-ratchet, geen volledige YAML-parser of semantische tokenprivilege-analyse. Ongebruikelijke mappingnotaties, expliciete mapping keys, anchors/aliases, duplicate-key shadowing en complexe inline constructies worden conservatief als risicobron behandeld en moeten onafhankelijk worden gereviewd; de scanner kan zowel omzeilingen als false positives kennen. Het maakt geen claims over effectieve GitHub-instellingen, omgevingsbescherming of de runtime. Bij een echte noodzaak voor een nieuwe write-scope moet een afzonderlijke eigenaar de wijziging onafhankelijk beoordelen; de ratchet verleent zelf nooit toestemming.

**Release blijft gesloten:** #633 parent trust debt; #1326 runner, #1447 productie-writer lock, #922 hoofdbranchbescherming en #580/PWQ-41/#1089 serializer blijven bij bestaande eigenaren. Een groene bronrun is geen live runnerherstel, merge, dispatch of deployment. DRAFT tot volledige exacte-HEAD CI, onafhankelijke securityreview en conflictvrije owner-handoff.
