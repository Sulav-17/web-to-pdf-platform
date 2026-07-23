# Orchestration Log

## Task A-ENGINE — Conversion engine, API, metering, storage

Status: **complete**. Lint, type-check and tests all pass (see results below).

### Summary

Implements the core web-to-PDF engine: a FastAPI application backed by
PostgreSQL (SQLAlchemy Core, async/asyncpg) that authenticates callers by
bearer API key, meters credits atomically, and renders PDFs with a bounded,
self-recycling Playwright Chromium pool. Output is written through a pluggable
storage backend (local for dev/tests; Cloudflare R2 wired but deferred). URL
rendering is accepted but disabled by default and returns a clear 503 (full
SSRF protection is Task A-SEC).

### Endpoints

- `GET /healthz` — liveness + browser-pool connectivity.
- `POST /v1/convert` — synchronous: charge → render inline → return the PDF.
- `POST /v1/jobs` — asynchronous: charge → enqueue background render → return the job (202).
- `GET /v1/jobs/{id}` — job status / lifecycle fields.
- `GET /v1/usage` — plan, monthly credits, current balance, job count.

### Files created

**Application (`apps/api/src/pdf_api/`)**
- `__init__.py` — package marker / version.
- `config.py` — `Settings` (pydantic-settings); env-driven, `r2_configured` gate.
- `logging_config.py` — structlog JSON logging with `request_id` / `job_id` binding.
- `observability.py` — Sentry + PostHog init; both no-ops when unconfigured.
- `db.py` — async engine/connection/transaction helpers (NullPool under tests).
- `models.py` — SQLAlchemy Core tables (schema source of truth).
- `schemas.py` — Pydantic v2 request/response models + input validation.
- `keys.py` — secure API-key generation, SHA-256 hashing, constant-time verify.
- `auth.py` — bearer API-key dependency (`Principal`), `last_used_at` update.
- `metering.py` — atomic lock/dedupe/charge/refund, balance, ledger reasons, costs.
- `renderer.py` — `BrowserPool`: semaphore, per-job isolated context, recycle, crash recovery, output-size guard.
- `storage.py` — `StorageBackend` / `LocalStorageBackend` / `R2StorageBackend` + factory.
- `jobservice.py` — `EngineState`, job execution, fail-and-refund, usage snapshot.
- `routes.py` — the five endpoints, input guards (413 / 503 / 402).
- `main.py` — app factory, lifespan (browser pool start/stop), request-id middleware.
- `seed.py` — canonical plan seed data (free / starter / pro).
- `bootstrap.py` — dev bootstrap command (verified user + free key + 75 credits).

**Migrations**
- `alembic.ini`
- `apps/api/alembic/env.py` — async migrations using app settings + metadata.
- `apps/api/alembic/script.py.mako`
- `apps/api/alembic/versions/0001_initial.py` — all tables + plan seed.

**Tests (`apps/api/tests/`)** — 25 tests
- `conftest.py`, `test_health.py`, `test_convert.py`, `test_jobs.py`,
  `test_metering.py`, `test_validation.py`, `test_auth.py`, `test_storage.py`,
  `test_concurrency.py`.

**Infra**
- `docker-compose.yml` — PostgreSQL 16 + api service.
- `infra/Dockerfile` — Python 3.12 slim, Playwright Chromium, non-root `appuser`.
- `.env.example` — no secrets.
- `.github/workflows/ci.yml` — ruff + mypy + pytest against a Postgres service.
- `.gitignore`, `.dockerignore`.

### Dependencies added and why

No new runtime dependencies were required — `pyproject.toml` already pinned
FastAPI, Pydantic v2, SQLAlchemy, asyncpg, Alembic, Playwright, boto3,
structlog, sentry-sdk, posthog, uvicorn, pypdf, httpx.

Dev-group additions:
- `boto3-stubs[s3]` — type stubs so `mypy` can check the R2 backend without
  loosening import strictness on the whole codebase.

Tooling configuration added to `pyproject.toml`: `[tool.uv] package = false`
(the repo is a runnable project, not a distributable wheel), and `ruff`,
`mypy`, and `pytest` sections. `pytest`/`mypy`/`alembic` use
`apps/api/src` on the path (src layout).

### Design assumptions

- **Balance = `SUM(credit_ledger.delta)`.** There is no denormalised balance
  column; the ledger is authoritative. Atomicity is guaranteed by taking a
  `SELECT ... FOR UPDATE` lock on the `users` row before reading the balance and
  inserting the job + charge in the same transaction.
