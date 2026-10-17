# ADR-0004: Use async PostgreSQL in production

- Status: Accepted
- Date: 2026-07-26

## Context

The application has concurrent HTTP requests and background jobs that persist
store, order and configuration state. Commit `c0234adb` records the migration
from SQLite to PostgreSQL 16 with asyncpg and Alembic on 2026-07-26.

## Decision

Use [FastAPI](../../backend/main.py), SQLAlchemy 2 async sessions and PostgreSQL
through asyncpg in production. Keep schema changes in
[Alembic revisions](../../alembic/versions/001_initial_schema.py).
[Database setup](../../backend/db/database.py) converts a PostgreSQL URL to the
asyncpg dialect, configures a connection pool and exposes request-scoped
sessions.

When `DATABASE_URL` is absent, the database module defaults to SQLite through
aiosqlite for local execution and tests. The
[CI workflow](../../.github/workflows/ci-cd.yml) runs the test suite without
provisioning a production PostgreSQL instance.

## Alternatives considered

- SQLite as the production database: it was the earlier layer replaced by the
  documented migration. The repository does not record a benchmark or full
  option analysis.
- Synchronous database access: not evaluated in depth in the repository.

## Consequences

- The production path supports pooled concurrent access;
  [database setup](../../backend/db/database.py) sets pool size, overflow and
  connection checks.
- Schema evolution requires migrations and deployment coordination. Local SQLite
  and production PostgreSQL can differ in SQL behavior; CI alone cannot prove
  every production database path.
- The app also calls `create_all` at startup, so the migration discipline must
  be checked against actual deployment behavior rather than inferred from
  Alembic's presence alone.
