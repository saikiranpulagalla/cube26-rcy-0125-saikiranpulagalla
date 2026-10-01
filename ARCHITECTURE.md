# Recovery Manager architecture

Recovery Manager is a PostgreSQL-backed, deterministic recovery decision system. It models whether a synthetic financial recovery is justified and currently actionable; it does not use a model or LLM to create monetary entitlement.

## System architecture

~~~mermaid
flowchart LR
    S[Raw source data] --> I[Ingestion and source versions]
    I --> C[Canonical financial events]
    C --> O[Economic obligations]
    E[Evidence facts] --> A[Assessment engine]
    P[Synthetic policy authority] --> A
    L[Settlement and pursuit ledger] --> A
    O --> A
    A --> G[Guarded publication]
    G --> H[Immutable assessment snapshot]
    H --> X[Synthetic export boundary]
    B[Benchmark and demo CLI] --> A
    DB[(PostgreSQL\nRLS + role boundary)] --- I
    DB --- C
    DB --- O
    DB --- E
    DB --- L
    DB --- A
    DB --- G
    DB --- H
~~~

The primary data flow is:

1. Source data is ingested and retained as tenant-scoped source versions.
2. Canonical financial events and economic obligations establish exact business identities.
3. Evidence assertions prove specific source/version/subject/value propositions.
4. Applicable synthetic policy authority, evidence, settlement reconciliation, and active-pursuit reconciliation are evaluated.
5. The assessment engine calculates the remaining residual only when decisive inputs are complete.
6. A guarded publication boundary rechecks current decisive state before an assessment becomes current.
7. The assessment retains a historical snapshot while later state can independently invalidate current actionability.

## Components

| Component | Inputs | Output | Key invariant |
|---|---|---|---|
| Ingestion and canonicalization | Raw envelopes and fixture sources | Source versions and canonical events | Source identity and tenant scope are retained. |
| Economic obligations | Canonical events and business keys | Stable obligation identity | Similar subjects or amounts cannot substitute for the same economic opportunity. |
| Evidence | Evidence records, source versions, assertions | Supported, contradicted, or unavailable propositions | An evidence ID alone is not proof; JSON values are compared with type-aware semantics. |
| Synthetic authority | Versioned synthetic policy data | Applicable or unavailable authority | Unavailable authority cannot create a claim. |
| Ledger and reconciliation | Linked credits, reversals, pursuits, allocations | Known settlement and active-pursuit operands | Empty rows are not treated as known zero without reconciliation certainty. |
| Assessment engine | Obligation, evidence, authority, and ledger state | `SYNTHETIC_CLAIM_READY`, `REVIEW`, `RESOLVED`, or `ALREADY_PURSUED` | Recovery is economically conserved. |
| Guarded publication/export | Assessment and current revision | Current pointer or synthetic packet eligibility | A stale pre-mutation assessment cannot be blindly made current. |
| Historical snapshots | Assessment-time dependencies | Immutable decision-time proof | Historical proof remains separate from current status. |
| Benchmark/demo adapter | Isolated synthetic setup | Persisted real-engine assessment and report | Ground truth evaluates output; it does not configure the engine. |

## Decision and residual model

The assessment engine uses this residual accounting model:

~~~text
E = justified gross entitlement
C = net linked settlement
U = E - C
A = active pursuit allocation
R = U - A
~~~

An arithmetic residual is insufficient on its own. A claim-ready decision also requires applicable authority, mechanically supported current evidence, reconciliation completeness, valid identity linkage, and current decisive state.

## Model and agent usage

The authoritative recovery decision path is **deterministic**. The repository does not use an LLM, classifier, or agent to decide financial entitlement or change a monetary recommendation.

That is intentional. Financial recovery is safety-critical: a hallucinated policy, a coerced evidence value, or a guessed reconciliation result must not create a claim. The system treats missing or contradictory decisive inputs as `REVIEW`. Any future AI assistance may help a surrounding workflow, but it must not authorize a monetary entitlement or bypass this deterministic boundary.

The two CLI entry points are application-owned orchestration:

