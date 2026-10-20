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
