# Frozen evaluation specification — pre-v0.2

No performance result is claimed in v0.1. This specification prevents later metric design from being tuned around implementation output.

## Split rule

Split dependency-connected groups, never merely rows or `unit_id`. A group joins records sharing an economic obligation, source incident, consequential shipment, evidence/revision lineage, settlement/pursuit allocation, or renamed scenario copy. Groups cannot span dev, validation and holdout.

The final holdout remains inaccessible to ordinary implementation tests. If independent custody is unavailable, call it an author-held locked set, not independent blind evaluation.

## Gold requirements

Gold records must contain opportunity identity, recovery basis, proposition, conclusion/action, amount derivation/currency, quantity/coverage, evidence versions, settlement/pursuit allocations, policy applicability/source, cutoff and annotation provenance. `NOT_DETERMINABLE` is valid. Human agreement cannot manufacture policy authority.

## Metric definitions

Let `N` be fixed benchmark opportunities, `G` known positive new-pursuit residual opportunities, `P` machine recommendations and `TP` one-to-one strict matches that have correct obligation, basis, amount/currency, coverage and decisive evidence.

- Strict precision: `TP / |P|`; N/A when `|P| = 0`.
- Strict recall: `TP / |G|`; N/A when no known positive opportunities.
- Claim rate: opportunities with a claim / `N`.
- Assessment completion: completed machine assessments / `N`.
- Decision coverage: determinate automatic actions / `N`.
- Evidence attribution precision: valid/relevant/admissible cited fact edges / all predicted fact edges.
- Evidence sufficiency: claims whose cited facts establish every premise / `|P|`.
- Exact amount accuracy: exact amount-and-currency matches among opportunity/basis matches.
- False exposure, overclaim exposure and missed justified value are calculated separately per currency; correct recovery never offsets false exposure.

SILENT, UNCERTAIN, REVIEW, policy-unavailable and execution failure retain separate denominators and can overlap. Machine-only metrics and post-human outcomes remain separate.

## Synthetic mechanics fixture

An explicitly labeled `SYNTHETIC MECHANICS FIXTURE — NOT ORGANIZER GROUND TRUTH OR CHANNEL POLICY` may prove calculation mechanics only. It must state a synthetic rule, obligation, coverage, evidence, full ledger and expected residual. It cannot activate operational capability.
