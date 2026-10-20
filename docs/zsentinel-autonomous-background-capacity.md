# zSentinel autonomous background capacity (20 Oct 2026)

This source extends the existing Ubuntu user-sidecar without launching new workers. An
**existing** `zcloud-background-priority.timer` evaluates live host pressure once a
minute, then changes **only** `fuckball-ai-coach.service`'s user-scope
`CPUQuota`/`CPUWeight`. All bands leave some CPU for training. The tuner does
not edit models or forecasts, manage FTMO trades, claim jobs, dispatch workflows,
move files or bypass a merge/deploy gate.

## Runtime behavior

| Observation | CPUQuota / CPUWeight | Meaning |
| --- | --- | --- |
| zCloud GitHub job worker observed, load > 1.35× affinity cores, <2 GiB MemAvailable, swap >=80% used, root unavailable >=90%, **or incomplete /proc, memory, load or root-disk telemetry** | 60% / 25 | Yield strongly, never assume unknown observations mean idle. |
| Other GitHub job worker observed, load >0.85× cores, <3 GiB MemAvailable, swap >=60% used, or root unavailable >=85% | 100% / 40 | Yield moderately. |
| None of the above | 175% / 70 | Allow bounded idle background training. |

A process is a job worker only if `/proc/*/comm` is `Runner.Worker` and
its command line is present. An idle listener alone is not a running job.
Disappearing processes during the scan are ignored. If a still-running process
cannot be inspected, or a Runner.Worker has no readable argv, source coverage
is incomplete and the conservative band applies. A failed whole-/proc
observation likewise cannot be treated as proof of idle runner capacity.
The `zcloud_runner_active` field is a **path marker**, not proof that an
AI browser worker or merge job is active. Load, memory, swap and root disk
are independent capacity indicators; CPU throttling does not actually free
swap, disk blocks or repair a runner.

On each timer invocation, the tuner reads the **actual** user-systemd
`CPUQuotaPerSecUSec`, `CPUWeight` and `ActiveState`, rather than assuming an
old saved JSON decision is still installed. If the active service already
has the selected values, no control write is necessary; otherwise the tuner
changes only the existing user-scope properties and reads them back before
claiming `applied_verified=true`. A failed systemctl update or read-back
never publishes a false verified decision. If the coach is inactive, the
tuner does not start it: it writes `unit_active=false` and
`applied_verified=false` for operator visibility. The read-only snapshot and
selected CPU policy are saved through a private atomic 0600 temporary file
under `$XDG_RUNTIME_DIR`; rename replaces the prior state. State stores only
numeric capacity and boolean runner observations, not process command lines,
full filesystem paths or credentials.

## Read-only decision preview

On a machine with access to the Python source, `python3
scripts/zsentinel_background_priority.py --preview` prints a single JSON
snapshot with root-disk/swap/memory/load observations, conservative mode,
recommended quota/weight and `"applied": false`. When a user systemd bus
is available, the preview additionally makes a **read-only** `systemctl
--user show` call for the current coach `unit_active`,
`current_quota_percent`, `current_weight` and
`recommendation_currently_applied`. Without a user bus it displays
`unit_observed=false` and null current values, never inventing an active
setting. It makes no service change or state-file write and does not require
Ubuntu UID 1000. The bare command remains restricted to that UID and only
changes the existing dedicated user unit. A preview's observed current
values **do not prove** this PR source was installed, nor does preview
validate runner recovery.


Voor operators zonder JSON is er daarnaast `python3 scripts/zsentinel_background_priority.py --preview --human`: een Nederlandstalig één-scherms overzicht van het CPU-advies, de concrete oorzaken (actieve GitHub-jobs, systeemload, swap en schijfruimte), huidige systemd-quota/gewicht waar uitleesbaar, en de expliciete melding dat de preview niets heeft gewijzigd. `--human` zonder `--preview` wordt geweigerd. Beide weergaven lezen alleen; geen periodieke controle wordt gestart.

## Why the CPU band was selected

