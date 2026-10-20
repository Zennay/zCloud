# zSentinel · één commando voor privé-eigenaaroverdracht

Deze toevoeging is onderdeel van de **bestaande** [zCloud PR #1563](https://github.com/Zennay/zCloud/pull/1563), niet een zelfstandige scheduler of productie-release.

## Wat er daadwerkelijk bijgekomen is

De bestaande live cockpit kan, naast de normale HTML-, tekst- of JSON-weergave, in **dezelfde eenmalige bronlezing** een aparte, private `.md`- of `.json`-eigenaaroverdracht aanmaken. De operator ontvangt een P0-eerst actielijst die strikt onderscheid maakt tussen:

1. verse, exacte incidenten uit de bestaande herstelrouter;
2. owner-review voor toegewezen workers met een relevante pauze, lease-onzekerheid of browserblokkadesignaal uit de **optionele** geïndexeerde SQLite-workerbron;
3. open P0/P1-productissues uit de begrensde bestaande issue-inbox.

Elk item bevat het project, de categorie, volgende **menselijke** controle, en expliciet `claim_authorized=false`, `retry_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`. Onvolledige bronnen worden nergens tot `healthy`, `free` of `recovered` gepromoveerd. Maximaal 24 items worden opgenomen; afkapping wordt zichtbaar aangegeven. De bron levert geen taakclaim, dispatch of vrijgave.

## Eén bronmoment; twee offline resultaten

Op een **reeds geautoriseerde** host met bestaande lokale queue-DB en een **vooraf aangemaakte privémap**:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \
  --database /pad/naar/bestaande/history.db \
  --project cloud --project supa \
  --worker-events-database /pad/naar/bestaande/history.db \
  --handoff-output /private/zsentinel/handoff-20261020.md \
  --format html > /private/zsentinel/cockpit-20261020.html
```

Het P0-eerst-overzicht is ook **zonder** aparte export direct in de offline HTML op te vragen:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \\
  --github-only --project cloud \\
  --show-owner-handoff --format html > /private/zsentinel/p0-werkbak.html
```

Dit creëert geen eigen handoff-file vanuit Python; de HTML-redirect is een bewuste operatorhandeling naar een al bestaande besloten map. De bronverzameling blijft dezelfde eenmalige, begrensde GitHub-only read; queue- en actieve claimstatus blijven in deze variant ONBEKEND. `--show-owner-handoff` vereist expliciet `--format html`.

Bij `--format html` verschijnt dezelfde P0-eerst-werkbak bovendien **boven in de bestaande offline cockpit**, met directe links naar canonieke bestaande productissues. De privé-export en de HTML-kop delen exact dezelfde in-memory bronbeslissing: er komt geen tweede GitHub-, Notion- of SQLite-lezing bij. Onbetrouwbare titels, notities en actietekst worden HTML-geëscaped en bij Markdown ook letterlijk gecodeerd, zodat externe brontekst niet als opmaak, HTML of actieve link kan worden geïnterpreteerd.

De HTML-redirect is een expliciete shellhandeling van de **operator** en krijgt alleen private permissies wanneer de operator zelf een private `umask`/map gebruikt. Voor de **door het programma geschreven** handoff gelden onafhankelijk `O_EXCL | O_NOFOLLOW`, reguliere 0600-bestandspermissies en een bestaande oudermap. De handoff weigert een bestaande bestemming en een pad dat samenvalt met een SQLite-bron, lokale dedup-ledger, opgeslagen Notion-export, vorige privéscan of andere privé-uitvoer.

Machineleesbare uitvoer is ook mogelijk:

```sh
python3 scripts/zsentinel_live_cockpit.py \
  --github-only --project cloud \
  --handoff-output /private/zsentinel/handoff-20261020.json \
  --format text
```

Zonder worker-DB blijft workerstatus `not_requested`; zonder queue-DB blijft queue-status `not_requested_or_unavailable`. Het is dan **niet** bewezen dat er geen claims of lopend werk zijn. Notion-status is hooguit een partiële indicator en bewijst geen actuele file-claim of geldige lease. Bestaande echte CI, onafhankelijke review, exact-head SHA en beschermde main blijven verplicht.

## Gecombineerd portfolio-overzicht: dezelfde P0-eerst actiewerkbak

Dezelfde brongebonden eigenaaroverdracht is nu ook optioneel geïntegreerd in
`scripts/zsentinel_portfolio_overview.py`, naast het bestaande P0/P1-issuebord,
PR-gates, broncoverage en workeractiviteit:

```sh
umask 077
python3 scripts/zsentinel_portfolio_overview.py \\
  --github-only --window 0 --show-owner-handoff \\
  --format html > /private/zsentinel/portfolio-met-owner-handoff.html
```

Het globale portfolio houdt alle canonieke projecten in het bestaande dashboard,
maar **de eigenaaroverdracht omvat alleen het geselecteerde GitHub-venster** en
de werkelijk opt-in gelezen lokale bronnen; andere projecten staan niet
bewijsbaar op groen. De extra HTML-kaarten gebruiken exact dezelfde reeds
opgehaalde `decision` en geen extra providerrequests. Er vindt ook hier geen
claim-, merge-, queue- of releasewrite plaats; het nieuwe opt-in vlagargument
wordt bij niet-HTML-uitvoer geweigerd.

## Beveiliging en release

- De samenstelling is lokaal, onveranderlijk, éénmalig en zonder provider-I/O; alle netwerk-GETs komen uitsluitend uit de al bestaande, begrensde live cockpit.
- De handoff verwerkt alleen begrensde velden en canonieke zCloud/GitHub issue-links, geen workerprompt, browsertab, secret, queue-ID, ruwe claimtoken of pad van een lokaal bestand.
- Handmatige overdracht mag géén automatische claim/runner-restart/retry/billing/merge/deploy activeren.
- Bij ontbrekende of inconsistent geclassificeerde bronnen faalt de handoff conservatief en schrijft geen geldig verklaarde werkbak.
- `PR open` is geen `main merged`. Deze implementatie mag uitsluitend via de bestaande verplichte exacte-head CI, onafhankelijke review, branch protection en geautoriseerde runtime-release naar productie.

## Implementatie

- `scripts/zsentinel_runtime_handoff.py` — bronvalidatie, P0/P1-triage en Markdown/JSON-rendering.
- `scripts/zsentinel_live_cockpit.py` — nieuwe optionele `--handoff-output`, padisolatie, veilige create-only export; bestaande HTML/JSON/tekstworkflow intact.
- `scripts/zsentinel_private_output.py` — reeds bestaande, hergebruikte private 0600-uitvoer; **niet** aangepast.
