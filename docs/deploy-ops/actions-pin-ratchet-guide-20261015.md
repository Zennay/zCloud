# GitHub Actions pin-drift gate — 15 October 2026

Scope: #633, source-only independent deploy-ops slice. The new policy compares immutable Git trees of GitHub Actions workflow YAML and composite Action metadata (`action.yml` or `action.yaml`) anywhere in the repository, including the root directory. It does not edit existing workflows, invoke VPS jobs, cancel other runs, merge PRs or authorize a release.

## Problem and behavior

A workflow may use a mutable reference like actions/checkout@v4. An upstream tag can be changed later. This ratchet allows existing debt in the immutable base revision but refuses newly introduced references whose exact token is not pinned to a 40-character Git SHA. A floating reference changed to another floating tag counts as new debt. Local repository actions (./...) and Docker SHA-256 digests are exempt; Docker tags and unknown/dynamic expressions remain debt.

* Baseline: github.event.pull_request.base.sha. Candidate: github.event.pull_request.head.sha. Both are exact commits, not moving branches.
* Sources are read from complete Git tree objects, not a mutable worktree. Workflow YAML and composite Action metadata are checked; the latter includes nested custom directories, so a local Action moved outside `.github/actions/` does not evade the pinning policy. The hosted check triggers on tracked `action.yml`/`action.yaml` changes as well as workflow changes. Symlink/nonregular metadata, symlinked `.github/actions` directories, and local `./action` references traversing a symlink ancestor are refused. Unrelated symlinks remain allowed. Invalid bytes, excessive files and ambiguous evidence are refused.
* Blob memory boundary: query each immutable Git object's declared size with `git cat-file -s` before reading its bytes. Refuse >1 MiB per workflow/composite metadata file or >32 MiB total source, and refuse any actual/declared byte-count mismatch. A regression stubs raw reads to prove oversized blobs are rejected without buffering their contents.
* Debt identity is a multiset of workflow path, classification and SHA-256 of the raw reference. Duplicate actions are counted, so an additional unpinned invocation is not concealed by a matching old one.
* JSON output has only bounded path, line and hash prefix. It never includes raw action references. Exit 0: no new debt; 1: new debt; 2: incomplete/unsafe evidence. All outcomes permanently deny deployment and release authority.
* Hosted-only PR workflow, contents:read, pinned credential-free checkout, no privileged dispatch, SSH or production write.
* Initial PR bootstrap uses the candidate script because the trusted base does not contain this script yet. This is advisory bootstrap evidence, NOT independent security review or approval. After integration, subsequent PRs read the scanner from the exact base commit.
* Ambiguous YAML aliases, step reuse, inline flow-map merges, quoted uses-keys, **empty/folded/literal `uses:` values** and non-regular workflow directory ancestry are rejected rather than silently accepted. A `uses: >` or `uses: |` multiline value can hide a changed downstream action while its first-line token stays unchanged; the scanner therefore refuses such source forms entirely. Inline shell literals and comments remain ignored where confidently distinguished. The source scanner is still not a complete YAML parser or publisher provenance validator; independent review remains required. A 40-character commit SHA is not by itself publisher authorization.
* Local action paths must use canonical, nonempty path segments without `.`/`..`, duplicate separators or trailing slash. This prevents alternative spellings such as `./tools/./action` from sidestepping the Git-tree source-ancestor check. Both **symlinks** and **Git submodule gitlinks (mode `160000`)** that are ancestors of a locally used Action are refused because the exact source contents are outside this scanner's validated tree. A normal hidden component (`./.github/actions/example`) remains allowed; unrelated symlinks and unrelated submodules remain unaffected.
* After the full scanner finishes, a separate GitHub-hosted shell gate rechecks both current canonical main and the exact same-repository pull-request head via read-only Git queries. If either reference advanced, disappeared or cannot be fetched, proof fails; a foreign-fork head is also refused because origin alone cannot independently attest it. Real temporary-Git regressions exercise unchanged, moved and missing main and head references, plus foreign-head denial. This is still not a production authorization check.
* This does not replace runner recovery #1326, three writer locks #1447, existing source owners, or serialized production gate #580/PWQ-41 and #1089.

## Aanvullende main-push audit

De GitHub-hosted workflow controleert naast PR's ook rechtstreekse pushes naar `main` zodra een workflowbestand, pin-scanner of lokaal `action.yml`/`action.yaml` wijzigt. Die extra, niet-geprivilegieerde job vergelijkt de exacte `before`-commit met de gepushte `HEAD`, leest de scanregel bij voorkeur van de historische before-commit (first-merge-bootstrap expliciet onbewezen) en controleert opnieuw dat `origin/main` nog op die gepushte HEAD staat. Onbetrouwbare/null before-revisies falen dicht. De audit schrijft alleen een korte Nederlandstalige samenvatting in GitHub-hosted Actions, zelfs bij ontbrekend bewijs. De job mag geen productie of release autoriseren en kan een reeds uitgevoerde push niet terugdraaien: het resultaat is signalering en bewijs, niet preventieve branchbescherming.

De echte shell-push wordt met tijdelijke bare Git repositories getest op ongewijzigde main, nieuw onveilig gepinde Action, ontbrekende before-commit en een niet-main event. Privileged runner, VPS, tokens met schrijfrecht, workflow dispatch of deploy worden nergens aangeroepen.

## Operator usage

From a local Git repository with the base and candidate commits already fetched:

    python3 scripts/zcloud_actions_pin_ratchet_20261015.py --base BASE_40_HEX_SHA --head HEAD_40_HEX_SHA

## Operatorbewijs bij succes én weigering

De GitHub-hosted workflow toont een klein Nederlandstalig overzicht van oude en nieuwe niet-vastgepinde Actions, zonder broncode, ongefilterde verwijzingen of wachtwoorden. Bij code 1 (nieuwe schuld), code 2 (onbetrouwbare bron) of een ontbrekend rapport wordt het overzicht toch aangemaakt met duidelijke **geen autorisatie**. Ook bij een volledig groen rapport blijven release- en deploytoestemming altijd **Nee**. Dubbele JSON-sleutels, NaN, tegenstrijdige uitslagen, onverwachte bevoegdheid, buitensporige rapportgrootte of te diep geneste JSON maken het overzicht onbetrouwbaar. Ook bij zo'n parserweigering verschijnt het Nederlandse ontmachtigde overzicht in plaats van een crash. Deze samenvatting draait buiten de checkout vanuit een tijdelijke map om Python-importschaduw vanuit PR-bestanden te beperken.

De echte GitHub-hosted regressies voeren zowel een lokaal Git origin-verplaatsingsscenario als ontbrekende origin, geslaagde origin, onbetrouwbare rapporten en een geheimhoudingscontrole uit. Het overzicht is uitsluitend een inspectiehulp; het mag niet als releasegoedkeuring worden ingezet.

Keep the PR DRAFT and unmerged until exact-head full regression, owner/branch collision proof and independent policy review. A green hosted source result cannot authorize production promotion.
