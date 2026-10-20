# Portfolio merge-first operator helper (zSentinel / zCloud)

The existing `scripts/portfolio_merge_ready.py` is an **operator tool**, not an unattended merge bot. It lists selected GitHub PR receipts without changing workers, SQLite or repositories unless an explicit, separately authorized merge is requested.

## Merge-first cockpit: cross-project offline worklist

The new `scripts/zsentinel_merge_first_board.py` combines multiple **explicit existing** product PRs into a bounded, read-only operator worklist. It does not discover new owners, claim work, approve PRs, merge, restart runners or deploy. Its canonical repository allowlist comes from `projects.json`.

```sh
python3 scripts/zsentinel_merge_first_board.py \
  --pr Zennay/zCloud#1571 --pr Zennay/Supa#123 \
  --format text --output /private/new-zsentinel-merge-board.html
```

Alternatively, discover up to four recent product PRs (including owner-controlled drafts) per canonical project without knowing their numbers:

```sh
python3 scripts/zsentinel_merge_first_board.py \
  --project cloud --project supa --project haxlab \
  --max-per-project 4 --output /private/new-merge-first-worklist.html
```

This **read-only discovery** prioritizes P0/fix-labelled product titles within each project's most recent 100 open PRs. The result is a triage hint, not a claim or a complete backlog enumeration. Draft PRs are **included as existing unfinished product work**, with a visible DRAFT / owner-action-required status; they are never treated as merge-ready and never promoted by this read-only tool. Ready PRs lead equally prioritized drafts. Child and QA-only PRs are not automatically selected. If a repository fails or has 100+ open PRs, it is separately flagged as incomplete; the other projects remain visible. A specific older PR is inspected with `--pr` and does not depend on the recent discovery window. The discovery budget is 3 list GETs + up to 72 exact PR/check/review/workflow GETs. The workbench also stops new reads after a 120-second monotonic source window (an already started bounded GitHub GET may finish afterwards); no unlimited background polling. An empty selection explicitly says it is not evidence that the backlog is empty.

The output is a new, exclusive-create, private (0600) responsive standalone HTML file. Without `--output` there is no local write. JSON output is available using `--format json`. The HTML contains no JavaScript, network fetches, tokens, worker credentials or queue identifiers. Never point output at a shared web directory. Existing reports are never overwritten.

The board is intentionally bounded: at most 12 selected PRs, 6 per canonical repository, 3 repositories and 84 GitHub GETs. All PRs are read again after their observed reviews and check-runs; an unstable head/base/draft becomes `SOURCE_MOVED`, not a false pass. A missing or oversized evidence page is UNKNOWN. Exact-head independent approvals distinguish changes requested from no approval; recent Check Runs, legacy statuses and all observed exact-head GitHub Actions workflow runs are checked separately. Queued, pending, incomplete, or failed current applicable workflows cannot be reported as green; optional skipped/neutral completions are tolerated only alongside positive completed workflow evidence. A successful observed set is labeled **OBSERVED_GREEN_ONLY** because the board cannot grant required-context coverage or protected-branch authorization.

Operators can quickly select an independent unblocked product PR while other PRs wait on reviewer, CI, claim or release rights. For any actual merge, the **separate** single-PR `portfolio_merge_ready.py --merge` owner-claim preconditions and GitHub server-side protection still apply. Nothing in the board's HTML or JSON is a merge permit; `main_merged=false` and `live_verified=false` remain conservative snapshot indicators, not assertions about the actual GitHub state.

## Issue-first productrecovery (open issues → bestaande product-PR)

`scripts/zsentinel_issue_first_board.py` is een tweede, zelfstandige operator-ingang die **echte open P0/P1 issues** aan bestaande open product-PR's koppelt voordat nieuw werk zou worden gestart. Ook **draft-product-PR's** worden als bestaand potentieel werk getoond, met status `DRAFT` en zonder automatische draft→review-overgang, reviewbewijs, claimrecht of mergeautorisatie. QA-only PR's worden nog steeds niet als productroute geselecteerd. Hij hergebruikt de exacte-head checks en de private HTML-renderer van de merge-first werkbak, en opent geen claims of PRs.

