# Recovery Manager

Recovery Manager is a deterministic, evidence-driven financial recovery engine that identifies justified recoverable value and refuses to recommend a claim when evidence, reconciliation, authority, or current economic state is uncertain.

Finding money that appears missing is easy. Proving that it is still recoverable, for the right obligation, without duplicating settlement or an active pursuit, is the hard part. Recovery Manager models that proof as a PostgreSQL-backed decision workflow with explicit evidence, authority, reconciliation, and historical provenance.

**Fast paths:** [Run the demo](#run-the-demo) · [Run the benchmark](#run-the-benchmark) · [Quickstart](#quickstart) · [Architecture](ARCHITECTURE.md)

> **Scope:** this project demonstrates synthetic recovery mechanics. It does not claim official Amazon policy accuracy, reimbursement eligibility, live filing, or guaranteed reimbursement.

## Why this matters

A charge can look wrong while still being impossible to recover safely. A naive recovery tool can double-claim money that was settled, claim value already under active pursuit, turn an unknown reconciliation result into zero, or treat a missing evidence record as proof.

Recovery Manager is built around the difference between **an apparent discrepancy** and **a justified, currently actionable recovery**. It produces an actionable recommendation only when the decisive inputs are known and valid. Otherwise, it keeps the case in `REVIEW`.

## See it work

After the isolated PostgreSQL setup in [Quickstart](#quickstart), run:

~~~bash
python -m recovery_manager demo
~~~

The application-owned demo provisions fresh synthetic state, runs the real worker path, persists assessments, and prints the resulting assessment IDs. It is not a pytest wrapper.

| Scenario | Actual result |
|---|---|
| Supported synthetic recovery | `SYNTHETIC_CLAIM_READY` — USD 2.00 |
| Evidence insufficient | `REVIEW` |
| Reconciliation unknown | `REVIEW` |
| Policy unavailable | `REVIEW` |
| Partial settlement | `SYNTHETIC_CLAIM_READY` — USD 1.00 |
| Fully settled | `RESOLVED` |
| Partial active pursuit | `SYNTHETIC_CLAIM_READY` — USD 1.00 |
| Fully pursued | `ALREADY_PURSUED` |

## What makes it different

- **Evidence-first decisions.** A recommendation requires mechanically supported, current evidence; an identifier alone is not proof.
- **Unknown is a real state.** Missing settlement or pursuit reconciliation does not become zero.
- **Economic conservation.** Linked settlement and active pursuit allocations reduce the amount that can remain actionable.
- **Exact financial identity.** Similar amounts or subjects do not make two obligations interchangeable.
- **Historical proof and current actionability are separate.** An old assessment retains its decision-time basis, while later changes can make it stale for current action.
- **Guarded publication.** A worker cannot simply publish an arbitrary current result; decisive state is rechecked at the publication boundary.
- **Real-engine evaluation.** The benchmark provisions isolated PostgreSQL state and reloads persisted assessments rather than replaying expected answers.

## How a decision works

~~~mermaid
flowchart TD
    A[Financial source record] --> B[Canonical event and obligation]
    B --> C[Evidence and authority checks]
    C --> D[Settlement reconciliation]
    D --> E[Active-pursuit reconciliation]
    E --> F[Entitlement and residual calculation]
    F --> G{Decision}
    G -->|supported and current| H[SYNTHETIC_CLAIM_READY]
    G -->|fully settled| I[RESOLVED]
    G -->|fully pursued| J[ALREADY_PURSUED]
    G -->|unknown or unsupported| K[REVIEW]
    H --> L[Guarded publication]
    I --> L
    J --> L
    K --> L
    L --> M[Historical assessment snapshot / synthetic export boundary]
~~~

The residual model is intentionally small and explicit:

~~~text
E = justified gross entitlement
C = net linked settlement
U = E - C
A = active pursuit allocation
R = U - A
~~~

| Symbol | Meaning |
|---|---|
| `E` | justified gross entitlement |
| `C` | net linked settlement |
| `U` | unresolved entitlement |
| `A` | value already under active pursuit |
| `R` | remaining actionable residual |

For example, USD 2.00 of justified entitlement, USD 0.50 already settled, and USD 0.50 already under active pursuit leaves USD 1.00. Arithmetic alone never authorizes a claim: evidence, authority, reconciliation completeness, and currentness must also permit action.

## Safety model

| Principle | Enforced behavior |
|---|---|
| Unknown ≠ zero | Unknown reconciliation produces `REVIEW`; an empty query is not treated as a known zero. |
| Missing evidence ≠ proof | Evidence assertions prove an exact source/version/subject/value proposition. |
| Historical ready ≠ currently actionable | Revision and freshness checks can invalidate an old current assessment without rewriting its history. |
| One entitlement cannot be recovered twice | Linked settlement and active-pursuit allocations reduce the residual. |
| Infrastructure failure ≠ `REVIEW` | Invalid configuration, preflight failure, or unavailable database makes the command fail. |
| Ground truth cannot control the engine | Benchmark truth is scored after real engine execution; it is not passed into provisioning or assessment. |

The authoritative monetary boundary is deterministic and evidence-constrained. This repository does not use an LLM or other model to determine financial entitlement. AI assistance, if added around the workflow, must not override this safety boundary.

## Architecture and trust boundaries

The application accepts source data, builds tenant-scoped canonical and economic records, evaluates evidence and synthetic authority, reconciles linked ledger state, and persists an assessment. PostgreSQL is the data and security boundary.

| Role | Responsibility |
|---|---|
| `recovery_app` | Normal tenant-scoped runtime operations. |
| `recovery_worker` | Deterministic assessment execution and guarded publication. |
| `recovery_owner` | Schema, migration, and security-sensitive setup operations. |

Protected tables use PostgreSQL row-level security with tenant context. The runtime verifies that its principal is not a superuser, RLS bypass role, or protected-table owner. See [ARCHITECTURE.md](ARCHITECTURE.md) for components, data flow, model usage, publication/currentness, and historical provenance.

## Historical provenance

An immutable assessment snapshot preserves the decision-time dependencies used to reach its result: applicable policy/version, evidence, reconciliation state and cutoffs, settlement contributors, pursuit allocations, and pursuit state at assessment time. Later ledger or policy changes do not rewrite that historical proof.

Historical proof is deliberately distinct from current status. An assessment can accurately describe what was supported when created while no longer being safe to publish or export now.

## Evaluation

The authoritative benchmark is defined in [`data/evaluation/recovery-engine-benchmark.json`](data/evaluation/recovery-engine-benchmark.json). It is prediction-free: setup data provisions isolated PostgreSQL state; the real engine creates and persists an assessment; the adapter reloads that assessment and reconstructs the prediction from the persisted snapshot. Ground truth is used only for evaluation.

The evaluator uses strict opportunity, obligation, basis, amount, currency, and evidence identity matching. It matches prediction occurrences one-to-one, so an equal-valued duplicate claim remains unsupported exposure. Unsupported exposure is also bucketed by currency.

| Metric | Result |
|---|---:|
| Strict claim precision | `1.0` |
| Decision coverage | `0.25` |
| Unsupported exposure | `{}` |
| Runner mode | `real_engine` |
| Engine-benchmarked revision | `8ff4e1598fdbfc43405726dac909afd09091fc9a` |

The four benchmark cases include one supported synthetic claim and three conservative `REVIEW` outcomes for insufficient evidence, unknown reconciliation, and unavailable policy. Decision coverage is deliberately conservative: `REVIEW` is not converted into a successful claim to increase a metric. These results validate the synthetic mechanics represented in this repository; they are not an operational Amazon-policy accuracy claim.

Read the committed [JSON result](artifacts/evaluation/repair09-results.json) and [Markdown report](artifacts/evaluation/repair09-results.md) for case-level provenance and limitations.

## Technical highlights

- PostgreSQL RLS, forced tenant scoping, and separate runtime, worker, and owner roles.
- Canonical event and obligation identities that prevent same-subject or same-amount confusion.
- Exact, recursive JSON-type-aware evidence comparison: booleans and numbers do not collapse into one another.
- Explicit reconciliation completeness, including settlement reversals and active-pursuit state.
- Immutable decision-time ledger proof with contributor identities and decision-time pursuit state.
- Bounded concurrent publication/invalidation schedules that protect against stale current results.
- Isolated benchmark preflight that verifies designation, endpoint equality, roles, migration version, and bounded database timeouts.
- Strict evaluator validation for truth conflicts, claim money, currency shape, and one-to-one matching.

## Quickstart

Recovery Manager requires **Python 3.12** and **PostgreSQL 16**. Use a dedicated database for demo, evaluation, and required PostgreSQL tests. The examples use `recovery_test`, which satisfies the test harness safety guard; never point these commands at a development or production database.

1. Copy `.env.example` to an uncommitted `.env` and replace password placeholders.
2. Install dependencies and start PostgreSQL:

~~~bash
python -m pip install -e ".[dev]"
docker compose up -d postgres
~~~

3. Create the isolated database once, using the local bootstrap administrator:

~~~bash
docker compose exec -T postgres psql -U recovery_admin -d recovery -c "CREATE DATABASE recovery_test"
docker compose exec -T postgres psql -U recovery_admin -d recovery_test -c "GRANT CONNECT ON DATABASE recovery_test TO recovery_owner, recovery_app, recovery_worker; GRANT USAGE, CREATE ON SCHEMA public TO recovery_owner; GRANT USAGE ON SCHEMA public TO recovery_app, recovery_worker"
~~~

Docker Compose and application settings can read `.env`. Alembic and the explicit test configuration read exported shell variables, so export the following in the same shell used for migrations, tests, demo, or evaluation. Replace password, host, and port placeholders with your isolated instance. Percent-encode reserved characters in URL passwords.

**PowerShell**

~~~powershell
$env:RECOVERY_DATABASE_URL = 'postgresql+psycopg://recovery_app:<app-password>@localhost:<port>/recovery_test'
$env:RECOVERY_MIGRATION_DATABASE_URL = 'postgresql+psycopg://recovery_owner:<owner-password>@localhost:<port>/recovery_test'
$env:RECOVERY_WORKER_DATABASE_URL = 'postgresql+psycopg://recovery_worker:<worker-password>@localhost:<port>/recovery_test'
$env:TEST_RUNTIME_DATABASE_URL = $env:RECOVERY_DATABASE_URL
$env:TEST_OWNER_DATABASE_URL = $env:RECOVERY_MIGRATION_DATABASE_URL
$env:RECOVERY_REQUIRE_POSTGRES = 'true'
$env:RECOVERY_BENCHMARK_DATABASE = 'true'
$env:RECOVERY_DEVELOPMENT_MODE = 'true'
$env:RECOVERY_DEV_CREDENTIALS = '{"local-token":{"org_id":"demo_org","actor_id":"demo_operator","role":"operator"}}'
~~~

**POSIX shell**

~~~bash
export RECOVERY_DATABASE_URL='postgresql+psycopg://recovery_app:<app-password>@localhost:<port>/recovery_test'
export RECOVERY_MIGRATION_DATABASE_URL='postgresql+psycopg://recovery_owner:<owner-password>@localhost:<port>/recovery_test'
export RECOVERY_WORKER_DATABASE_URL='postgresql+psycopg://recovery_worker:<worker-password>@localhost:<port>/recovery_test'
export TEST_RUNTIME_DATABASE_URL="$RECOVERY_DATABASE_URL"
export TEST_OWNER_DATABASE_URL="$RECOVERY_MIGRATION_DATABASE_URL"
export RECOVERY_REQUIRE_POSTGRES=true
export RECOVERY_BENCHMARK_DATABASE=true
export RECOVERY_DEVELOPMENT_MODE=true
export RECOVERY_DEV_CREDENTIALS='{"local-token":{"org_id":"demo_org","actor_id":"demo_operator","role":"operator"}}'
~~~

### Run migrations

~~~bash
alembic upgrade head
~~~

### Run the demo

~~~bash
python -m recovery_manager demo
~~~

The command requires the explicit isolated benchmark designation and prints actual persisted assessment IDs and decision details.

### Run the benchmark

~~~bash
python -m recovery_manager evaluate
~~~

It writes authoritative JSON and derived Markdown results under `artifacts/evaluation/`. Review generated output before committing it; the command records the current Git revision as its engine provenance.

### Run tests and checks

~~~bash
python -m pytest tests -q
python -m ruff check src tests alembic
python -m mypy src
alembic current
alembic heads
alembic check
~~~

`RECOVERY_REQUIRE_POSTGRES=true` makes required PostgreSQL testing fail if the database is unavailable rather than silently skipping it.

## Verification evidence

The final implementation passed the required PostgreSQL firewall with **292 passed, 0 failed, 0 skipped** and one unchanged Starlette warning. Focused adversarial verification also exercised typed evidence values, cutoff omission versus explicit `None`, evaluator truth conflicts, money and currency validation, duplicate claim accounting, and fresh-shell configuration. Ruff, mypy, and Alembic migration consistency passed for the audited implementation.

## Repository structure

~~~text
src/recovery_manager/    Application, deterministic engine, CLI, and database boundary
alembic/                 PostgreSQL migrations
tests/                   Unit and PostgreSQL regression coverage
data/evaluation/         Benchmark manifest and committed authoritative result artifacts
artifacts/evaluation/    Generated authoritative evaluation reports
.github/workflows/       Continuous integration
ARCHITECTURE.md          System design and engineering decisions
~~~

## Scope and limitations

- Recovery-policy mechanics and benchmark amounts are synthetic. No authoritative operational Amazon recovery policy is bundled.
- The project makes no claim about official Amazon reimbursement eligibility, guaranteed reimbursement, or full operational-policy interoperability.
- Export produces a synthetic claim packet; it does not file a claim with Amazon.
- Provenance and hash fields aid traceability; they are not tamper-proof security guarantees.
- The benchmark measures the synthetic mechanics represented here. It is not a claim of real-world recovery accuracy.
