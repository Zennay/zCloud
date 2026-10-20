# zSentinel · privé workercontinuïteit zonder control-plane writes

## Wat is geleverd

Eén bestaand zSentinel workerobservatiepad produceert nu direct een
zelfstandige **Nederlandstalige HTML-cockpit** of **Markdown-handoff** op
basis van de reeds geïndexeerde `runner_events` metadata en de gekozen
worker/queue-indeling in de bestaande SQLite-database. Er is **geen**
zCloud HTTP-status-GET (die op bepaalde endpoints de SQLite kan
reconciliëren), GitHub-API, Notion-call, nieuw schedulerpad, timer,
runnerrestart of claimsysteemwrite nodig. De import draait binnen
`mode=ro` met `PRAGMA query_only=ON`, en vereist de bestaande
`runner_events_project_worker_id` en
`runner_events_project_worker_event_id` indexen.

Per project wordt de bestaande limiet van 8 workers gebruikt, maximaal
3 projectselecties. Per worker toont de cockpit hooguit twaalf recente
eventtypen met leeftijd en indicatoren of de gebeurtenis na de laatste
zichtbare `prompt-sent` plaatsvond. De raadpleging toont niet de
workerprompt, foutinhoud, chat-URL, conversation-ID, token, originele
queue-ID of raw assistant-output. Een actie wordt nooit zelf uitgevoerd.

## Direct lokaal gebruiken

Een bevoegde operator kiest een **al bestaand**, zelf beheerd privédossier
(mode 0700), met toegestane read-only toegang tot de bestaande
`history.db`. Bijvoorbeeld:

```sh
python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud \
  --format html \
  --output /private/authorized/zsentinel-workers.html

python3 scripts/zsentinel_worker_progress_observer.py \
  --database /private/authorized/history.db \
  --project cloud \
  --format markdown \
  --output /private/authorized/worker-handoff.md
```

Alternatief `--format html` of `--format markdown` zonder `--output`
schrijft uitsluitend naar stdout. `--format json` en `--format text`
blijven compatibel. Voor `--output` is het pad expliciet en de
bestandsextensie passend; het bestand wordt alleen nieuw aangemaakt met
rechten 0600. Bestaande dossiers, symlinks, DB's, cursors en rapporten
worden niet overschreven. De bovenliggende map moet bestaan en 0700
zijn, eigendom van de uitvoerende gebruiker. Bij weigering wordt
**niet** teruggevallen op een onveilige schrijfroute.

Het HTML-resultaat is volledig **offline**, zonder scripts en externe
assets en met een restrictieve CSP. Het heeft bovenaan een
actie-inbox met concreet advies voor de bevoegde owner en daarna
uitklapbare historie; Markdown heeft dezelfde actievolgorde en
context. De operator kan documenten meenemen naar een menselijke review,
maar ze vervangen geen autorisatie of actuele claimcontrole.

## Betrouwbaarheid, zichtbaarheid en grenzen

Een recente browserblokkade zonder later activiteitssignaal verdient
andere aandacht dan een blokkade waarop een nieuwe `generation-progress`
of `generation-finished` volgt. Geen van beide bewijst **werkelijke
productdelivery**. Een browser-heartbeat kan een oudere progressregel
overschrijven; de read-only bron reconstrueert hoogstens een begrensde
eventtijdlijn, maar heeft **geen geverifieerd claim-epoch eventbewijs**.
Dit is daarom een operatorverbetering, **niet de control-plane reparatie
van issue #1342**.

De 12-eventselectie is geen complete historische inventaris. Het rapport
toont brontijd en meldt duidelijk wanneer deze ouder dan 10 minuten is.
Een eerder opgeslagen v1-bronmoment blijft leesbaar; ontbrekende nieuwe
velden worden als `ONBEKEND` behandeld, niet als nul blokkades.

De volgende velden blijven `false`: `owner_claim_verified`,
`worker_resumed_verified`, `repair_authorized`,
`recovery_authorized`, `continuation_verified`,
`actions_performed`, `database_write_authorized`.
Het dossier bevat geen retry-key, geen commandolijst en geen
`approve`-/`resume`-/`force`-knoppen. Menselijke approval, originele
eigendom, verplichte CI, VPS-rechten en beveiligingsgates mogen
hierdoor niet worden omzeild.

## Integratie

Deze uitbreiding zit op de bestaande zSentinel hoofd-PR
[#1563](https://github.com/Zennay/zCloud/pull/1563) en raakt alleen
`scripts/zsentinel_worker_progress_observer.py` en de nieuwe renderer
`scripts/zsentinel_worker_handoff.py`. De live cockpit behoudt zijn
bestaande component `worker_progress_html` en dezelfde read-only
databron. Er zijn geen QA-, regressie- of testsuites geschreven of
gedraaid. Een compile-check bewijst alleen geldige broncode, geen
werkende VPS-ingreep of vrijgegeven productie-integratie.
