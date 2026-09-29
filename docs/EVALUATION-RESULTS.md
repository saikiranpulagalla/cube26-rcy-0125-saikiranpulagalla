# Recovery evaluation results

Evaluation is frozen by dependency group: a group may appear in only one of `dev`,
`validation`, or locked holdout. Results are always stratified as `SYNTHETIC_MECHANICS`,
`POLICY_UNAVAILABLE`, or `VERIFIED_POLICY`; this project has no verified-policy cases.

Run `python -m recovery_manager evaluate`. It loads the prediction-free
`data/evaluation/recovery-engine-benchmark.json`, provisions each case on isolated PostgreSQL,
runs guarded Recovery assessment publication, reloads the immutable assessment snapshot, and
only then scores the resulting prediction. It writes machine-readable JSON and derived Markdown
under `artifacts/evaluation/`.

`data/evaluation/repair09-benchmark.json` remains a pure-evaluator regression fixture. It is not
the judge-facing engine benchmark and its recorded prediction fields are rejected by the
authoritative engine-manifest loader.

The current real-engine benchmark has four cases: a valid synthetic USD 2 claim, insufficient
evidence, unknown reconciliation, and unavailable policy authority. Synthetic mechanics are
scored separately from policy-unavailable behavior; this is not a measure of operational
Amazon-policy accuracy.

The evaluator reports strict claim precision/recall, exact amount accuracy, evidence
attribution precision, evidence sufficiency, decision coverage, review rate, and
currency-separated unsupported exposure. A run with no claim predictions reports precision
as N/A, never 100%.

## Judge demo

1. Use the v0.5 synthetic mechanics fixture: USD 10 observed, USD 8 valid, resulting
   in `SYNTHETIC_CLAIM_READY` USD 2.
2. Allocate USD 1 settlement and reassess: USD 1 remains.
3. Allocate active pursuit for the remainder and reassess: `ALREADY_PURSUED`.
4. Remove decisive evidence: `REVIEW`.
5. Use ordinary organizer data without a verified policy: `REVIEW`; it never receives
   synthetic policy authority.
6. Revoke decisive evidence: the old current assessment reads `STALE`.
7. Request an Alpha assessment with Bravo credentials: the review route returns 404.

Synthetic mechanics are an engineering fixture, **not** organizer ground truth or real
channel policy. Operational policy, official evidence compatibility, AI assistance, and
live filing remain disabled.
