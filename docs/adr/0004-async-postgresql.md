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

An earlier SQLite database file was tracked in the repository and used by CI;
that historical fixture is absent from the current tree.

## Alternatives considered

- SQLite as the production database: it was the earlier layer replaced by the
  documented migration. No benchmark or full initial option analysis is
  recorded. Later store-scoped tables and composite keys relied on relational
  migrations, not shared JSON files.
- Synchronous database access: not evaluated in depth in the repository.

## Consequences

- The production path supports pooled concurrent access;
  [database setup](../../backend/db/database.py) sets pool size, overflow and
  connection checks.
- Schema evolution requires migrations and deployment coordination. Local SQLite
  and production PostgreSQL differ in SQL behavior: SQLite accepted Boolean
  and timestamp backfill values that asyncpg rejected during a billing deploy.
  Database migrations need a PostgreSQL check, not only SQLite CI.
- A later pool-exhaustion incident showed that async sessions must be closed
  before long external calls; [store context](../../backend/security/store_context.py)
  now resolves and closes its own read session before returning.
- The app also calls `create_all` at startup, so the migration discipline must
  be checked against actual deployment behavior rather than inferred from
  Alembic's presence alone.
- A local development server started with live credentials can also start
  background jobs and queue real outbound actions. The SQLite fallback does
  not by itself isolate local development from production integrations.
