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

## Eenzelfde bronmoment voor cockpit én agenda

Met `--agenda-output` kun je naast een gekozen normale operatorweergave in
één run een afzonderlijke privé-ICS voor de bevoegde incidenteneigenaar
aanmaken. Hierdoor hoeven GitHub- en SQLite-bronnen niet opnieuw gelezen te
worden voor alleen het kalenderbestand; beide resultaten zijn gebaseerd op
dezelfde routerbeslissing. Iedere bestandsnaam moet uniek en nieuw zijn.

```bash
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format html --output /private/zsentinel-operator.html \
  --agenda-output /private/zsentinel-owner.ics
```

Je kunt het HTML-bord lokaal openen en de ICS afzonderlijk in een
kalenderprogramma **handmatig** importeren. Het kalenderbestand bevat geen
bevestiging van een actieve eigenaar, verzonden notificatie of GitHub-merge.
`--agenda-output` werkt met de normale cockpitformaten, inclusief
`--format desk --desk-output ...`. Bij `--format ics` is die optie niet nodig:
gebruik `--output NEW.ics`. De uitvoerbestanden mogen geen bronbestanden,
elkaar, een queue-ledger of een andere privé-uitvoer overschrijven.

Als een van meerdere afzonderlijke bestandsaanmaken faalt, kan een eerder
aangemaakt privérapport behouden blijven. De CLI meldt de fout en verzint
nooit een complete transactie of geslaagde menselijke kalenderimport.

## Verifieerbaar historisch bronmoment in de agenda

Iedere ICS-export bewaart `X-ZSENTINEL-SOURCE-UTC` in de kalenderkop.
Elk menselijk actie-item toont daarnaast het **daadwerkelijke
observatietijdstip van de oorspronkelijke routerbeslissing**, niet de tijd
waarop de kalender opnieuw werd opgeslagen. Een bron uit de toekomst of met
`ci_retried=true` wordt geweigerd. De kalender bevat
`X-ZSENTINEL-NO-AUTHORITY:TRUE` om vast te leggen dat een ontvangen
kalenderitem geen runner-, release-, claim- of mergebevoegdheid is.
Dit bronmoment kan historisch zijn; controleer issue, eigenaar en gates
altijd opnieuw voordat een menselijke actie volgt.