```sh
python3 scripts/zsentinel_issue_first_board.py --project cloud --project supa \
  --format text --output /private/zsentinel-priority.html

python3 scripts/zsentinel_issue_first_board.py --project cloud --format json
```

- Maximaal 3 canonieke projectrepositories, de 100 meest recente open PRs én 100 meest recente open GitHub issues per repo, en maximaal 4 geselecteerde bestaande niet-QA product-PRs per repo. Het gedeelde venster is maximaal **84 GitHub GETs / 120 seconden**. Geen web/browsercalls nadat de private HTML is opgeslagen.
- Uit PR-bodytekst worden expliciete `fixes #123`, `closes #123`, `resolves #123` of `refs #123`-achtige verwijzingen herkend. De huidige bron herkent ook expliciete **canonieke links naar dezelfde repository** zoals `https://github.com/Zennay/zCloud/issues/1326` of `Zennay/zCloud#1326`, in titel of body. Verwijzingen naar een andere repository gelden nooit als een lokaal issueverband, ook niet wanneer het nummer hetzelfde is. Deze links zijn alleen navigatiehints, geen bewijs van opgelost werk. Ook een **expliciete PR-titelverwijzing** zoals `feat(...): herstel worker (#1326)` wordt aan een daadwerkelijk geobserveerd open issue van **dezelfde repository** gekoppeld. Beide zijn uitsluitend koppelsignalen, nooit bewezen fixes of eigenaarschapsverklaringen. Er worden geen rechtsgeldige fixes, goedkeuringen of GitHub issue-relaties aangemaakt. Een link is geen bewijs dat de code het issue oplost.
- Bestaande PRs met een geobserveerde **P0**, daarna **P1** issueverwijzing worden eerst getoond; binnen zo'n groep geldt bronvolgorde/titelprioriteit. Actuele GitHub PR-HEAD wordt opnieuw opgevraagd vóór de issueverwijzing wordt getoond; gewijzigde of onbereikbare bron → geen issue-linkbewijs. Onbekende ownerclaims blijven `UNKNOWN_NOT_OWNED`.
- De werkbak toont daarnaast (max. 24) urgente open issues zonder waargenomen PR-bodyverwijzing, met klikbare issue-URL. Dat is **geen** bewering dat het werk onbezet is, want oude PRs buiten de steekproef, forks, commits zonder PR en actieve Notion-claims kunnen buiten beeld zijn.
- Een onbereikbare projectbron blokkeert alleen dat project en verschijnt als `SOURCE_UNAVAILABLE`; een volle bronpagina toont `SOURCE_WINDOW_100`. Prioritering volgt uitsluitend de beperkte waarneembare bronnen. Bij onzekerheid worden ontbrekende resultaten nooit stilzwijgend tot `DONE` omgezet.
- Alle resultaten zijn read-only; export naar een **nieuw** HTML-bestand is exclusief 0600 met nofollow/fsync. Niets publiceert naar GitHub, Notion, SQLite, workerpool of productie. De verplichte onafhankelijke review, exact-head CI, owner/mainwriterclaim en bestaande releaseprocedure blijven separaat.

### Gerichte P0/P1-reparatie zonder recent-PR-venster

Als een kritisch bekend issue niet in de 100 recent bijgewerkte PRs zichtbaar is, gebruik **dezelfde bestaande product-PR**, niet een nieuwe parallelle draft:

```sh
python3 scripts/zsentinel_issue_first_board.py --project cloud --issue 1326 \
  --format text --output /private/zsentinel-issue-1326.html
```

