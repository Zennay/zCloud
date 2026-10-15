# zCloud GitHub-token permission drift — bounded source gate

## Waarom deze controle

De bestaande deploy-ops-gates bewaken workflow-events, runnergebruik, acties en shelluitvoering, maar een nieuwe of gewijzigde `permissions`-toekenning kan een read-only workflow onverwacht GitHub API-schrijfrechten geven. Deze afzonderlijke broncontrole weigert **nieuwe** write-scope risico's vergeleken met de actuele immutable `main`-tree. Bestaande schuld wordt uitsluitend voor dezelfde workflow en job én dezelfde semantisch herkenbare rechten gespaard: verplaatsen, dupliceren of uitbreiden telt als nieuw. Opaque YAML-waarden worden gebonden aan een inhoudshash, zodat wijziging van een oude onduidelijke declaratie niet stilzwijgend als veilig kan doorgaan.

## Wat wordt beschermd

- Zowel `permissions: write-all` als expliciete `id-token: write`, `actions: write`, `contents: write`, `statuses: write` en andere `*: write`-scopes tellen als write-risico.
- Opaque expressies, flow-maps, onbekende waarden en onduidelijke mapping/indentation tellen conservatief als risico; eenvoudige `read`/`none` en `read-all` blijven read-only.
- Nieuwe workflows en nieuwe jobs zonder expliciete veilige permissiegrens worden geweigerd als ze onbekende repository- of workflowstandaardrechten zouden erven. De bestaande impliciete baseline blijft alleen op dezelfde job en hetzelfde bestand toegestaan.
- Scope is gekoppeld aan workflowpad en job-id. Een nieuwe write in een andere job wordt niet als oude schuld beschouwd. Verwijderen van een bestaande expliciete read-only-declaratie wordt ook geweigerd: daardoor zou een job terug kunnen vallen op ruimere standaard- of overgeërfde tokenrechten.
- Via ongewijzigde Git-objecten wordt zowel de complete baseline als de kandidaat gelezen; symlinks, submodules, ongeldige paden, grote bestanden, NUL en non-UTF8 zijn onvolledig bewijs en dus een afwijzing.
- PR-job draait uitsluitend GitHub-hosted met `contents: read`, exacte SHA-checks, een tweede actuele `main`- en PR-head-check, zonder secrets, VPS, SQLite, service-acties of artifactwrites.

## Grenzen en vrijgave

Dit is een bewust **lexicale** YAML-ratchet, geen volledige YAML-parser of semantische tokenprivilege-analyse. Ongebruikelijke mappingnotaties, expliciete mapping keys, anchors/aliases, duplicate-key shadowing en complexe inline constructies worden conservatief als risicobron behandeld en moeten onafhankelijk worden gereviewd; de scanner kan zowel omzeilingen als false positives kennen. Het maakt geen claims over effectieve GitHub-instellingen, omgevingsbescherming of de runtime. Bij een echte noodzaak voor een nieuwe write-scope moet een afzonderlijke eigenaar de wijziging onafhankelijk beoordelen; de ratchet verleent zelf nooit toestemming.

**Release blijft gesloten:** #633 parent trust debt; #1326 runner, #1447 productie-writer lock, #922 hoofdbranchbescherming en #580/PWQ-41/#1089 serializer blijven bij bestaande eigenaren. Een groene bronrun is geen live runnerherstel, merge, dispatch of deployment. DRAFT tot volledige exacte-HEAD CI, onafhankelijke securityreview en conflictvrije owner-handoff.
