# Cube Buildathon · 05 · Recovery Manager

**Commerce Context stream · Round 2 · Individual Build**

> Five agents, one unit, one record that follows it.
> A physical product arrives, gets prepped, gets shipped, comes back. At every step a person makes a fast judgment that nobody records. **You build the agent that makes one of those judgments, and leaves proof.**

**New here? Read these first:**

1. [`GITHUB-GUIDE.md`](GITHUB-GUIDE.md) explains how to fork the repository, set it up, build and push your work.
2. [`RULES.md`](RULES.md) covers the repository and engineering rules.

---

## Your problem statement: Recovery Manager

|                              |                                                          |
| ---------------------------- | -------------------------------------------------------- |
| **Position in the chain**    | Step 5 of 5. Money back. This step has no camera.        |
| **Customer**                 | Anyone being charged fees they do not owe                |
| **What gets recorded**       | Claim filed                                              |
| **Who consumes your output** | The seller, and whoever reviews the claim at the channel |

Amazon charges inbound defect fees, loses units, damages inventory and mis-weighs parcels. Sellers are owed reimbursements they never claim, and charged fees they cannot contest, because contesting requires evidence and they have none. Today this is done by hand, by agencies taking a percentage, or not at all.

**This is not a vision agent.** No camera, no capture surface. It reads the evidence records the other four Managers produce, matches them against channel fee and reimbursement reports, and assembles a claim.

* Ingest a fee or reimbursement report and parse the charges
* Match each charge to the unit evidence covering it
* Decide whether the evidence contradicts the charge, supports it, or is insufficient
* Assemble a disputable claim with evidence attached and a dollar figure
* State explicitly what it cannot claim, and why

> **Build against the official evidence contract.** Recovery depends on the evidence produced by the other four Managers. For Round 2, use the evidence contract provided by the organisers as the baseline rather than creating a separate cross-pod contract.

> **Your eval is different.** Others measure a model against human labels on units. You measure claim correctness on charges, and you report precision, because a wrongly filed claim costs a seller standing with the channel while a missed one costs only money.

### The chain you are part of

```text
 Supplier delivery      Inbound to Amazon     Outbound to buyer     Customer return        Money back
 ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
 │ 01 Receiving │ ───▶ │ 02 Prep      │ ───▶ │ 03 Pack      │ ───▶ │ 04 Returns   │      │ 05 Recovery  │
 │ condition on │      │ compliance   │      │ contents at  │      │ condition &  │      │ reads all    │
 │ arrival      │      │ proof        │      │ seal         │      │ disposition  │      │ four → claim │
 └──────┬───────┘      └──────┬───────┘      └──────┬───────┘      └──────┬───────┘      └──────▲───────┘
        └─────────────────────┴─────────────────────┴─────────────────────┴─────────────────────┘
```

The first four are the same machine: a camera, a model, and a decision bound to a record. What changes is the ruleset, the buyer and the moment. The fifth has no camera. It turns the other four's records into a claim.

Your output has to be usable by another pod. That's deliberate, and it's scored.

---

## Reference data

`data/` holds a **dummy** CSV for reference while you design and build. Its columns and meanings are listed in [`data/README.md`](data/README.md).

**The data is synthetic.** The SKUs, ASINs, FNSKUs, orders, suppliers, operators and amounts are all invented. The requirement flags and fee amounts are **not** Amazon's real rules or fees. Engineering rule 5 applies: look the authoritative rule up. The `photo_refs` paths are placeholders, and no images ship with this repo. Your fixtures and eval set are yours to capture.

All five buildathon repos share the same `unit_id` values (`UNIT-0001` … `UNIT-0100`). You can follow one unit from receiving through recovery, the same way the real records will be joined. In the sample, each unit takes one route: **FBA** (prep, then Amazon ships it and charges fees) or **merchant-fulfilled / 3PL** (the seller packs it). So a unit has a Prep record or a Pack record, never both.

Recovery also gets `data/upstream/`, a copy of the other four files, so you can practise the join before Round 3 integration.