- `--issue` vereist **exact één** canoniek `--project` en een bestaand open issue-ID. Het leest het exacte GitHub-issue, doorzoekt native GitHub Search naar open PRs op basis van het nummer en controleert daarna de echte PR-body, GitHub-HEAD, huidige CI/legacy/workflows en review. Zo komen **oudere, nog open PRs** aan bod die in de snelle recent-PR-selectie ontbreken.
- De zoekresultaten zijn slechts een kandidatenlijst. Alleen expliciete brongebonden issueverwijzingen `fixes #N`, `refs #N` in de body, óf expliciete `#N` in de PR-titel op dezelfde repository worden gekoppeld. De oorspronkelijke én laatste titel en beschrijving moeten allebei ongewijzigd zijn tegenover de eerste huidige PR-bron. Maximaal 32 bekeken kandidaten en 4 gekoppelde product-PRs; overige zoekresultaten blijven expliciet `CANDIDATE_WINDOW_32`/UNKNOWN. De laatste exacte issue-opvraag moet nog steeds `open` melden. Een tussentijds gewijzigde PR-body **of titel** wordt geweigerd, ook als de Git commit ongewijzigd bleef.
- Alle bronnen en één privé offline HTML worden begrensd door hetzelfde totale 84-GET / 120s budget. De HTML toont het gerichte issue, zoekdekking, PRs met concrete next-gate en de onvolledigheidswaarschuwingen. Geen automatische issueclaim, workflowrestart, PR-merge, deploy of P0-statuswijziging.

### Rechtstreeks naar bekende bestaande PRs zonder zoekindex

Als een ouder P0/P1-issue al concrete bestaande product-PR-nummers heeft
(bijvoorbeeld uit Notion of een project-handoff), kan zSentinel deze **direct**
tegen de actuele canonieke GitHub PR-bron inspecteren, zonder de GitHub Search
index of paginering:

```sh
python3 scripts/zsentinel_issue_first_board.py \
  --project cloud --issue 1326 \
  --candidate-pr 1563 --candidate-pr 1571 \
  --format text --output /private/issue-1326-direct-prs.html
```

Dit werkt ook voor open **draft-product-PRs**. Per expliciet nummer leest de
operator de actuele PR-HEAD, titel/body en zo mogelijk de bestaande
checks/review/workflow-waarnemingen met dezelfde product-only filters.
Verwijzingen in PR-titel/body naar het gekozen issue zijn **alleen hints**;
een bekende PR zonder verifieerbare issueverwijzing blijft als PR zichtbaar,
maar krijgt `EXPLICIT_PR_NO_CURRENT_VERIFIED_REFERENCE`. De werkbak claimt
nooit dat de PR dit issue daadwerkelijk oplost, dat de branch veilig is om te
mergen of dat andere PRs niet bestaan.

Gebruik maximaal **tien unieke PR-nummers per opdracht**, bewust kleiner dan
de algemene limiet van twaalf rapporten: een volledig PR-gatebewijs kan per
nummer meerdere actuele GitHub GETs verbruiken en binnen het **bestaande
84-GET/120-secondenbronbudget** moet ruimte blijven om het issue zelf
na afloop opnieuw te bevestigen. Het offline HTML-overzicht bevat de
bekende geselecteerde PR-nummers en markeert de bron als
`EXPLICIT_PR_SELECTION_NOT_SEARCH`. Niet-beschikbare, gesloten, QA-only,
child- of andere niet-geschikte PRs worden expliciet als deelbronbeperking
zichtbaar, niet stilzwijgend als afwezig bewijs. Als het gedeelde GitHub-bronbudget
opraakt voordat alle opgegeven nummers zijn gelezen, bevat de JSON-lijst
`remaining_pr_numbers` uitsluitend de **nog niet opgevraagde** PR-nummers.
De HTML-werkbak en terminal tonen dezelfde concrete vervolgkeuze met links.
De operator kan deze nummers in een nieuwe `--candidate-pr`-opdracht
opgeven zonder ooit te suggereren dat het eerste overzicht compleet was.
Als het issue tussentijds van status, titel of prioriteit verandert, worden
de vermeende actuele issuekoppelingen ingetrokken.

`--candidate-pr` is uitsluitend voor één `--project` met `--issue`;
deze bronroute kan niet samen met `--candidate-page`, `--auto-pages`,
`--candidate-offset`, `--expected-page-sha` of
`--snapshot-output` worden gebruikt. Opgeslagen historische GitHub
Search-pagina's worden niet door de direct-PR-route geclaimd of gemengd.
Deze functie **leest uitsluitend**: geen claim, workflowdispatch, CI-retry,
automerge of productieactie.