The preview and private on-host state now include a bounded array of
machine-readable `reasons`: e.g. `zcloud-github-job-running`,
`high-system-load`, `high-swap-use`, and
`low-root-disk-headroom`. These describe independently observed host
pressure, **not** proof of a failing specific GitHub workflow. An incomplete
`/proc` or capacity read emits `incomplete-capacity-source` and chooses
the conservative band. This lets operators distinguish a real running
zCloud job from high host load or disk/swap pressure without exposing
process arguments, runner IDs, home paths or command-line credentials.

The tuner independently reconciles with the actual `systemctl --user
show` values on every authorized oneshot invocation. It does not rely
on old `zcloud-background-priority.json` data for admission. A unit that
was manually changed after the previous observation is re-evaluated on
the next invocation, while an inactive unit remains untouched (but has
an explicit unverified observation in private state).

## Read-only GitHub runner service overview

For a one-command private operator view, use `python3
scripts/zsentinel_background_priority.py --preview --human --runners`.
The added `--runners` flag is **opt-in and preview-only**; the background
timer never inventories other services. It uses bounded, shell-free
`systemctl list-units` reads for at most 20 canonical
`actions.runner.<owner>.<runner>.service` names. Only failed units get
a bounded `systemctl show -p Result` read for familiar values such as
`oom-kill`, `exit-code` or `timeout`. No unit descriptions, arbitrary
command lines, GitHub credentials or process environment are returned.
An oversized list explicitly says the coverage is partial; if systemd is
unreachable, the output says the service state could not be observed.

An **active runner service is not proof of an executing GitHub job**,
correct GitHub labels, successful builds or a healthy deploy. Likewise,
`inactive/dead` does not mean that a service is crashed: it may have
stopped normally. The optional overview deliberately provides no restart,
enable, stop, dispatch, write or repair action. Recovery of OOM-failed
project runners remains subject to the specific service owner, memory
headroom and existing production gates.

## Privé offline herstelwerkbank

Met de bestaande one-shot preview kan een bevoegde operator een statisch
Nederlandstalig HTML-overzicht genereren zonder een externe dashboarddienst:

`python3 scripts/zsentinel_background_priority.py --preview --human --report "$HOME/private-recovery/zsentinel-20261020.html"`

De bestemmingsmap moet **al bestaan** en door de uitvoerende gebruiker
beschrijfbaar zijn. Het opgegeven `.html`-bestand moet **nieuw** zijn: aanmaken
is exclusief (`O_CREAT|O_EXCL|O_NOFOLLOW`), bestandstoegang is `0600`,
de laatste doelmapcomponent wordt zonder symlink geopend. Het programma
overschrijft nooit een bestaand HTML-rapport en logt de privébestandsnaam
niet naar stdout of een portfolio-feed. Een ongeldige naam, bestaand bestand,
ontoegankelijke doelmap of onvolledige write geeft een fout; gedeeltelijk
geschreven bestanden worden weer verwijderd.

De privé herstelwerkbank geeft ook een **geprioriteerde herstelvolgorde**:
bij onvoldoende of hoge RAM/swap/disk/load-capaciteit krijgt eerst
capaciteitsveiligheid prioriteit en adviseert hij geen extra runner-start.
Daarna volgen afzonderlijk het bevoegde onderzoek van OOM-gefaalde services,
eigenaarcontrole voor bewust mogelijk gestopte services en onafhankelijke
GitHub-toewijzingscontrole voor actieve services. Onvolledige bronnen krijgen
een aparte waarschuwing. Dit is tekstadvies, geen uitvoerbare herstelactie
of bevoegdheidsverlening.

De rapportbron komt uit dezelfde **eenmalige** read-only host- en
service-observaties als de preview. Een afzonderlijke zelfstandige ingang
`python3 scripts/zsentinel_runner_report.py --output NEW.html`
biedt dezelfde HTML-werkbank voor een losse beheeractie. Het rapport bevat
hostcapaciteit, CPU-advies versus huidige coach-instellingen en maximaal
20 canonieke runner-servicestatussen met veilige vervolgadviezen bij
`failed/oom-kill`, gestopt of actief. De status van een uitvoerende GitHub-job
blijft onafhankelijk van een actieve listener.