## Quickstart and bounded demo

Recovery Manager uses Python 3.12 and PostgreSQL 16. The demo, evaluation, migrations, and
required PostgreSQL tests must use one **isolated** database with the separate
`recovery_owner`, `recovery_app`, and `recovery_worker` roles. The examples below use
`recovery_test`, which satisfies the destructive-test safety guard; do not point them at a
development or production database. The example host and port are placeholders, not required
machine-specific values.

First copy `.env.example` to an uncommitted `.env`, replace its password placeholders, install
the project, and start the loopback-only service. Docker Compose reads `.env` for bootstrap
passwords, but Alembic and the Python commands below read **exported shell variables**; merely
creating `.env` does not configure those commands.

```bash
python -m pip install -e ".[dev]"
docker compose up -d postgres
```

Create a separate test database once, using the local bootstrap administrator. This grants the
least privileges needed by the isolated roles to migrate and run tests.

```bash
docker compose exec -T postgres psql -U recovery_admin -d recovery -c "CREATE DATABASE recovery_test"
docker compose exec -T postgres psql -U recovery_admin -d recovery_test -c "GRANT CONNECT ON DATABASE recovery_test TO recovery_owner, recovery_app, recovery_worker; GRANT USAGE, CREATE ON SCHEMA public TO recovery_owner; GRANT USAGE ON SCHEMA public TO recovery_app, recovery_worker"
```

Set these variables in the same shell that will run Alembic, tests, demo, or evaluation. Replace
the password placeholders and host/port as appropriate for your isolated instance.

```powershell
$env:RECOVERY_DATABASE_URL = 'postgresql+psycopg://recovery_app:<app-password>@localhost:<port>/recovery_test'
$env:RECOVERY_MIGRATION_DATABASE_URL = 'postgresql+psycopg://recovery_owner:<owner-password>@localhost:<port>/recovery_test'
$env:RECOVERY_WORKER_DATABASE_URL = 'postgresql+psycopg://recovery_worker:<worker-password>@localhost:<port>/recovery_test'
$env:TEST_RUNTIME_DATABASE_URL = $env:RECOVERY_DATABASE_URL
$env:TEST_OWNER_DATABASE_URL = $env:RECOVERY_MIGRATION_DATABASE_URL
$env:RECOVERY_REQUIRE_POSTGRES = 'true'
$env:RECOVERY_BENCHMARK_DATABASE = 'true'
$env:RECOVERY_DEVELOPMENT_MODE = 'true'
$env:RECOVERY_DEV_CREDENTIALS = '{"local-token":{"org_id":"demo_org","actor_id":"demo_operator","role":"operator"}}'
```

```bash
export RECOVERY_DATABASE_URL='postgresql+psycopg://recovery_app:<app-password>@localhost:<port>/recovery_test'
export RECOVERY_MIGRATION_DATABASE_URL='postgresql+psycopg://recovery_owner:<owner-password>@localhost:<port>/recovery_test'
export RECOVERY_WORKER_DATABASE_URL='postgresql+psycopg://recovery_worker:<worker-password>@localhost:<port>/recovery_test'
export TEST_RUNTIME_DATABASE_URL="$RECOVERY_DATABASE_URL"
export TEST_OWNER_DATABASE_URL="$RECOVERY_MIGRATION_DATABASE_URL"
export RECOVERY_REQUIRE_POSTGRES=true
export RECOVERY_BENCHMARK_DATABASE=true
export RECOVERY_DEVELOPMENT_MODE=true
export RECOVERY_DEV_CREDENTIALS='{"local-token":{"org_id":"demo_org","actor_id":"demo_operator","role":"operator"}}'
```

Run migrations with the owner URL, then execute the required PostgreSQL suite, the application-owned
demo, and the authoritative evaluation:

```bash
alembic upgrade head
python -m pytest tests -q
python -m recovery_manager demo
python -m recovery_manager evaluate
uvicorn recovery_manager.api:app --reload
```