### P0/P1 in één opdracht over meerdere bestaande PR-zoekpagina's

Bij een bekend kritisch issue kan de issue-first werkbak nu **zonder handmatige
tussenstappen** maximaal drie aaneengesloten GitHub Search-pagina's doorlopen
binnen het **bestaande gezamenlijke 84-GET / 120s-budget**:

```sh
python3 scripts/zsentinel_issue_first_board.py \
  --project cloud --issue 1326 --candidate-page 1 --auto-pages 3 \
  --max-linked-prs 12 --output /private/issue-1326-three-pages.html
```

**Meer volledige bestaande-PR-inventaris, zonder de externe limieten te verhogen.**
De gerichte issue-route kan nu met `--max-linked-prs 1..12` per **hele opdracht**
maximaal twaalf actuele bestaande product-PR-rapporten meenemen (standaard 12).
Een ingestelde limiet telt dus over alle automatisch gelezen pagina's; de
84-GET/120-secondenlimiet blijft ongewijzigd. Het minimum is 1 en er
worden nooit dertien of meer rapporten gepubliceerd. De hoeveelheid bekeken
zoekkandidaten is afzonderlijk zichtbaar en is geen garantie dat alle 20, 60
of 1000 mogelijke resultaten daadwerkelijk zijn onderzocht.

**Geen kandidaten binnen één pagina overslaan.** Bij het ingestelde PR-limiet,
een aflopende GitHub-GET-limiet of een vroeg gestopte pagina stopt de
automatische volgende-pagina-route bewust vóór er andere zoekpagina's gelezen worden. De
operator krijgt `next_candidate_offset` en `source_page_sha256` in de JSON,
tekst en HTML. Hervat op **dezelfde** zoekpagina met de geobserveerde offset en
fingerprint, bijvoorbeeld als het resultaat `next_candidate_offset=6` toont:

```sh
python3 scripts/zsentinel_issue_first_board.py \
  --project cloud --issue 1326 --candidate-page 1 \
  --candidate-offset 6 --expected-page-sha <exact-sha256-uit-vorig-rapport> \
  --output /private/issue-1326-page1-remainder.html
```

De SHA-256 is een *zelfcontrole van het volledige zoekvenster*: de geordende
zoekkandidaatnummers, GitHub Search `total_count` en de
`incomplete_results`-status. Dit is geen cryptografisch bewijs van
GitHub-herkomst. Een gewijzigde zoekvolgorde
weigert een onbetrouwbare offset en vraagt een verse inspectie vanaf offset 0.
Een offset die niet naar een werkelijk nog bestaand kandidaatnummer wijst,
wordt eveneens geweigerd. Het bronvenster toont voortaan exact de **daadwerkelijk
geïnspecteerde** positie, afzonderlijk van de gehele opgevraagde zoekpagina.
Vervolg-snapshots die midden in een pagina starten zijn niet toegestaan met
`--snapshot-output`: de offline bundelaar accepteert uitsluitend oorspronkelijke
privésnapshots vanaf pagina-offset nul en zal onvolledige pagina's als PARTIAL
weergeven. Deze hervatroute neemt geen merge, claim of dispatch over.

Voor elke pagina worden bestaande nog open PR-kandidaten via de actuele echte
GitHub-bron opnieuw gelezen, inclusief HEAD, checks, review en eventuele
expliciete issueverwijzingen. De output combineert maximaal twaalf rechtstreeks
geobserveerde PR-kaarten, toont de werkelijk bekeken pagina's en de volgende
nog te bekijken pagina. Veranderende zoektotalen, dubbele kandidaten, inmiddels
veranderde HEADs, ontbrekende issueverificatie, einde van bronbudget of
onbeschikbare latere pagina blijven expliciet **PARTIAL**; het onafhankelijke
PR- en repo-eigenaarschap wordt nooit afgeleid. Multi-page GitHub Search is
niet atomair, ook niet bij een aansluitende paginareeks. Iedere afzonderlijke gerichte zoekpagina leest het echte issue na afloop
nogmaals en vergelijkt niet alleen OPEN/nummer/identiteit, maar ook de
oorspronkelijke titel en P0/P1-prioriteit. Bij minimaal twee bekeken pagina's
wordt het issue bovendien na de laatste pagina opnieuw gelezen. Is het issue
niet meer aantoonbaar met dezelfde actuele urgentie open, of ontbreekt het
laatste bronbewijs binnen het budget, dan worden gekoppelde
issue-referenties ingetrokken en krijgt de zichtbare operatorweergave
`current_state=UNKNOWN`. Het historische prioriteitslabel in die weergave
is dan **niet actueel bewezen**, ook niet bij maar één zoekpagina. Veranderen
de oorspronkelijke prioriteit of titel van het issue tussen afzonderlijke
bronmomenten, dan blijven oude P0/P1-labels evenmin als geverifieerd staan.
Het programma
neemt geen claim, dispatch, review, merge of deploy over.

