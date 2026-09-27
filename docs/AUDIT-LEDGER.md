# Audit ledger

| Version | Base | Candidate | Migrations | Mandatory checks | P0 status | External blockers |
|---|---|---|---|---|---|---|
| v0.1 repair | `51ce94a` | recorded in the Git checkpoint map/report | `0002_v01_repair`, `0003_v01_readiness` | migration upgrade/check, Ruff, mypy, PostgreSQL suite, CLI smoke | internal Terra gate passed | official evidence contract and authoritative operational policy remain unavailable |
| v0.2 canonical sources | `30c9371` | recorded in the Git checkpoint map/report | `0004_v02_sources` | fixture accounting, strict parse, row-order, PostgreSQL canonicalization | pending final checkpoint gate | source data remains synthetic; official contracts/policy unavailable |
| v0.5 deterministic recovery | `16f92f2` | `74cdd687b4d68c8604dfc380ede1c70870bf3600` | `0008`–`0012` | synthetic control, tenant lock, export concurrency, immutability, rollback, static checks | pending Astra audit | synthetic-only mechanics; no operational policy, official contract, AI, or filing |
| v0.6 review UI | `74cdd68` | `4970590ecd1f5eef9a8faa7a7f6c3d634204a207` | unchanged | authenticated review route, cross-tenant 404, stale display, Ruff, mypy | pending Astra audit | AI intentionally disabled; read-only UI only |

The ledger records evidence locations; it does not replace Astra’s independent audit.
