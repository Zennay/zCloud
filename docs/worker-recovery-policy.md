### Parallelle merge-eigenaaracties (read-only)

De exacte PR-preflight levert naast de ene overkoepelende `status`
nu `parallel_owner_actions` in de JSON-resultaten en dezelfde kaarten als
**Problem / Action / Status** in Markdown en statische offline HTML.
Hierdoor maskeert `BASE_TIP_OUTDATED` geen afzonderlijke `pending`
of terminal niet-geslaagde checks: een eigenaar kan tegelijkertijd de
base oplossen, runner-/CI-toewijzing onderzoeken en onafhankelijke review
regelen. Een gedeeltelijke checkpagina vraagt expliciet om inspectie van
alle verplichte exacte-HEAD-checks; een lege reviewsteekproef is alleen
`ZERO_REVIEWS_IN_SAMPLE`, nooit bewijs dat op GitHub geen reviewer bestaat.

Elke kaart heeft een stabiele code, herleidbare *sample*-categorie en
`status=HOLD_OWNER_REVIEW`, met `execution_authorized=false`.
Gesloten PRs worden niet meer als mergekandidaat aangeboden; bij
`merged=true` gaat de eigenaarshandoff uitsluitend over controle van de
default-branch-mergecommit en een afzonderlijke live release.
Instabiele PR- of default-branch-bron geeft uitsluitend een bronherleesactie,
zonder onbetrouwbare parallelle gateclaims. Er is geen extra GitHub API GET,
workflow-rerun, claim, queue-mutatie, merge of deploy gekoppeld aan deze
werkkaarten. Dit blijft een bron- en operatorfunctie op een open PR;
een volledige review, branch-protectionbeleid en eigenaarsmandaat
zijn apart verplicht.

De nieuwe `scripts/zsentinel_pr_merge_preflight.py` valideert een exacte
`Zennay/<repo>/pull/<positief-nummer>` URL, gebruikt maximaal acht
**read-only** GitHub API GETs (PR → repository-default-branch → default-branch
tip SHA → HEAD-checks → reviews → repository-default-branch opnieuw →
default-branch tip opnieuw → dezelfde PR). Zowel de naam als de commit-SHA
van de standaardbranch moeten stabiel zijn tijdens de observatie. Het vergelijkt
de PR-base-SHA met die stabiele tip: `DEFAULT_BRANCH_TIP_UNKNOWN` betekent
dat de huidige tip niet stabiel kon worden vastgesteld; `BASE_TIP_OUTDATED`
geeft aan dat de PR op een andere base-commit staat en eerst door de eigenaar
moet worden bijgewerkt, zonder force of gate-bypass. `DEFAULT_BRANCH_SOURCE_UNKNOWN`
blijft gelden als de branchnaambron ontbreekt of verandert; `BASE_NOT_DEFAULT`
markeert een PR die niet naar de waargenomen standaardbranch wijst. PR-status,
base-branch-SHA, draft, merged en HEAD worden opnieuw gelezen vóór een stabiele
exact-HEAD wordt getoond. Een mislukte laatste PR-herlezing wordt als
`SOURCE_UNKNOWN` met een concrete bronfout in het handoff teruggegeven; de
eerste waargenomen HEAD wordt dan niet als stabiel gepresenteerd. De check-run samenvatting scheidt potentieel blokkerende
terminale non-success (failure/cancelled/stale/unknown), neutral/skipped en
pending; neutral/skipped zijn uitdrukkelijk géén succesbewijs. Markdown en
de scriptvrije offline HTML linken iedere zichtbare check naar het bestaande
exacte GitHub-runbewijs waar een canoniek run-ID beschikbaar is. De check- en reviewpagina zijn **beperkte samples**; zelfs volledig
geslaagde zichtbare checks bewijzen niet welke CI branch protection verplicht
stelt. De reviewuitvoer onderscheidt nu aantallen/states die aan de exacte
PR-HEAD zijn gebonden, reviews op andere commits, reviews zonder geldige
commitbinding en reviewrecords met een onbekende/ontbrekende status. Bij
ongebonden, afgekapt of onbekend statusbewijs wordt de dekking `PARTIAL`.
Dit bewijst nog steeds geen reviewer-onafhankelijkheid, laatste effectieve
goedkeuring of opgeloste threads. Reviewstatus, branch protection, owner claims
en release-autorisatie blijven apart **UNKNOWN / HOLD** totdat een bevoegd
persoon ze onafhankelijk controleert.
Een mislukte check of een nog niet afgeronde check resulteert in een gerichte
operatoractie, nooit een workflowdispatch of merge.

#### Asynchrone GitHub-mergeability-hint

De exacte PR-preflight vergelijkt de optionele `mergeable`-hint van beide
PR-reads afzonderlijk van de stabiele PR-identiteit. Als de hint tussen de
reads verandert, wordt de gate `UNKNOWN` en vraagt de operatoractie om een
nieuwe observatie. `mergeable=false` leidt tot inspectie van de echte
conflictpaden met de bestaande branch-eigenaar; een ontbrekende hint leidt
eveneens tot `UNKNOWN`. `mergeable=true` wordt alleen als een waargenomen
hint getoond, nooit als bewijs van een conflictvrije of toegestane merge.
Exact-head verplichte CI, onafhankelijke review, branch protection, actuele
claims en eigenaarautorisatie blijven afzonderlijke gates. Deze toevoeging
doet geen extra GitHub-GET, workflow-rerun, claim-, merge- of deployactie.

GitHub 401/403/429 stopt onmiddellijk de verdere secundaire reads en levert
`SOURCE_UNKNOWN`, niet een eerder opgeslagen head alsof die nog actueel is.
Een verschoven PR HEAD of state tussen de eerste en laatste GET wordt eveneens
`SOURCE_UNKNOWN`. Een algemene secundaire bronfout blijft zichtbaar en maakt
CI/review UNKNOWN. De uitvoer heeft altijd
`merge_authorized=false`, `merge_executed=false`,
`active_claim_verified=false`, `branch_protection_verified=false` en
`deployed=false`: alleen brontriage, geen toestemming.

De output bevat daarnaast acht concrete, per-gate operator-kaarten in JSON,
Markdown en offline HTML: exacte HEAD, open/non-draft/default-branch,
zichtbare checks, alle verplichte CI, onafhankelijke reviews en threads,
branch protection, actieve ownerclaims, en post-merge/main/live bewijs.
Alle ontbrekende live autorisaties blijven expliciet UNKNOWN/HOLD; een
geslaagde bronobservatie vormt nooit zelf een merge- of deploytoestemming.

Ook de reeds bestaande policy-acties `retry_ci`, `assign_independent_work`,
`park_blocker`, `merge_pr`, `deploy` en `restart_runner` houden nu
expliciet op een **ontbrekende benoemde owner**. Een ingevulde