- `python -m recovery_manager demo` provisions fresh synthetic scenarios and prints persisted assessment IDs and decisions.
- `python -m recovery_manager evaluate` provisions the prediction-free benchmark through the real engine, reloads persisted assessments, then evaluates them against separate truth.

## Trust boundaries and tenant isolation

PostgreSQL is the primary trust boundary. Business records are tenant-scoped and protected with row-level security. The runtime uses transaction-local tenant context, and startup/readiness checks reject unsafe database principals.

| Role | Purpose |
|---|---|
| `recovery_app` | Tenant-scoped application/runtime access. |
| `recovery_worker` | Worker assessment execution and constrained guarded-publication operations. |
| `recovery_owner` | Migration, schema, and security-sensitive owner/setup operations. |

The roles are distinct. The normal runtime principal is not permitted to act as a superuser, RLS bypass principal, or protected-table owner.

## Publication, freshness, and concurrency

Publication is a guarded state transition rather than a blind write. The decisive revision and relevant current state are checked at the publication boundary. A settlement, active-pursuit, policy, or invalidation change can make a prior assessment stale for current action even though its historical snapshot remains valid.

The implementation includes bounded concurrent schedules around settlement/pursuit mutation, policy changes, and invalidation versus export. These tests demonstrate the covered orderings complete within finite database timeouts; they do not claim that every possible deadlock schedule is impossible.

## Historical provenance

The immutable dependency snapshot records what the engine used at assessment time. This includes policy/version, evidence identities, reconciliation state and cutoffs, settlement allocation contributors, reversal provenance and net contribution, pursuit allocation contributors, and the pursuit state used by the decision.

This enables the system to distinguish:

~~~text
historical proof at T0
≠
current actionability at T1
~~~

Later settlement changes or a pursuit moving from pending to settled do not rewrite the old proof. Older snapshots without contributor-level proof are rendered truthfully as legacy rather than reconstructed from mutable current ledger rows. Provenance/hash fields support traceability; they are not a claim of tamper-proof records.

## Benchmark and evaluation design

The authoritative benchmark uses an explicit isolated-database preflight before synthetic writes:

- `RECOVERY_BENCHMARK_DATABASE=true` is required.
- Runtime, worker, and owner endpoints must identify the same approved isolated database with distinct expected roles.
- The migration version is checked before provisioning.
- Benchmark transactions use bounded lock and statement timeouts.

The engine receives setup only. The adapter projects setup into PostgreSQL, runs the actual assessment/publication path, reloads the persisted snapshot, and reverse-maps pinned identities to the evaluator’s logical IDs. Unknown evidence mapping fails closed.

Evaluation is strict and one-to-one: it validates conflicting oracle rows, claim money and currency shapes, and matches prediction occurrences rather than value equality. This prevents duplicate claims from disappearing in exposure accounting.

## Important engineering decisions

| Decision | Why it matters |
|---|---|
| Unknown is explicit | Missing reconciliation, evidence, or authority cannot be silently converted into certainty. |
| Exact identities over similarity | Prevents same-subject, same-fee, or same-amount records from being conflated. |
| Deterministic monetary boundary | Prevents a model or heuristic from inventing entitlement. |
| Conservation through ledger operands | Settlement and active pursuit are deducted before a residual is actionable. |
| Historical snapshot plus freshness | Preserves a defensible historical decision without treating it as currently actionable forever. |
| Guarded publication | Makes current state checks part of the transition that exposes an assessment. |
| Database-enforced isolation | Keeps tenant scoping and role separation at the PostgreSQL boundary. |
| Prediction-free real-engine benchmark | Measures persisted engine behavior rather than replaying a desired result. |

## Repository map

~~~text
src/recovery_manager/    Application, domain logic, CLI, persistence, and benchmark adapter
alembic/                 PostgreSQL migrations and role/RLS controls
tests/                   Unit and PostgreSQL integration regressions
data/evaluation/         Prediction-free benchmark manifest and committed results
artifacts/evaluation/    Authoritative JSON and Markdown evaluation reports
.github/workflows/       CI configuration
README.md                Product narrative, setup, usage, and limitations
~~~

For setup, demo instructions, benchmark metrics, and scope limitations, see [README.md](README.md).
