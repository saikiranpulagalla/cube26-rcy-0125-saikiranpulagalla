# Recovery Manager architecture — v0.2

This document records the Astra-approved Plan V2 boundary for the implemented v0.1 foundation. It does not claim that Recovery logic, official evidence-contract compatibility, or policy-backed claims exist.

## v0.1 capability boundary

| Capability | v0.1 state |
|---|---|
| Recovery policy | `DISABLED` |
| Official evidence contract | `DISABLED` |
| AI semantic reasoning | `DISABLED` |
| Claim/export | `DISABLED` |
| Recovery UI | `DISABLED` |

v0.2 can safely accept, preserve, schedule and inspect tenant-owned input. It adds lossless
canonical source versions, financial-event direction/money/time metadata, and evidence-record
coverage metadata. It does not evaluate policy, evidence admissibility, entitlement, settlement,
or issue business assessments.

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

The migration creates `recovery_owner`-owned protected tables. `recovery_app` can append raw envelopes,
work intents and audit events but cannot rewrite or delete them. `recovery_worker` has a separate credential
and only receives constrained lifecycle-column permissions. Database constraints and triggers reject invalid
tenant identifiers, revision decrements, raw/audit rewrites, and illegal work transitions. Each business table
has `org_id`, RLS is enabled and forced, and the policy uses transaction-local `app.current_org_id` with empty
context treated as no tenant. Composite `(org_id, id)` foreign keys prevent a work intent or attempt from
referring across tenants.

The application checks at startup/readiness that its runtime role is neither superuser, `BYPASSRLS`, nor owner of protected tables.

## Work recovery

The polling worker enumerates only configured trusted tenants. It acquires jobs through `SELECT … FOR UPDATE SKIP LOCKED`, records an attempt and leases work with a token. Completion and failure validate that token and the database wall clock after acquiring the final row lock. Expired work becomes recoverable with a `LEASE_EXPIRED` historical outcome; a former holder cannot complete or mutate it later.

There is no network, AI, policy or financial work in v0.1. Successful worker completion means only `COMPLETED_STRUCTURAL_PROCESSING`.

## Fixture boundary

The local-only `fixture-load` CLI is disabled unless `RECOVERY_DEMO_FIXTURES_ENABLED=true`. It accepts only SHA-256-allowlisted organizer fixture files. It splits rows by source-declared tenant into separate tenant envelopes and retains the original file hash and row number. It has no HTTP endpoint and is not an ordinary-import bypass.

## External blockers

The official evidence contract, domain brief, authoritative recovery policy and annotation guidance are not available in this repository. Those capabilities remain disabled. The synthetic organizer CSVs are reference data only and cannot establish real policy or claim correctness.

## Future constraints frozen now

Later versions must retain raw/source versioning, tenant-wide decision revisions, immutable machine assessments, obligation-level settlement allocation, conservative quantity coverage, evidence admissibility, retrieval completeness and snapshot freshness. No later work may treat source amount as recoverable residual or let an AI citation establish a financial fact.

## Canonical sources, time and coverage

Each allowlisted organizer CSV row becomes a tenant-scoped `SourceRecordVersion`, keyed by its opaque
source identifier and content hash. The original row envelope remains preserved. A changed payload is a
new source version; a replay of identical content is idempotent. No suffix or fuzzy identifier matching
exists.

The fee report adapter produces `FinancialEvent` records with strict fixed-point USD parsing, explicit
`DEBIT`/`CREDIT`/`ADJUSTMENT` direction, source posting time and `DATE` precision. It never treats
posting time as incident time. Receiving, prep, pack and returns adapters produce `EvidenceRecord`
metadata with captured observation time and conservative coverage. Only receiving has a known quantity;
other fixture coverage stays unknown rather than being added across potentially overlapping records.

The `canonicalize-fixture` command remains a demo-only allowlisted loader. Its reconciliation gate
requires 61 fee, 100 receiving, 62 prep, 29 pack and 24 returns rows. Synthetic files remain neither
policy nor recovery ground truth.