Een snapshot via `--snapshot-output` is bewust uitsluitend voor **één**
oorspronkelijke `--candidate-page`; voor meerdere historische pagina's zijn de
afzonderlijke privé-snapshots met de offline bundelaar hieronder nodig. De optie
`--auto-pages` geldt uitsluitend met `--issue`, is 1–3 en kan niet voorbij
zoekpagina 50 doorlopen. Onverwachte storingen en nog niet bekeken kandidaten op dezelfde pagina laten het reeds gelezen bronvenster zichtbaar met een onvolledigheidsmelding, niet als afgeleverd werk.

### Meerdere zoekpagina's bewaren en samenvoegen (offline)

Een gerichte P0/P1-zoekopdracht kan meer dan twintig bestaande PR-kandidaten hebben.
Bewaar afzonderlijke pagina's als **nieuwe private JSON-bronbestanden**; voeg ze
later lokaal samen zonder GitHub opnieuw te bevragen en zonder enige writer-actie:

```sh
python3 scripts/zsentinel_issue_first_board.py --project cloud --issue 1326 \
  --candidate-page 1 --snapshot-output /private/issue-1326-page1.json
python3 scripts/zsentinel_issue_first_board.py --project cloud --issue 1326 \
  --candidate-page 2 --snapshot-output /private/issue-1326-page2.json

python3 scripts/zsentinel_issue_page_bundle.py \
  --page /private/issue-1326-page1.json \
  --page /private/issue-1326-page2.json \
  --format html --output /private/issue-1326-combined.html
```

De bronbundelaar controleert vooraf dat **alle** pagina's één canoniek project,
dezelfde issue-URL en hetzelfde issue-ID beschrijven, met unieke paginanummers,
start-/eindvensters van exact 20 zoekkandidaten, consistente GitHub Search-totalen, exact bekeken PR-kandidaatnummers per pagina
en maximaal 10 minuten verschil tussen bronmomenten. Maximaal 50 pagina's,
1 MiB per bestand, 8 MiB totaal; de sourcebestanden moeten private reguliere
0600-bestanden zijn. Bij ontbrekende of verschoven pagina's, veranderde zoektotalen,
ononderzochte kandidaten, herhaalde zoekresultaat-ID's tussen pagina's,
botsende PR-HEADs of onvolledige externe bronnen blijft het resultaat expliciet
**PARTIAL_HISTORICAL_SNAPSHOT**. De verwachte `SEARCH_WINDOW_PARTIAL` van een
individuele pagina kan pas vervallen wanneer alle pagina's coherent bewaard
zijn. Zelfs zonder gedetecteerde overlap is een reeks losse GitHub Search
opvragingen nooit atomair: de positieve status heet daarom uitsluitend
**CONTIGUOUS_SAVED_WINDOWS_NONATOMIC**, niet 'complete actuele backlog'.
Een bundel bewijst geen huidige GitHub-state, eigenaarsvrijheid, geslaagde
checks, review, merge of deploy. Het originele actuele issue moet voor een vervolgactie opnieuw
worden opgehaald. Onbekende PRs worden nooit automatisch onbezet verklaard.

