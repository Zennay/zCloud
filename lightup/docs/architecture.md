# LightUp architecture

## Objective

LightUp is designed as a broad AI-assisted security-testing platform for systems that are owned or explicitly authorized. The current release builds orchestration and evidence infrastructure only.

## Control plane

Every future active adapter must receive an `ExecutionPermit` from the shared activation gate. Adapters may not decide scope themselves.

```text
                    +-------------------+
Target + Auth ----> | Scope Supervisor  |
                    +---------+---------+
                              |
                              v
                    +-------------------+
Owner activation -> | Activation Gate   |
                    +---------+---------+
                              |
                         ExecutionPermit
                              |
               +--------------+--------------+
               |              |              |
               v              v              v
          web/API lane   identity lane   cloud/host lane
               \              |              /
                +-------------+-------------+
                              |
                              v
                      Evidence Ledger
                              |
                  +-----------+-----------+
                  |                       |
                  v                       v
             Verification            Remediation
                  |                       |
                  +-----------+-----------+
                              |
                              v
                            Retest
```

## Agent model

Workers specialize by capability rather than all sharing one giant prompt. The orchestrator can schedule independent lanes in parallel, while a SQLite lease on `(run_id, capability_id)` prevents duplicate work.

Planned roles:

- scope supervisor;
- surface/assessment planner;
- web/API auditor;
- identity/access auditor;
- network/service auditor;
- cloud/IAM auditor;
- host/container auditor;
- supply-chain/CI auditor;
- evidence verifier;
- remediation engineer;
- detection engineer;
- report synthesizer.

## Adapter contract

Future adapters should be small and capability-scoped. They receive immutable run context and a permit, emit structured observations, and never write directly to another capability's state.

The project intentionally separates:

- **planning** — safe to run without touching a target;
- **lab execution** — limited to loopback/private lab targets;
- **authorized execution** — requires both scope authorization and explicit owner activation.

## Evidence model

Evidence metadata contains a SHA-256 digest, source label, capability, run id and timestamp. Raw evidence storage is deliberately separate so secrets can be redacted or access-controlled without breaking lineage.

## Non-goals in M0

M0 does not implement network scanners, exploit modules, credential use, payload delivery, persistence or evasion. Those are not needed to prove the orchestration architecture.
