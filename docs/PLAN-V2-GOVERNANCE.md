# Plan V2 governance

## Astra ↔ Terra protocol

1. Astra defines one bounded version contract.
2. Terra implements only that version.
3. Terra returns the diff, commands, test evidence, migration state and limitations.
4. Terra does not tag or promote itself.
5. Astra independently inspects code, migrations, tests, security and adversarial cases.
6. Astra returns only `GO`, `CONDITIONAL GO` or `NO-GO`.
7. Only `GO` permits promotion to the next version.

If a required implementation choice changes entitlement, tenant, durability, provenance, capability restriction or financial semantics, Terra stops with `ARCHITECTURE/REQUIREMENT BLOCKER`.

## P0 invariants carried through v0.3

- No cross-tenant read, write, reference, raw-object or audit/work-history leak.
- No accepted input without exact durable raw bytes and durable work intent.
- No source-declared-organization laundering.
- No unsafe runtime DB role.
- No stale lease holder can complete work.
- No recovery, policy, AI or claim capability is active.
- No synthetic organizer data is represented as authoritative recovery policy.
- No committed secrets or modified organizer starter resources.
- Every settlement and pursuit amount is allocated to a tenant-matching economic obligation; source
  credits, reversals and active pursuits cannot be silently netted or double allocated.
- A missing justified entitlement remains unknown and cannot become a recovery recommendation.

## Requirement traceability

| Requirement | v0.1 evidence |
|---|---|
| RLS before features | Migration, role setup and security tests |
| Fail-open preservation | Atomic raw/work transaction and quarantine path |
| Batch model calls | Not applicable: v0.1 performs no model calls |
| Uncertainty first class | Not business-modeled yet; technical failures remain separate from processing state |
| Authoritative rules | Disabled until authority is supplied |
| Traceability | Raw hash, actor, source, audit event and work attempts |

## Submission tracking

Current repository requirements include runnable code, README, architecture, evaluation results, demo/video, deployment where applicable, a LinkedIn post URL and no secrets. The current GitHub guide supersedes older participant-folder/PR mechanics. The legacy template and submission guard are retained unmodified pending organizer clarification.