Iedere nieuwe gerichte privé-JSON snapshot heeft een formaatversie en canonieke SHA-256-inhoudschecksum. De bundelaar weigert een beschadigd of gewijzigd bestand waarvan de checksum niet klopt. Dit is **geen digitale handtekening** en geen bewijs van GitHub-authenticiteit: iedereen met schrijfrechten op het privébestand kan een nieuwe checksum berekenen. Gebruik niet tegelijkertijd `--output` en `--snapshot-output`; het CLI weigert twee potentieel gedeeltelijke lokale bestemmingen.

HTML is een zelfstandige offline werkbak met bestaande PR-links, bron-HEADs,
review/CI-observaties en bronbeperkingen; `--format json` of `text` kan naar
stdout of een nieuw 0600-bestand. De export schrijft nooit bestaande bestanden
over en verricht geen netwerk-, queue-, workflow-, claim- of GitHub-writes.
De privacy-JSON-export is alleen bij expliciet `--issue` beschikbaar.

### Doorzoeken van oudere PRs + zicht op lokale claims

Gerichte bekende issues ondersteunen opeenvolgende **zoekpagina's van 20 bestaande PR-kandidaten**, zonder de andere pagina's stilzwijgend als verwerkt te beschouwen:

```sh
python3 scripts/zsentinel_issue_first_board.py --project cloud --issue 1326 \
  --candidate-page 1 --format json
python3 scripts/zsentinel_issue_first_board.py --project cloud --issue 1326 \
  --candidate-page 2 --output /private/zsentinel-p0-1326-page2.html
```

De `--candidate-page`-optie accepteert 1–50 en uitsluitend in combinatie met `--issue`. Elke pagina van maximaal 20 kandidaten herbevestigt het issue, vraagt de werkelijke PR-body/HEAD, gates en reviews op, en meldt het start/eindvenster, het aantal daadwerkelijk bekeken PR's, bronvolledigheid en een eventuele `next_page`. GitHub's native Search heeft een 1000-resultaatgrens en zoekrangschikking kan onderweg verschuiven; afzonderlijke pagina's geven nooit vanzelf een compleet of onbezet portfolio. De bestaande globale bronlimiet (84 GETs / 120 seconden), vier gevonden PRs per pagina en onafhankelijke review-/writer-/releasegates blijven ongewijzigd.

Dezelfde read-only issue-first uitvoer toont voortaan **geanonimiseerde lokale claimwaarnemingen** per gekozen project, uit `history.db` via `scripts/zsentinel_claim_visibility.py` met SQLite `mode=ro`, `PRAGMA query_only`, één snapshot en maximaal 1000 claimrijen. Alleen totalen van waargenomen actieve lokale leases, exclusieve schrijvers en ambigue bronrijen worden opgenomen in console, JSON en privé offline HTML. Geen owner-, worker-, sleutel-, scope-, metadata- of claim-ID wordt geëxporteerd. Ontbrekende of onvolledige bron = UNKNOWN. Zelfs `NO_ACTIVE_LOCAL_CLAIMS_OBSERVED` betekent nadrukkelijk **niet** dat Notion-claims, andere workers of release-eigenaarschap vrij zijn; `owner_free=NOT_INFERABLE`, `authorization=NOT_GRANTED`. Deze observatie doet geen claims en schrijft niets aan SQLite.

### Huidige CI versus historische of optionele workflows

De merge-helper en offline PR-board gebruiken dezelfde conservatieve status van **actuele voor de PR relevante workflows**. Exacte HEAD-verificatie blijft verplicht. GitHub Actions-workflows voor `pull_request`, `pull_request_target`, `push`, `merge_group` en `workflow_run` worden per workflow/event/branch tot de nieuwste bestaande run gereduceerd; een oudere door een nieuwe run vervangen fout wordt niet als een aparte actieve blokkade voorgesteld. Handmatige/scheduled workflows worden niet als nieuw verzonnen verplicht PR-checks behandeld. `skipped`/neutrale optionele jobs zijn niet noodzakelijk een fout, maar *alleen* overgeslagen workflows zijn geen bewijs van groen. De echte GitHub branch rules, onafhankelijke review, actuele writerclaim en bestaande releaseautorisatie bepalen nog altijd of mergen mag.

