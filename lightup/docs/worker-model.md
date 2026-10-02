# LightUp worker model

## Worker prompt

Keep project prompts compact:

> Werk verder aan LightUp. Kijk in Notion in welke fase het project zit en wat er nog gedaan moet worden. Pak één concreet niet-geclaimd werkpakket en werk dat uit. Respecteer de huidige activation mode en scope policy.

## Coordination

Workers coordinate on `(run_id, capability_id)` leases. Independent capabilities may run in parallel; identical capabilities for the same run may not.

Recommended future lanes:

| Lane | Responsibility | Current mode |
|---|---|---|
| scope-supervisor | authorization + boundaries | active infrastructure |
| assessment-planner | creates scoped plans | active infrastructure |
| web-api | web/API security assessment | planning only |
| identity | authentication/authorization assessment | planning only |
| network-services | service/configuration assessment | planning only |
| cloud-iam | cloud privilege/config review | planning only |
| host-container | OS/container hardening | planning only |
| supply-chain | dependency/build/CI review | planning only |
| evidence-verifier | dedupe + evidence integrity | active infrastructure |
| remediation | fixes + hardening proposals | active infrastructure |
| detection | monitoring/detection follow-up | active infrastructure |
| report | final evidence/retest synthesis | active infrastructure |

## Collision policy

- one lease per run/capability;
- leases expire and can be safely recovered;
- evidence is append-only and content-addressed;
- workers do not modify another lane's evidence;
- scope and activation state are immutable inputs for a run;
- no worker may enable active execution on its own.
