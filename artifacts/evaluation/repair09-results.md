# Recovery evaluation report

Benchmark: `recovery-engine-synthetic-v1`
Runner mode: `real_engine`
Engine-benchmarked revision: `74a7f7702ecce1de8d3be2ad865f12d0126faba6`
Cases: 4
Strict claim precision: 1.0
Decision coverage: 0.25
Unsupported exposure by currency: {}

Synthetic-mechanics results validate the deterministic synthetic contract only.
Operational recovery-policy accuracy is not measured because authoritative operational policy is unavailable.

## Engine executions

- `synthetic-positive`: recommendation=SYNTHETIC_CLAIM_READY; basis=INVALID_FEE; stratum=SYNTHETIC_MECHANICS; currency=USD; amount_minor=200; evidence=E-FEE-VALID
- `synthetic-evidence-insufficient`: recommendation=REVIEW; basis=INVALID_FEE; stratum=SYNTHETIC_MECHANICS; currency=None; amount_minor=None; evidence=E-FEE-VALID
- `synthetic-reconciliation-unknown`: recommendation=REVIEW; basis=INVALID_FEE; stratum=SYNTHETIC_MECHANICS; currency=None; amount_minor=None; evidence=E-FEE-VALID
- `policy-unavailable`: recommendation=REVIEW; basis=INVALID_FEE; stratum=POLICY_UNAVAILABLE; currency=None; amount_minor=None; evidence=E-FEE-VALID