## Read-only inventory

```sh
python3 scripts/portfolio_merge_ready.py --repo Zennay/Supa --project supa --pr 123
```

The result describes the current PR source HEAD, draft/base status, **exact-HEAD independent** GitHub review receipts, complete observed GitHub check-runs, legacy statuses and exact-HEAD workflow receipts, and whether a merge would still need a valid exclusive lease. A green-looking result is **not** a claim, GitHub branch protection waiver or production-deploy proof. If the source is unavailable or untrusted the tool fails closed rather than inventing green checks.

The older `portfolio_merge_ready.py` read-only no-`--pr` sweep now inspects at most the **12 newest open PRs** by default (`--max-prs 1..24`). Both the 100-PR GitHub list window and the smaller local selection are marked PARTIAL when they omit records. An explicitly pinned `--pr` ignores this recent-window cap. Do not treat a quiet limited window as a cleared backlog.

## Authorized single-PR merge

```sh
python3 scripts/portfolio_merge_ready.py --repo Zennay/Supa --project supa \
  --pr 123 --merge --owner-id '<real claim owner>' --claim-key '<existing exclusive mainwriter claim>'
```

Use only an **already held and independently authorized** live claim. The helper will not create, renew, steal or modify one. Do not insert real credentials or private lease data into tickets or documentation. It requires:

1. The project-to-repository association matches the canonical `projects.json` entry exactly (including case-insensitive GitHub owner/repo). Missing/ambiguous project entries deny writes. A mainwriter claim in one repository cannot merge another repository.
2. Exactly one explicit PR number. No bulk automatic merges, no default-branch surprises; target default must be `main`.
3. The PR remains open, non-draft, mergeable and targeting `main`. The exact HEAD must still match.
4. At least one **independent approval for that exact HEAD**, no active changes-requested review, and no incomplete >100-review inventory.
5. All observed GitHub check-runs, relevant legacy statuses and applicable current exact-head GitHub Actions workflow receipts are successfully completed (or optional skipped/neutral alongside positive evidence); incomplete, missing, pending or failed current gate sources deny the merge. A completed optional check-run is not proof that other already-queued workflows have completed.
6. A **live non-expired** exact-head `task_claims` record with the specified owner, claim key and a structured `mainwriter` or `main-merge` capability. The same SQLite read-only transaction observes up to 1,000 sibling claims for that canonical project and denies a merge if any other unexpired structured exclusive mainwriter lease exists. Incomplete inventories and ambiguous active writer metadata fail closed; ordinary independent task leases with no writer capability remain untouched. Strings pretending to be a capability list are rejected.
7. A second fresh PR, review, CI and lease check immediately before one SHA-bound GitHub squash merge request. GitHub's branch protection remains the final gate.

After the write, the helper independently reads the PR's `merged_at`, explicit `merged=true`, unchanged PR head, `main` base and `merge_commit_sha`, and the actual `refs/heads/main` SHA. `MERGED` is only emitted if the result is corroborated by the fresh read. `main_commit` identifies the exact merge commit and `default_branch_head_observed` shows the later main ref observed. These may differ if another authorized mainwriter subsequently advanced the branch. In that case a bounded read-only GitHub compare must additionally prove that the reported merge SHA is an ancestor of the current default ref. A divergent branch, missing comparison or uncertain ancestry remains `MERGE_ACCEPTED_POSTCHECK_UNKNOWN`; there is no automatic second merge attempt.

If GitHub accepted a merge but the follow-up receipt is unavailable, it reports `MERGE_ACCEPTED_POSTCHECK_UNKNOWN` with the provisional reported commit. **Do not automatically retry** the merge: query GitHub and reconcile first. A successful source merge does not imply a live VPS deployment or a resumed worker.

## Boundaries

Never use a claim from a different project, treat old approvals as current, bypass protected-main checks, increase GitHub billing, restart a runner or claim ownership on behalf of another worker. The current PR is source-only until its own required exact-head CI, independent review, protected-main merge and serialized release are satisfied. No QA or regression suite is created or manually run by this feature.