The demo is application-owned orchestration: it provisions fresh synthetic world state, runs
the worker assessment and guarded publication, reloads each immutable assessment, and prints
the actual assessment IDs, logical/persisted identities, evidence, ledger state, and outcome.
It covers a USD 2 synthetic claim, insufficient evidence, unknown reconciliation, unavailable
policy, partial/full settlement, and partial/full active pursuit. It creates no direct
`CLAIM_READY` assessment or current-pointer rows.

The evaluation command loads the prediction-free
`data/evaluation/recovery-engine-benchmark.json`, executes real isolated-PostgreSQL Recovery
assessment/publication for every case, and writes `artifacts/evaluation/repair09-results.json`
and `artifacts/evaluation/repair09-results.md`. The JSON artifact is authoritative; the
Markdown report is derived from it. `repair09-benchmark.json` remains a pure evaluator fixture.

Recovery Manager is deterministic on this path: financial event → evidence → trusted
synthetic authority → entitlement derivation → reconciliation → assessment → synthetic
claim packet, REVIEW, RESOLVED, or ALREADY_PURSUED.

### Limits

- Synthetic policy amounts are synthetic. No authoritative operational Amazon recovery
  policy is bundled; policy-unavailable inputs remain REVIEW.
- AI does not decide financial readiness. The safety-critical decision path is deterministic.
- Hashes are provenance and integrity identifiers, not tamper-proof guarantees.
- Export creates a synthetic claim packet; it does not file a claim with Amazon.
- Official evidence-contract interoperability remains disabled until authoritative artifacts
  are available.

---

## How this works

You have a defined problem statement, supporting domain information and an engineering repository to build from. Understand the customer and operational workflow before writing code, then build and measure whether the solution works.

Your goal is to turn the Recovery Manager problem into a working, measurable agent.

### What you're given

* This problem statement
* A domain brief covering the real economics, fee structures and what a working day in a warehouse looks like *(shared by the organisers)*
* The engineering rules in [`RULES.md`](RULES.md)
* Repository data and supporting resources
* One fully worked package for Returns Manager (customer letter, PR/FAQ, one-pager) as a reference for the standard expected. **Read it. Don't copy it.**

### What you produce

Build your solution in **your own GitHub fork**.

Your final Round 2 submission should include:

* A working Recovery Manager
* A `README.md` explaining your solution, setup, assumptions and limitations
* An `ARCHITECTURE.md`
* An eval report/results with numbers and named failure modes
* A working demo/video
* A deployment URL, where applicable
* Your mandatory LinkedIn post URL

## Build and submission flow

```text
Understand
    ↓
Build
    ↓
Test
    ↓
Evaluate
    ↓
Document
    ↓
Demo / Deploy
    ↓
Submit
```

Round 2 is an **individual build**.

The official build phase begins on **25 September 2026 at 9:00 AM IST**.

Submissions open from **27 September 2026**.

The final submission deadline is **1 October 2026 at 6:00 PM IST**.

The submission form closes permanently at the deadline. **There is no resubmission.**

All code commits forming your Round 2 submission must be made during the authorised build phase. Do not continue making Round 2 code changes after the build phase ends.

---

## Evaluation

Recovery Manager is evaluated differently from the vision-based Managers.

The primary question is:

> **When Recovery Manager recommends a claim, is that claim actually supported by the available evidence?**

Your evaluation should focus on:

* charge/report parsing,
* charge-to-unit matching,
* upstream evidence matching,
* evidence interpretation,
* claim correctness,
* claim precision,
* uncertainty/review handling,
* false claims and missed recoverable claims,
* important failure modes.

Report the methodology clearly.

### Primary metric

```text
Claim Precision
=
Correctly Supported Claims
--------------------------
All Claims Recommended
```

Where measurable, also report:

* total charges evaluated,
* claims recommended,
* correctly supported claims,
* incorrectly recommended claims,
* missed recoverable claims,
* `UNCERTAIN` / review rate,
* latency/cost where relevant.