- **Costs:** HTML = 1 credit, URL = 2 credits.
- **Domain modules live as submodules of `pdf_api`** (`auth.py`, `keys.py`,
  `metering.py`, `logging_config.py`) rather than as separately-installable
  packages under `packages/`. The root `pyproject.toml` defines a single
  package with no workspace configuration, so a single coherent import root
  keeps `uv run`, `mypy`, `pytest`, and Alembic working without extra packaging
  wiring. The `packages/*` directories are left in place for later extraction.
- **Async jobs run as in-process background tasks** (`asyncio.create_task`).
  A durable queue/worker is intentionally out of scope for A-ENGINE; the raw
  HTML/URL source is held in memory for the life of the task and never
  persisted (only `input_hash` + options are stored).
- **Synchronous `/v1/convert`** renders inline and returns the PDF bytes with
  `X-Job-Id` / `X-Credits-Charged` headers; a render failure returns `502` after
  the credit refund.
- **Input size:** HTML is capped at 10 MB, enforced byte-accurately in the route
  (413) and structurally by Pydantic `max_length`.
- **Output size:** PDFs over 50 MB fail the job with `output_too_large` and are
  refunded.
- **Browser pool:** Chromium launches once in lifespan; at most 3 concurrent
  renders (semaphore); every job uses a fresh isolated `BrowserContext` that is
  always closed; the browser is retired and relaunched after 50 jobs with
  in-flight renders draining first; a disconnected (crashed) browser is
  relaunched on next acquire. Graceful shutdown closes the browser and disposes
  the DB engine.
- **Tests use real PostgreSQL** (never SQLite) so `FOR UPDATE`, JSONB, and
  `bigserial` behave as in production. `NullPool` is used under tests so asyncpg
  connections are not shared across the per-test event loops.

### Deferred: Cloudflare R2 activation

- `R2StorageBackend` is implemented (put/get/delete) but is **only constructed
  when all five `R2_*` settings are present** (`Settings.r2_configured`).
- When R2 is unconfigured, `build_storage_backend()` returns
  `LocalStorageBackend` and **boto3 is never imported or called** — covered by
  `test_r2_not_contacted_without_configuration`.
- Local outputs expire after 24 h (lazy sweep on access + `purge_expired()`).
- **R2 bucket lifecycle rules (object expiry on the R2 side) remain deferred**
  to a later task; `R2StorageBackend.purge_expired()` is a no-op by design.

### Refund-reason contract issue

The A-ENGINE spec's `credit_ledger.reason` value list did **not** include a
value for *refunds*. To preserve the schema exactly (no new column, no schema
drift), failure refunds are recorded with **`reason = "admin"` and the failing
`job_id`** (see `metering.refund_job` and the `REASON_*` constants). This is a
deliberate, documented compromise; if a dedicated `"refund"` reason is later
approved, `refund_job` is the single place to change. Reasons currently in use:
`render` (charge), `grant` (signup/bootstrap), `subscription` (reserved),
`admin` (adjustments **and** failure refunds).

### Exact test / lint / type-check results

Commands (run with `DATABASE_URL` pointing at a PostgreSQL 16 instance):

```
uv run ruff check .        ->  All checks passed!
uv run mypy apps/api/src   ->  Success: no issues found in 17 source files
uv run pytest -q           ->  25 passed
```

Test breakdown (25 total):

| File                  | Tests | Covers |
|-----------------------|-------|--------|
| test_health.py        | 1     | health endpoint |
| test_convert.py       | 3     | valid HTML render, `%PDF` prefix, 1-credit charge, options honoured |
| test_jobs.py          | 4     | queued→completed lifecycle, idempotency dedupe, 404, usage |
| test_metering.py      | 4     | 402 empty credits, refund on forced failure, output-size limit, input-size 413 |
| test_validation.py    | 4     | both html+url, neither, out-of-range options, URL rendering disabled (503) |
| test_auth.py          | 4     | API-key hashing, unauthorized (missing/bad/inactive key) |
| test_storage.py       | 4     | local roundtrip, 24 h expiry, local-without-R2, R2 not contacted |
| test_concurrency.py   | 1     | ten concurrent jobs without browser-pool corruption |

### Explicitly NOT implemented (out of scope)

Stripe billing, webhooks (columns exist, no delivery), reading-pack merging,
Chrome extension, landing page, the full SSRF module, and deployment.
