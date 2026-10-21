# Per-project intensity slider policy

This document defines the control-plane meaning of the per-project **intensity** slider.
It is a scheduler-intent contract, not a safety override.

## Range and tiers

| Slider | Tier | Queue weight | Dispatch interval multiplier | CPU target fraction | Idle capacity borrow | Burst eligible | Backpressure bias |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| 0–20 | minimum | 25 | 2.00x | 35% | no | no | early |
| 21–40 | conservative | 50 | 1.50x | 50% | yes | no | early |
| 41–60 | balanced | 100 | 1.00x | 70% | yes | no | standard |
| 61–80 | accelerated | 150 | 0.75x | 85% | yes | yes* | standard |
| 81–100 | maximum | 200 | 0.50x | 100% | yes | yes* | late |

`cpu_target_cores` is always bounded by the project's existing
`compute.cpu_soft_cores` contract. A non-zero compute project keeps a 0.25-core
floor so a low slider does not accidentally become a hidden Pause action.

`* burst eligible` means the scheduler may use otherwise idle capacity only after
all normal admission checks pass. Protected control-plane projects and disabled
pools are never burst eligible.

## Non-negotiable guardrails

Changing intensity MUST NOT:

- change or bypass `ai_worker_cap`;
- move work to another resource pool;
- bypass memory/load/IO admission or backpressure;
- preempt capacity protected for zCloud/Firefox/control-plane work;
- cross a worker lane, task claim or project isolation boundary;
- act as Start, Pause or Drain;
- mutate `project-contracts.json` as a side effect.

The mapping is intentionally side-effect free. The canonical executable contract
is `scripts/zcloud_intensity_policy.py`.

## Scheduler consumption contract

Future scheduler integration should consume the generated fields as follows:

- `queue_weight`: relative preference between runnable projects after hard safety
  and dependency gates have admitted them.
- `dispatch_interval_multiplier`: scales the scheduler's normal dispatch cadence;
  it never creates a tighter cadence than platform anti-spam/rate limits allow.
- `cpu_target_cores`: desired compute target, bounded by the existing project
  `cpu_soft_cores` ceiling.
- `allow_idle_capacity_borrow`: permits borrowing only capacity that is actually
  idle and not protected.
- `burst_eligible`: allows temporary opportunistic acceleration; it is false for
  protected projects and disabled pools even at intensity 100.
- `backpressure_bias`: tuning hint for how early the scheduler should yield when
  resource-pressure or diminishing-return evidence is present.

## Integration boundary

This change defines and proves the policy only. It deliberately does not modify
`server.py`, scheduler allocation, SQLite state, dashboard controls or live VPS
configuration while the serialized control-plane writer/integration window is
owned elsewhere.

A later integration change may wire the slider into the scheduler only when it can
prove:

1. the stored slider value is validated as an integer in 0–100;
2. this policy is the sole translation layer;
3. existing worker/pool/memory/protection gates still dominate the result;
4. requested versus actually allocated capacity is observable;
5. rollback restores the previous scheduler behavior without data migration.