---

## Round 2 Evaluation — 100 Points

| Criterion                                    |  Points |
| -------------------------------------------- | ------: |
| Problem Understanding & Solution Relevance   |  **15** |
| Agent Functionality & Decision Quality       |  **25** |
| Evaluation, Accuracy & Uncertainty Handling  |  **25** |
| Evidence, Traceability & Engineering Quality |  **20** |
| UX, Demo & Documentation                     |  **15** |
| **TOTAL**                                    | **100** |

For Recovery Manager, the evaluation focus is on **claim correctness and evidence quality**, not image-level accuracy.

---

## Evidence and decision traceability

Your Recovery Manager should make the claim traceable to the evidence that supports it.

At minimum, the workflow should make it possible to understand:

```text
Charge
   ↓
Unit
   ↓
Upstream Evidence
   ↓
Evidence Interpretation
   ↓
Claim Decision
   ↓
Supporting Evidence
```

Use the official evidence contract provided by the organisers as the baseline for interoperability.

Do not create a separate negotiated evidence schema for Round 2.

---

## PASS · FAIL · UNCERTAIN

For upstream checks and evidence states:

* **PASS** — the evidence supports the condition.
* **FAIL** — the evidence shows the condition is not met.
* **UNCERTAIN** — the evidence is insufficient for a reliable judgment.

`UNCERTAIN` is not simply a low-confidence PASS.

For Recovery, missing, contradictory or insufficient evidence should lead to an appropriate review/uncertain outcome rather than an unsupported claim.

---

## Engineering expectations

* **Tenancy isolation:** If you store persistent data, keep organisation/client data properly isolated.
* **Batch model calls:** Avoid unnecessary repeated model calls.
* **Fail open:** A model or dependency failure should not silently discard incoming information. Preserve the available information and move the case into an appropriate pending/review state.
* **Authoritative rules:** Where an external rule is required, use the authoritative source rather than relying on model memory or synthetic sample values.
* **Evidence traceability:** Preserve the records used to support recovery decisions.

---

## What we're being straight with you about

* **The core assumption is untested.** Nobody knows yet whether the evidence produced by automated upstream Managers will be reliable enough to support recovery claims at scale. Finding out that an assumption does not hold, and documenting that clearly, counts as a useful outcome.
* **Nobody has spoken to a customer yet.** If you can get a real prep center or seller on a call, ask them to rank the five problems by urgency. Don't ask whether they'd buy what you're building.
* **The background documents disagree in places.** A contradiction is a finding. Raise it as an Issue labelled `finding`.

---

## Submission

### Submissions open

**27 September 2026**

### Final deadline

**1 October 2026 · 6:00 PM IST**

The submission form closes permanently at the deadline.

**There is no reopening and no resubmission.**

Your final submission should include:

* your GitHub fork,
* working Recovery Manager,
* `README.md`,
* `ARCHITECTURE.md`,
* evaluation results,
* demo video,
* deployment URL where applicable,
* LinkedIn post URL.

### LinkedIn — Mandatory

Publish a LinkedIn post about your Round 2 build.

The post must:

* mention your Recovery Manager build,
* explain what you built,
* tag **CodeQuesters**,
* tag **Sydon.AI**.

Include the LinkedIn post URL in the submission form.

---

## Commit rule

All code commits forming your Round 2 submission must be made during the authorised build phase.

Round 2 begins:

**25 September 2026 · 9:00 AM IST**

Once the build phase ends, do not continue making Round 2 code changes.

---

## Round 2 → Round 3

Round 2 is about your **individual Recovery Manager**.

Participants selected for Round 3 will work in five-person Pods combining:

```text
Receiving Manager
+
Prep Manager
+
Pack Manager
+
Returns Manager
+
Recovery Manager
```

The objective is to integrate the five specialised agents into one connected end-to-end commerce system.

Your Round 2 implementation should therefore have clear outputs, structured evidence and an understandable interface for downstream integration.

---

*Cube Buildathon · Commerce Context*
