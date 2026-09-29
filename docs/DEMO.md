# Bounded Recovery demo

Run this against the isolated PostgreSQL test database configured by the project:

```bash
python -m recovery_manager demo
```

The command executes real worker assessment, guarded publication, export, ledger, evidence,
reconciliation, and freshness integration cases. It does not insert a claim-ready assessment,
current pointer, or packet directly.

It demonstrates a USD 2 trusted-synthetic claim and export, partial/full settlement and
already-pursued outcomes, evidence and reconciliation REVIEW outcomes, and stale-export
rejection. The command is intentionally bounded and rerunnable because the integration
harness uses isolated fixture state.

For the locked evaluation report run:

```bash
python -m recovery_manager evaluate
```

This loads the prediction-free engine benchmark, provisions isolated PostgreSQL state, executes
the real Recovery assessment/publication path, and writes JSON and Markdown results under
`artifacts/evaluation/`. `repair09-benchmark.json` is a pure evaluator fixture, not the
judge-facing benchmark. Synthetic mechanics are not operational Amazon policy validation;
ordinary policy-unavailable cases remain conservative REVIEW outcomes.
