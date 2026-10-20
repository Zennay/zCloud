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

## Later opnieuw openen zonder netwerk of queue-herlezing

Nieuw: `scripts/zsentinel_saved_operator_replay.py`. Een eerder met
`--format json --output /private/source.json` opgeslagen cockpitbron
kan maximaal 30 dagen later opnieuw worden weergegeven, **zonder GitHub,
Notion, VPS, SQLite, runner of andere externe provider te benaderen**.
Bron en bestemming worden expliciet gekozen. Alleen een regulier
enkelvoudig owner-owned 0600 JSON-bestand (max. 6 MiB) wordt geaccepteerd;
de bronreader gebruikt dezelfde no-follow/same-inode/duplicate-key-weigering
als de bestaande router. Autoriserende/muterende bronvlaggen en onbekende
broncontext worden geweigerd. De bestaande daadwerkelijke operatorrenderers
worden hergebruikt, inclusief de gebonden menselijke agenda.

```bash
python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format html \
  --output /private/historisch-bord.html

python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format ics \
  --output /private/historische-mensenagenda.ics

python3 scripts/zsentinel_saved_operator_replay.py \
  --snapshot /private/source.json --format md \
  --output /private/historische-handoff.md
```

Daarnaast zijn `--format text --output NIEUW.txt` en
`--format desk --output NIEUW.html` beschikbaar. De HTML- en
tekst-/Markdown-overdracht bevatten een zichtbare historische
bronwaarschuwing; ICS bewaart het daadwerkelijke originele bronmoment.
Ook wanneer het opgeslagen bestand gisteren is gemaakt, ontstaat
**geen nieuwe CI- of eigenaarswaarneming**. Alleen de operator kan de
eventuele oude herinnering bewust importeren. Source-only branchfunctie:
geen scheduler, claim, auto-deploy of live service-activering.

## Historische P0/P1-incidenttijdlijn (2–8 echte bronmomenten)

De zelfstandige productmodule
`scripts/zsentinel_decision_timeline.py` vergelijkt twee tot acht
eigenaar-private, chronologisch opeenvolgende **live-cockpit JSON-rapporten**.
Het hergebruikt dezelfde 0600/no-follow/duplicate-key-admissie als de
bestaande offline replay; originele bestanden worden niet gewijzigd. De
uitvoer is een nieuw bestand, nooit een overschreven bron.

```bash
# Maak bronbestanden op verschillende momenten met de bestaande cockpit:
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-t0.json
# (Apart later uitgevoerde bronobservatie; geen achtergrondtaak ingepland.)
python3 scripts/zsentinel_live_cockpit.py --github-only --project cloud \
  --format json --output /private/zsentinel-t1.json

# Later op hetzelfde apparaat, ZONDER een provider te raadplegen:
python3 scripts/zsentinel_decision_timeline.py \
  --snapshot /private/zsentinel-t0.json \
  --snapshot /private/zsentinel-t1.json \
  --format html --output /private/historische-incidenten.html
```

Formaten: `html` (interactieve offline filters voor urgentie, aanwezigheid,
menselijke actie en zoeken), `json` (gebonden bron- en incidentmomenten),
`md` (privé overdracht) en `text`. De tool leest alleen eerder
aangemaakte privébestanden en exporteert exclusief nieuwe
`.html/.json/.md/.txt`-bestanden. Maximaal 8 bronnen, 30 dagen per
bron, 200 incidenten per moment, 1600 unieke incidenten intern en
400 zichtbare kaarten (eventuele afkapping expliciet). Bestaande
exact-incidentidentiteit is nodig voor herhaling; hetzelfde ID bij een
ander project/probleem wordt geweigerd.

**Bronwaarheid:** P0 blijft historisch urgent als een latere bron P1
ziet. Een tijdelijk niet-bemonsterd incident dat daarna terugkeert
krijgt de categorie *bronhiaat/heropgemerkt*, **geen** veronderstelde
tussentijdse oplossing. Ontbrekend in de laatste steekproef betekent
NOOIT opgelost; de CLI houdt zulke incidenten als menselijke
reconciliatiekandidaten. Het oorspronkelijke bronmoment, chronologische
vensters, genormaliseerde SHA-256-identiteit, waargenomen P0/P1-cijfers,
menselijke opvolgacties en onbekende eigenaarstoestand blijven zichtbaar.

Dit is een **operatorproduct**, niet een nieuwe Quality Validation- of
schedulerfunctie. Het genereert geen providerrequests, eigenaarsclaims,
GitHub merges, CI-retries, runneracties, betalingen of productie-deploys.
Een opgeslagen waarneming is geen actueel CI- of reviewbewijs.

### Privé menselijke actieoverdracht vanuit de tijdlijn

De offline HTML-tijdlijn heeft lokale filters en een aparte **Bekijk
privéoverdracht**-voorvertoning. Alleen geselecteerde, historisch als
menselijke P0/P1 geobserveerde incidentkaarten worden in de Markdown
opgenomen. Na expliciete controle kan de operator op **Download geselecteerd
Markdown** klikken. Een gewijzigd project-/zoek-/prioriteitsfilter maakt de
oude voorvertoning ongeldig; de download wordt pas na een nieuwe preview
beschikbaar. De overdracht bevat originele bronvensters en bron-SHA-256,
incidentidentiteit, oorspronkelijke opvolgdeadline, eigenaarlabel met
onbevestigde status, bronhiaten en de waarschuwing dat niet opnieuw gezien
**geen bewezen herstel** is. Er wordt niets verstuurd, geclaimd, gemerged,
als geslaagd afgetekend of automatisch in een agenda geïmporteerd.

### Veilig doelpad en bestandsaanmaak

De gedeelde operator-exportwriter is nu ook op de **bestaande doelmap**
vergrendeld. Maak die map vooraf onder het eigen operatoraccount en voorkom
groep-/wereldschrijfrechten (bijvoorbeeld een eigen privémap met `chmod 700`).
De writer weigert ontbrekende, vreemde of groep-/wereldschrijfbare mappen,
opent de map met `O_DIRECTORY|O_NOFOLLOW`, vergelijkt de werkelijke
geopende directory-inode met de vooraf gelezen eigenaargegevens, en maakt
het bestand uitsluitend via directory-geankerde `openat` met
`O_EXCL|O_NOFOLLOW`. Bestand blijft exact 0600. Ook bij een schijffout
wordt alleen de eigen nieuw gemaakte inode opgeruimd; een vervangen
bestandsnaam wordt nooit blind verwijderd. Dit is uitsluitend opslag voor
uitdrukkelijk gevraagde privébestanden en geeft geen rechten voor andere
VPS-paden, runner- of productieacties.
