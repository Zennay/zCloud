# zCloud main protection: historical writer compatibility inventory (16 October 2026)

Parent: [#922](https://github.com/Zennay/zCloud/issues/922). Related source-only prevention work: [PR #1471](https://github.com/Zennay/zCloud/pull/1471). This tool is different: it inventories the *existing* workflows at exactly one local main commit. It does not veto new changes, approve a protection rule, enforce runtime locks or perform a live operation.

## Usage

From a clean checkout whose HEAD and local refs/heads/main are the exact same verified commit:

    python3 scripts/zcloud_main_writer_compat_inventory_20261016_w12.py --expected-main-sha EXACT_40_CHAR_SHA --format markdown

For an evidence JSON with stable signal codes, use --format json. The output is read-only stdout; no files are written. The caller must establish remote GitHub origin/main identity independently before and after checkout; this helper verifies only the local immutable source identity. All error responses exit 2, are redacted and explicitly deny any production or governance permission. A successful inventory exits 0 **only** to indicate a completed *static* scan.

The scanner reads every tracked YAML workflow blob under .github/workflows at the pinned SHA. It recognizes direct pushes to main, other pushes, ref API calls, merge operations, write-token grants and GitHub script usage; reports filename, committed SHA-256 digest and line-numbered signal codes. Limits: 512 workflows, 512 KiB each, 24 MiB total; reject symlinked YAML, malformed/oversized/binary YAML blobs, unexpected paths, SHA drift and unexpected tree shapes.

## Why this does not solve #922 alone

- Static regex signals are candidates, **not proof of execution or active business need**. Scripts and imported composite actions may hide indirect writes; a no-signal workflow is not necessarily compatible.
- The inventory does not infer whether a job is obsolete, actively required or a false positive. For each candidate, the workflow owner must classify one of: active and migrate to PR/artifact/status/comment evidence; terminal and retire via its existing owner; false positive/read-only, with reason and verified provenance.
- Job trigger trust, privilege semantics, workflow provenance, recent PR-less branch ownership, runtime scheduler behavior and API writers outside workflows need separate scrutiny.
- A complete inventory does not certify independent review, configured main protection, required status checks or production readiness. Both safe_to_enable_branch_protection and production_mutation_authorized are **invariantly false**.
- Existing ownership of the writer workflows and governance work remains with #922/#1033/#580/PWQ-41/#1089 and their current PR owners. This PR introduces uniquely named source/test/docs/hosted-workflow files; it must not alter those owners' files.

## Next owner-side acceptance

Run against a freshly verified canonical main SHA, retain the redacted JSON receipt, map every candidate to its existing PR owner and concrete disposition, then separately audit REST/GraphQL clients and composite actions. Re-run exact-head compatibility before proposing branch protection; use #1033's independent GitHub governance audit and the serialized release owner for any eventual live change. Never treat this scanner as an authorization gate.

## GitHub-hosted current-main evidence job

The new same-branch `.github/workflows/zcloud-main-writer-compat-inventory-20261016-w12.yml` runs only on pull requests to main, with explicit `contents: read`, a pinned checkout action, no checkout credentials persisted, and `ubuntu-latest` (never the VPS). It admits only same-repository heads; independently checks current origin/main against the PR base SHA and the live remote PR head against the event head SHA before and after tests. It runs the 19 real-Git tests, then inventories a local detached worktree at the exact pinned main SHA, outputting a human-readable candidate list and permanent denial flags. This job is diagnostic and cannot grant protection or production authority. A queued/self-hosted VPS diagnostic is not a substitute for this job's completed evidence.

No remote Git ref, branch protection, workflow owner, production service or worker state is changed; the ephemeral hosted worktree and its local main ref are discarded with the job.

## Owner review claims: offline triage, not admission to protected main

After a GitHub-hosted inventory has produced `main-writer-receipt.json`, optionally create a local `reviews.json` containing one **untrusted, self-declared owner decision** per selected workflow:

```json
{
  "schema": "zcloud-main-writer-review-v1",
  "main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "decisions": [
    {
      "path": ".github/workflows/example.yml",
      "source_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "owner": "workflow-owner",
      "reviewed_by": "independent-reviewer",
      "decision": "migrate",
      "evidence_url": "https://github.com/Zennay/zCloud/issues/922#issuecomment-12345"
    }
  ]
}
```

The 40-character main SHA and 64-character committed blob digest shown are placeholders only. Use **exact actual values from the inventory**, never the example literals. Valid `decision` values: `migrate` (retain function but replace direct main writes), `retire` (obsolete, retire via workflow owner), `false_positive` (prove that the reported signal does not perform an incompatible write). Every claim needs an existing GitHub issue/PR link to the concrete evidence, a named owner, and a different named reviewer. Names and links are not independently authenticated, only validated syntactically; the applicable workflow owner must separately confirm their validity.

```bash
python3 scripts/zcloud_main_writer_review_admission_20261016_w12.py \
  --inventory main-writer-receipt.json \
  --reviews reviews.json \
  --expected-main-sha EXACT_40_CHAR_SHA \
  --format markdown
```

The reviewer consumes bounded local UTF-8 regular files (320 KiB inventory, 128 KiB reviews) and rejects symlinked parent directories, path traversal, duplicate keys, ambiguous numbers, surrogate/bidi control sequences, duplicate workflow decisions, mismatched source blob hash, stale main identity, unrecognized disposition, unsupported URLs, and conflicting inventory counts or authorization flags. A partially reviewed queue is allowed for honest progress reporting; the tool lists each remaining path. Every successfully claimed decision retains **unverified** provenance, even if all candidates have a submitted review: `review_claims_independently_verified=false`, `safe_to_enable_branch_protection=false`, `production_mutation_authorized=false`. Exit 0 means only "offline claims syntactically consistent"; exit 2 denies invalid data.

The hosted workflow exercises both real Git source tests and owner-claim tests, and reports **zero review claims** against the actual main to avoid fabricating owner approvals. The operator must resolve each candidate in its original issue/PR ownership thread and maintain an external exact-sha review audit before any #922 branch rule change. Never feed self-declared offline review counts to a privileged gate.

## Source safety validation

Both the main inventory and human review triage run without GitHub API write privileges. Inventory Git subprocess output is streamed with a hard per-command output cap and a monotonic 20-second deadline, terminating stalled/oversized child process groups. This prevents unchecked output buffering while examining potentially hostile Git trees. Review evidence is read from fd-anchored regular files with `O_NOFOLLOW` for every ancestor and final component, rejecting symlink parents and traversal. On any failure the CLI returns a stable non-sensitive reason and **never** branch protection or production authorization. These bounded offline proofs cannot certify live runner health or owner authenticity.

## Machine-readable reviewer queue export

```bash
python3 scripts/zcloud_main_writer_compat_inventory_20261016_w12.py \
  --expected-main-sha EXACT_40_CHAR_SHA --format csv > review-queue.csv
```

This CSV includes immutable Git main SHA, workflow path, **SHA-256 of the workflow's committed Git blob bytes**, line/kind signal references and P1/P2 triage priority. All owner, reviewer, decision and evidence URL cells start blank. `review_verified` and `branch_protection_authorized` are always `false`. Each row is a candidate requiring human action and is not evidence that a workflow actually ran. The read-only GitHub-hosted job checks the generated CSV and exposes its bounded source-only rows in the job log, without uploading executable artifacts or GitHub write tokens. Independent identity/evidence confirmation and the #922 owner workflow remain mandatory.

## Vul de CSV rechtstreeks in (geen handmatige JSON-conversie)

De eerdere JSON-interface blijft beschikbaar, maar de reviewlijst uit GitHub-hosted CI of uit Notion is nu **rechtstreeks** bruikbaar. Vul in de originele CSV alléén de vier kolommen \`owner\`, \`reviewed_by\`, \`decision\` en \`evidence_url\` in, en laat elke onbesliste rij geheel leeg op deze vier velden. Gebruik uitsluitend de bestaande waarden \`migrate\`, \`retire\` of \`false_positive\`. Een ingevulde cel zonder de andere drie wordt geweigerd.

\`\`\`bash
python3 scripts/zcloud_main_writer_review_admission_20261016_w12.py \
  --inventory main-writer-receipt.json \
  --reviews-csv ingevulde-review-queue.csv \
  --expected-main-sha EXACT_40_CHAR_MAIN_SHA \
  --format markdown
\`\`\`

De oorspronkelijke CSV-kolommen voor main-SHA, workflowpad, workflow-SHA-256, signalen, prioriteit en beide autorisatievlaggen mogen **niet** veranderen. Alle kandidaten van dezelfde broninventaris moeten precies één keer in het bestand blijven staan. De controle weigert dubbele, ontbrekende, onbekende, verouderde of gemanipuleerde rijen, inclusief gewijzigde header, vervalste beveiligingsvlag, spreadsheet-formule als username, verborgen biditekens, onbetrouwbare bewijslink en deels ingevulde beoordelingen. Beoordelingen worden pas als *syntactisch volledig* meegeteld wanneer eigenaar en andere reviewer van elkaar verschillen; hun GitHub-identiteit en bewijs zijn **niet** door de offline tool geverifieerd.

De source scanner ondersteunt ook shellregelvervolg (backslash), Git-opties als \`-C\`, \`-c\` en \`--git-dir\` voor een \`git push\`, en weigert control-karakters voordat regels worden opgesplitst. Git-omgevingvariabelen zoals \`GIT_DIR\` en \`GIT_CONFIG_COUNT\` uit de aanroepomgeving krijgen geen invloed op de vaste Git-bron. Onzichtbare schrijfpaden via variabelen of externe reusable Actions blijven een menselijke audittaak; nul gevonden signalen is nooit een vrijgave.

De hosted workflow controleert dat de **echte, ongewijzigde main-inventaris** via JSON én CSV precies dezelfde openstaande beoordelingen en \`safe_to_enable_branch_protection=false\` geeft. Een lokaal ingevuld bestand wordt niet zonder expliciete onafhankelijke verificatie gebruikt voor een production release.