Het HTML bevat **geen JavaScript of externe assets**, heeft een beperkende
Content Security Policy en is uitsluitend privé beschikbaar. Hiermee worden
geen logs, GitHub credentials, gebruikersprofielen of proces-argv opgeslagen.
Het bestand is een momentopname; de tool claimt geen CI-success, main-merge,
recovered listener, vrijgevallen Notion-claim of live productieintegratie.
De optionele `--report`-modus draait nooit vanuit de bestaande
systemd-timer en voert geen nieuwe `systemctl set-property`-actie uit.

## Privé T0/T1-bronmomenten voor runner-status

Wanneer incidentonderzoek over meerdere tijdstippen gaat, slaat de operator
twee losse, create-only momentopnamen op. Eén opdracht met de bestaande
`--preview` verzamelt dezelfde lokale host-, CPU- en systemd-bronnen als
het private HTML-overzicht:

```bash
python3 scripts/zsentinel_background_priority.py --preview --snapshot "$HOME/private-recovery/T0.json"
# Na een volgende echte observatie:
python3 scripts/zsentinel_background_priority.py --preview --snapshot "$HOME/private-recovery/T1.json" --previous "$HOME/private-recovery/T0.json"
```

De alternatieve standalone ingang is
`python3 scripts/zsentinel_runner_snapshot.py --output NEW.json --previous OLD.json`.
Elk doel is een **nieuw** bestand (`0600`, `O_CREAT|O_EXCL|O_NOFOLLOW`),
met maximaal 128 KiB. Bestaande snapshots worden niet aangepast.
Voor T0/T1 is het vorige bestand een door dezelfde Unix-eigenaar beheerde
reguliere file zonder groep-/wereldrechten en zonder finale symlink.
De vergelij­king vereist schema `zsentinel.runner-observation.v1`,
**dezelfde host**, strikt oplopende UTC-bronmomenten en maximaal 72 uur
verschil. Onvolledige of afgekorte runner-bronnen blijven zichtbaar als
`complete_service_coverage=false`.

De `comparison.transitions` onderscheiden `newly_observed`,
`no_longer_observed` en `service_state_changed`. **Zelfs een overgang van
`failed` naar `active` is géén bewijs dat een GitHub-job, PR of deploy
hersteld is**; de velden `github_job_recovered` en
`production_restored` blijven daarom `null`.
Iedere statusovergang krijgt daarnaast een stabiele, privé SHA-256-afgeleide `incident_key` (24 hextekens) over host, canonical service-id en waargenomen vóór/na-toestand. Herhaalde identieke overgangen kunnen hierdoor als hetzelfde type incident worden herkend, zonder een oude T0-bron als huidige vrijgave of herstelbewijs te accepteren. De vergelijking bevat ook de gemeten `elapsed_seconds` en per overgang `action_authorized=false`. Dit is een deduplicatiehint, geen vertrouwenshandtekening.

Iedere bron draagt expliciet `claim_authorized=false`,
`service_restart_authorized=false`, `merge_authorized=false` en
`deploy_authorized=false`. Een oude bron wordt nooit als vrijgave
voor nieuwe werkzaamheden of automatische recovery geïnterpreteerd.
Zonder `--snapshot` kan `--previous` niet gebruikt worden; `--report`
en `--snapshot` zijn bewust afzonderlijke create-only uitvoeracties.

## Historische HTML: twee opgeslagen bronnen zonder VPS-toegang

De privé werkbank ondersteunt nu **offline herweergave** van twee eerder
vastgelegde runnerbronmomenten. Op dezelfde Unix-gebruikersaccount als de
privé snapshotbestanden kan de operator uitvoeren:

```bash
python3 scripts/zsentinel_runner_report.py \
  --t0 "$HOME/private-recovery/T0.json" \
  --t1 "$HOME/private-recovery/T1.json" \
  --output "$HOME/private-recovery/vergelijking.html"
```

Deze modus leest uitsluitend twee bestaande eigenaar-privé JSON-bronnen
en maakt één **nieuw** 0600 HTML-rapport. Er worden **geen actuele
systemd-/proc-/GitHub-metingen uitgevoerd**, geen achtergrondservice
geactiveerd en geen gegevens naar een netwerkendpoint verzonden. De
bestaande vergelijking weigert een andere host, verkeerde bronvolgorde,
onbekend schema of een verschil groter dan 72 uur.

