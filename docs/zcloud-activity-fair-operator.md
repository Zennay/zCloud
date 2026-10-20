# Lokale Activity zonder projectverdringing (#1518)

De bestaande `scripts/zcloud_activity_fair_page.py` selecteert events per project eerlijk, maar had geen zelfstandige operatoringang. `scripts/zcloud_activity_fair_export.py` maakt dit nu direct bruikbaar zonder het centrale `server.py`-writerpad of de productie-API aan te raken.

## Eenmalige read-only weergave

```sh
python3 scripts/zcloud_activity_fair_export.py \
  --db /pad/naar/bestaande/history.db \
  --project cloud --project supa --project zsentinel \
  --page-size 60 --format html --output /prive/nieuwe/activity.html
```

Het uitvoerpad moet nog **niet** bestaan. De export wordt alleen nieuw aangemaakt met bestandsrechten `0600`; een bestaande export wordt nooit overschreven. Zonder `--output` verschijnt HTML of JSON op stdout. De export is mobiel leesbaar, volledig offline en bevat alleen `ts`, `project`, `kind` en `id` van lokale events, nooit legacy `title` of `detail`.

## Bladeren met behoud van bron- en projectscope

```sh
python3 scripts/zcloud_activity_fair_export.py \
  --db /pad/naar/bestaande/history.db \
  --project cloud --project supa --format json \
  --output /prive/nieuwe/activity-page-1.json

python3 scripts/zcloud_activity_fair_export.py \
  --db /pad/naar/bestaande/history.db \
  --project cloud --project supa --format json \
  --cursor-file /prive/nieuwe/activity-page-1.json \
  --output /prive/nieuwe/activity-page-2.json
```

Vervolg werkt alleen met een geldige versie-1 JSON-export van **dezelfde geordende projectset** en met een aanwezige `next_cursor`. Geen volgende cursor betekent dat de lokale selectie op het meetmoment uitgeput was; dat betekent **niet** dat externe projectactiviteit afwezig is. Het oudere bronmoment kan tijdens de tweede paginering alweer veranderd zijn; de resultaten zijn geen atomische portfolio-snapshot.

De commandoregel beperkt tot 32 projecten, 80 events per pagina, maximaal 16 KiB cursorinput, 2 MiB uitvoer en een read-only databaseverbinding met `mode=ro` en `PRAGMA query_only=ON`. Fouten worden uitgesproken gemeld, niet vervangen door een lege 'geen activiteit'-status. Het operatorproces schrijft nooit naar SQLite of de lokale queue en roept geen GitHub-API aan.

## Niet als live fix presenteren

- **Wel:** zelfstandige private operator-HTML/JSON uit lokale events, eerlijke zichtbaarheid voor een stil project, begrensd doorbladeren.
- **Niet:** geïntegreerde `/api/activity` of een publiek dashboard, GitHub-PR/branch/CI-dekking, bewijs van hoofdbranchmerge, deploy, werkereigendom of runnerherstel.
- **Vervolg eigenaar:** de geserialiseerde `server.py` / Activity-integratierol moet de selector expliciet adopteren en bestaande verplichte reviews, exacte-head CI en releasegates afronden.

Dit pad verandert geen test-, QA-, regressie- of releaseworkflow; er is geen workflow of suite handmatig gestart.
