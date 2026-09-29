# Bounded Recovery demo

Run this against the isolated PostgreSQL test database configured by the project:

```bash
python -m recovery_manager demo
```

The command is application-owned orchestration, not a pytest wrapper. It provisions fresh
synthetic world state, runs the real worker assessment and guarded publication, reloads the
persisted immutable assessment, and prints the resulting assessment ID, recommendation, basis,
logical and persisted identities, evidence, and ledger values. It does not insert a claim-ready
assessment, current pointer, or packet directly.

It demonstrates a USD 2 trusted-synthetic claim, partial/full settlement, partial/full active
pursuit, evidence and reconciliation REVIEW outcomes, and policy-unavailable REVIEW. Set
`RECOVERY_BENCHMARK_DATABASE=true` only after configuring an isolated PostgreSQL database with
the owner, runtime, and worker URLs all targeting that same database. Preflight rejects any
other configuration before demo provisioning.

For the locked evaluation report run:

```bash
python -m recovery_manager evaluate
```

This loads the prediction-free engine benchmark, provisions isolated PostgreSQL state, executes
the real Recovery assessment/publication path, and writes JSON and Markdown results under
`artifacts/evaluation/`. `repair09-benchmark.json` is a pure evaluator fixture, not the
judge-facing benchmark. Synthetic mechanics are not operational Amazon policy validation;
ordinary policy-unavailable cases remain conservative REVIEW outcomes.