Het HTML toont expliciet de T0- en T1-tijd, dekkingsgraad, waargenomen
statusovergangen en stabiele incidentcodes, plus de bij T1 **opgeslagen**
capaciteitswaarden en CPU-aanbeveling. Voor nieuwe bronbestanden bewaart
zSentinel ook de waargenomen Linux-root-mount-identiteit en het totale
volume. Het historische overzicht toont verschillen in beschikbare RAM,
swapdruk en load, en toont een schijfdelta **uitsluitend** wanneer zowel
het mount-device als de totale volumeomvang op T0 en T1 overeenkomen.
Ontbrekende oude mount-identiteit of een veranderd volume geeft
`niet vergelijkbaar`, niet een verzonnen vrijeruimtewinst.
Alle verschillen zijn begrensde momentopnames, geen bewijs van veilige
bestandsverwijdering, runnerherstel of nieuwe training.
Een vorige bron zonder
opgeslagen coachinstellingen blijft **onbekend**, niet stiekem live
nagevraagd. `--previous T0.json` zonder `--t0/--t1` is de aparte
**actuele**, read-only systeemobservatie die het verschil met T0 in het
nieuwe privé HTML-bestand plaatst. Beide modi zijn afzonderlijk van
`--snapshot` en van productieactivatie.

## Bootbewuste runnerherstelvergelijking (privé T0/T1)

Nieuwe `zsentinel_runner_snapshot.py`-bonnen slaan vanaf deze bronversie
`boot_fingerprint` op: een afgekorte SHA-256 van de Linux `boot_id`
(geen letterlijke boot-UUID; uitsluitend binnen het eigenaar-private JSON).
Voor vergelijkingen op hetzelfde hostlabel worden twee bronmomenten nu
onderverdeeld in drie expliciete gevallen:

- `same_boot_verified=true`: opstartsessie van beide metingen bevestigd;
  niet hetzelfde als een bevestigde GitHub-job, runner-claim of CI.
- `host_reboot_observed=true`: Linux heeft tussen T0/T1 aantoonbaar een
  andere opstartsessie; herstelde `active` services zijn geen bewijs
  dat oorspronkelijke worker/CI na de reboot doorging.
- Beide onbekend: ten minste één bron mist een geldig bootbewijs, bijvoorbeeld
  oudere v1-bronnen. Dit blijft **onbekend**, niet stilzwijgend dezelfde boot.

De CLI JSON en het privé offline HTML-vergelijkingsrapport tonen dit
onderscheid. Als T0 of T1 door bronfouten of de inventarislimiet onvolledig
was, krijgt een alleen in één bron zichtbare service expliciet de aanduiding
`previous_source_gap` respectievelijk `current_source_gap` in plaats van
ten onrechte `newly_observed` of `no_longer_observed`. Een verdwenen
inventarisregel bewijst geen gestopte of herstelde service.
Ook de gededupliceerde service-overgangen binden aan de
beschikbare opstartsessie-identiteiten; na reboot wordt niet per ongeluk
de oude incidentcode als hetzelfde meetmoment behandeld. V1 blijft leesbaar
en is bewust niet achteraf voorzien van verzonnen bootbewijs.

Het betreft uitsluitend een read-only systeemmeting bij het maken van een
nieuw privé bronmoment; geen extra systeembrede installatie, reset,
workerstart, GitHub-dispatch, betaling, merge of deploy. Toestemming en
exacte-head CI/review blijven onverkort van toepassing.

## Samenhangend privé operatorpakket (geen installatie)

Een normale toekomstige runtime-installatie van het basis CPU-prioriteitscript
is **niet automatisch** een installatie van de optionele HTML-/JSON-tools.
Die laatste importeren `zsentinel_background_priority.py`,
`zsentinel_runner_report.py` en `zsentinel_runner_snapshot.py` uit
dezelfde Python-modulemap. Alleen het hoofdscript overzetten veroorzaakt
`ModuleNotFoundError` in de optionele operatorfuncties.

