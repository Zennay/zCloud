# ADR-0001: Require versioned decisions for high-blast zCloud changes

## Status

Proposed

## Context

zCloud already blocks broad production promotion behind the temporary `high_blast_radius_promotion` feature flag. That protects execution, but it does not leave a durable, reviewable explanation of why a cross-plane or otherwise broad architectural change was chosen, which alternatives were rejected, what evidence supports it, and how to reverse it.

The repository therefore needs a lightweight decision log that is coupled to the same blast-radius semantics as the guarded promoter without turning normal low-risk changes into documentation overhead.

## Decision

Store architecture decisions as numbered Markdown records under `docs/adr/NNNN-slug.md`.

A GitHub-hosted read-only PR gate classifies production-relevant changed paths with the existing `promotion_blast_radius()` function from the transactional promoter. If that classifier reports a high-blast change, the PR must also change at least one valid numbered ADR. Low-blast changes do not require an ADR.

Each required ADR must contain Status, Context, Decision, Consequences, Evidence and Rollback sections. Tests and ordinary documentation are excluded from blast classification so test-only breadth cannot create a false high-blast result.

## Consequences

High-blast architectural changes become reviewable as an explicit decision plus implementation instead of relying on PR discussion or temporary feature-flag state alone. The CI gate reuses production classification semantics, reducing policy drift. Small changes remain fast because they do not need an ADR.

The gate is advisory through normal GitHub CI enforcement: it does not mutate VPS state, enable feature flags, deploy code, or grant production authority.

## Evidence

The implementation includes unit coverage for service-plus-browser changes, three-plane changes, six production-file changes, test/doc exclusion, missing ADR rejection, malformed ADR rejection, valid ADR acceptance, and parity with the existing transactional promoter classifier.

The workflow runs on GitHub-hosted infrastructure with `contents: read` only and exact PR-head checkout.

## Rollback

If the ADR gate produces incorrect classifications, remove or disable only `.github/workflows/zcloud-high-blast-adr.yml` while retaining the ADR records. The existing runtime `high_blast_radius_promotion` feature-flag gate remains authoritative for production promotion and is not modified by this decision.
