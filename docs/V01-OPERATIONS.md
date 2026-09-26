# v0.1 operations

## Security boundary

Copy `.env.example` to an uncommitted `.env`, replace every placeholder password, and use
`docker compose up postgres`. The local database is bound to loopback and password-authenticated;
the admin, migration owner, API runtime, and worker runtime use separate credentials. The sample
development credentials activate only when `RECOVERY_DEVELOPMENT_MODE=true` is explicit.

`recovery_app` may only add raw envelopes, work intents, and audit events. `recovery_worker` alone
may perform constrained work lifecycle changes. Raw envelopes and audit rows are append-only.

## Required verification

`make verify` is the mandatory gate. It requires PostgreSQL and fails rather than skipping security
or integration tests. It runs migration drift, lint, typing, and the full test suite. The CI workflow
also smoke-tests every headless command.

## Local setup

Copy `.env.example` to an uncommitted `.env`, install Python 3.12 dependencies, start PostgreSQL, then migrate as the owner role:

```powershell
python -m pip install -e ".[dev]"
docker compose up -d postgres
$env:RECOVERY_MIGRATION_DATABASE_URL = "postgresql+psycopg://recovery_owner@localhost:5432/recovery"
alembic upgrade head
```

Run the application with the restricted runtime URL from `.env.example`:

```powershell
uvicorn recovery_manager.api:app --reload
```

`/healthz` reports process health. `/readyz` also verifies database reachability and that the runtime role is not unsafe.

## Development authentication

The only built-in credentials are deliberately non-secret local/demo tokens in `RECOVERY_DEV_CREDENTIALS`. They map to a fixed tenant, actor and role. Send a credential in `X-Development-Credential`. The request body cannot select `org_id`.

This is not a production identity provider. A future replacement must preserve the trusted principal → transaction-local tenant context boundary.

## API

| Endpoint | Auth | Behavior |
|---|---|---|
| `GET /healthz` | none | Process health |
| `GET /readyz` | none | DB/role/config readiness |
| `POST /v1/imports` | development credential | Raw acceptance; requires `Idempotency-Key` |
| `GET /v1/imports/{id}` | development credential | Authorized import/work state |
| `GET /v1/imports/{id}/raw` | development credential | Exact authorized raw bytes |

Imports accept `text/csv` and `application/octet-stream` within `RECOVERY_MAX_INPUT_BYTES`. Unsupported content type, empty body and oversize input are rejected before acceptance. Structurally malformed CSV or a declared-org mismatch is accepted and quarantined, preserving bytes but never treating it as canonical evidence.

## CLI

```powershell
recovery ingest path/to/input.csv alpha-local-token idempotency-001
recovery status <envelope-id> alpha-local-token
recovery worker-once
recovery fixture-load data/fee_report_sample.csv
recovery readiness
```

`fixture-load` is deliberately CLI-only and requires `RECOVERY_DEMO_FIXTURES_ENABLED=true` plus an allowlisted organizer-fixture hash.

## Database and migration procedure

Migrations run as `recovery_owner`. The running service uses `recovery_app`; it must not use the owner connection string. `recovery_app` has no superuser, owner or `BYPASSRLS` privilege.

Do not use a session-level tenant setting. Every tenant transaction sets `app.current_org_id` locally.

## Limitations

v0.1 has no financial normalization, no evidence interpretation, no policy lookup, no amount calculation, no claims, no export, no AI, no human override and no Recovery UI. It cannot determine whether a fee is valid or whether a recovery is owed.
