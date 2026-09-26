# Audit ledger

| Version | Base | Candidate | Migrations | Mandatory checks | P0 status | External blockers |
|---|---|---|---|---|---|---|
| v0.1 repair | `51ce94a` | recorded in the Git checkpoint map/report | `0002_v01_repair`, `0003_v01_readiness` | migration upgrade/check, Ruff, mypy, PostgreSQL suite, CLI smoke | internal Terra gate passed | official evidence contract and authoritative operational policy remain unavailable |
| v0.2 canonical sources | `30c9371` | recorded in the Git checkpoint map/report | `0004_v02_sources` | fixture accounting, strict parse, row-order, PostgreSQL canonicalization | pending final checkpoint gate | source data remains synthetic; official contracts/policy unavailable |

The ledger records evidence locations; it does not replace Astra’s independent audit.
