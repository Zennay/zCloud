# zSentinel · privé live operator-export

Deze bestaande zSentinel live cockpit kan nu rechtstreeks een **nieuw lokaal
0600-bestand** schrijven. Een handmatig kalenderbestand is daarmee direct
bruikbaar voor menselijke opvolging; een offline HTML-bord of JSON-/tekst-
overdracht kan vanaf **dezelfde eenmalige bronlezing** bewaard worden.

```bash
# Read-only GitHub-only observatie: queue, claims en workerstatus blijven UNKNOWN.
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format ics --output /private/zsentinel-human.ics

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format html --output /private/zsentinel-operator.html

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-evidence.json

python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format text --output /private/zsentinel-owner.txt
```

**Bediening en grenzen.** Maak zelf vooraf een alleen voor de bevoegde operator
toegankelijke doelmap. Gebruik telkens een nog niet bestaande bestandsnaam;
dezelfde naam opnieuw gebruiken of een symlink overschrijven wordt geweigerd.
Met `--output` is er geen ruwe reportdump op stdout. De gekozen `--format`
en extensie moeten overeenkomen: `html/.html`, `json/.json`, `ics/.ics`,
`text/.txt`. `--format desk` blijft de aparte bestaande
`--desk-output NEW.html` route. Bestaande `--handoff-output` en
`--disk-hotspots-output` blijven expliciete afzonderlijke privé-uitvoer.
`--output` mag geen alias zijn van een bestaande invoer, ledger,
projectregistry, eerdere diskbron of ander rapportpad. De output-writer
weigert overschrijven en gebruikt een nieuwe `O_EXCL|O_NOFOLLOW` 0600 file
(maximaal 6 MiB). Een mislukte uitvoer wordt niet als geslaagde rapportage
gemeld; ledgerverwerking volgt pas na succesvolle rapportaanmaak.

**Kalenderimport is bewust menselijk.** ICS bevat uitsluitend uit de
niet-autorisende zSentinel-beslissing afgeleide P0/P1
`watch_alert_candidates` die aan menselijke incidenten zijn gebonden, met
originele beoordelingsdeadline (ook als die verstreken is). Het is **geen**
live agenda-integratie: het bestand wordt niet automatisch geïmporteerd of
verstuurd. De inhoud is vertrouwelijk; deel het alleen met de bevoegde eigenaar.
Een leeg ICS-bestand kan betekenen dat de begrensde steekproef geen menselijke
kandidaten bevat, nooit dat infrastructuur hersteld is.

**Releasegrens.** Deze opdracht schrijft geen SQLite/Notion-claim of
zCloud-workerqueue, herstart geen runner, retryt geen CI, autoriseert geen
merge/deploy en verleent geen nieuwe security- of systeemrechten. De
project-PR moet nog steeds door normale exact-HEAD verplichte CI, onafhankelijke
review, protected-main en runtime-release-eigenaarschap. Het bronresultaat op
een PR-branch bewijst geen live serverinstallatie.
