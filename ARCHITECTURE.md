# Recovery Manager architecture — v0.1

This document records the Astra-approved Plan V2 boundary for the implemented v0.1 foundation. It does not claim that Recovery logic, official evidence-contract compatibility, or policy-backed claims exist.

## v0.1 capability boundary

| Capability | v0.1 state |
|---|---|
| Recovery policy | `DISABLED` |
| Official evidence contract | `DISABLED` |
| AI semantic reasoning | `DISABLED` |
| Claim/export | `DISABLED` |
| Recovery UI | `DISABLED` |

v0.1 can safely accept, preserve, schedule and inspect tenant-owned input. It does not normalize financial events or issue business assessments.

## Trust boundary

```text
development credential → trusted principal (tenant + actor + role)
                              ↓
                     transaction-local DB tenant context
                              ↓
               RLS-protected envelope, work and audit records
```

The request body never selects the tenant. A CSV `org_id` is retained as source-declared metadata and is checked against the authenticated tenant. A mismatch is quarantined; it is never converted into canonical evidence.

## Durability

One transaction creates the raw envelope, its queued work intent, a receipt audit event and the tenant decision revision. The API returns accepted only after commit. A database failure returns no accepted success.

`RECEIVED` is preserved as an audit event; the durable work row starts in `QUEUED`.

## PostgreSQL isolation

The migration creates `recovery_owner`-owned protected tables and grants the restricted `recovery_app` runtime role CRUD access. Each business table has `org_id`, RLS is enabled and forced, and the policy uses transaction-local `app.current_org_id`. Composite `(org_id, id)` foreign keys prevent a work intent or attempt from referring across tenants.

The application checks at startup/readiness that its runtime role is neither superuser, `BYPASSRLS`, nor owner of protected tables.

## Work recovery

The polling worker enumerates only configured trusted tenants. It acquires jobs through `SELECT … FOR UPDATE SKIP LOCKED`, records an attempt and leases work with a token. Completion requires that exact unexpired token. Expired work becomes recoverable; a former holder cannot complete it later.

There is no network, AI, policy or financial work in v0.1. Successful worker completion means only `COMPLETED_STRUCTURAL_PROCESSING`.

## Fixture boundary

The local-only `fixture-load` CLI is disabled unless `RECOVERY_DEMO_FIXTURES_ENABLED=true`. It accepts only SHA-256-allowlisted organizer fixture files. It splits rows by source-declared tenant into separate tenant envelopes and retains the original file hash and row number. It has no HTTP endpoint and is not an ordinary-import bypass.

## External blockers

The official evidence contract, domain brief, authoritative recovery policy and annotation guidance are not available in this repository. Those capabilities remain disabled. The synthetic organizer CSVs are reference data only and cannot establish real policy or claim correctness.

## Future constraints frozen now

Later versions must retain raw/source versioning, tenant-wide decision revisions, immutable machine assessments, obligation-level settlement allocation, conservative quantity coverage, evidence admissibility, retrieval completeness and snapshot freshness. No later work may treat source amount as recoverable residual or let an AI citation establish a financial fact.