Een bevoegde operator kan na beoordeling van **dezelfde exacte commit**
de drie samenhangende modules bundelen, zonder VPS/runner/timer/service
te starten, te verplaatsen of te updaten:

```bash
python3 scripts/zsentinel_operator_export.py \
  --source-dir scripts \
  --output-dir "$HOME/private-recovery/zsentinel-operator-bron"
python3 scripts/zsentinel_operator_export.py \
  --verify-dir "$HOME/private-recovery/zsentinel-operator-bron"
```

De doelbovenmap, bijvoorbeeld `$HOME/private-recovery`, moet
**vooraf bestaan**, eigendom zijn van dezelfde Unix-gebruiker en zelf
privé (`0700`) zijn. Anders weigert de exporter het pakket vóór de
bestemmingsmap wordt gemaakt. Maak deze bovenmap alleen lokaal en met
toestemming van de runtime-eigenaar; export doet dit nooit automatisch.

De eerste opdracht maakt uitsluitend een **nieuwe** map met mode `0700`
binnen de reeds geopende privé bovenmap (zonder symlink en via directory-FD)
en drie Python-modules (ieder `0600`), `LEESMIJ.txt` en
`MANIFEST.json` met per module SHA-256 en byteomvang **én een afzonderlijke
SHA-256 van de operatorinstructies**. Bij bestaande
bestemming wordt niet overschreven. De tweede opdracht leest eigenaar-privé
bronbestanden en verifieert volledigheid en bytevingerafdrukken, zonder
modules uit te voeren of services aan te raken. De bijbehorende
bronmap moet bestaan en de benodigde bestanden bevatten; een ontbrekende
module stopt de export voordat de doelmap wordt aangemaakt. De bundel
bevat bewust **geen** systemd-unit, runner-script, secrets of autorisatietoken.

De controle bewijst alleen dat de overgebrachte bronbestanden overeenkomen
met het opgeslagen **pakketmanifest**, **niet** dat de bron al een
onafhankelijk goedgekeurde GitHub-release is. Het manifest bevat daarom
`external_source_verified=false`, `systemd_installed=false`,
`workflow_dispatched=false` en `production_released=false`.
Controleer daarnaast zelf de exacte GitHub-commit, verplichte CI,
onafhankelijke review en runtime-eigenaarstoestemming. Deze bundel kopieert
geen bestanden naar `~/.local/bin`, `~/.config/systemd/user/`,
of enige productieroute. Bij daadwerkelijk geautoriseerde installatie
moet de runtime-eigenaar de drie modules in één Python-modulemap beheren,
zonder bestaande timer of coach te dupliceren.

## Operator installation and release boundaries

Code files:
- `scripts/zsentinel_background_priority.py`
- `scripts/zsentinel_runner_report.py` (optionele privé HTML)
- `scripts/zsentinel_runner_snapshot.py` (optionele privé JSON/T0-T1)
- `scripts/zsentinel_operator_export.py` (alleen lokale export en controle)
- `ops/systemd/user/zcloud-background-priority.service`
- `ops/systemd/user/zcloud-background-priority.timer`

On the targeted Ubuntu VPS user session, only an authorized runtime owner
may copy the reviewed Python script to
`/home/ubuntu/.local/bin/zcloud-background-priority.py` and the two unit
files to `~/.config/systemd/user/`. The runtime owner then reloads their
**user** manager, enables/starts the timer and invokes the oneshot service
once. There is no privileged install path or unattended production rollout
from this PR. Preserve the existing coach's always-restart policy, 3 GiB
memory maximum, Nice=10, disk-space reservation and model promotion gates.
Do not start duplicate sidecars.

This implementation adds conservative disk/swap/load admission to a
previously installed independent user-sidecar, but **source being committed
does not prove the VPS's installed copy matches this branch**. Installing
these changes requires ordinary protected-main, review, exact-head CI and
release-owner authorization. Self-hosted runner incident #1326, Actions
billing issue #1523, and project claim ownership remain separate.

The FTMO autonomous systemd service is root-managed and outside this
sidecar's authority. Its own owner must resolve the reported inaccessible
`/home/ubuntu` paths through an authorized privileged release; no blanket
permissions or zCloud SQLite write access should be granted to work around it.
