# ADR-0004: Use async PostgreSQL in production

- Status: Accepted
- Date: 2026-07-26

## Context

HTTP handlers and background jobs update store, order, inventory and billing
state concurrently. Commit `c0234adb` migrated SQLite to PostgreSQL 16, asyncpg
and Alembic. One host and operator favor a small database setup, but shared
store-scoped state needs durable relational constraints and explicit migrations.

## Decision drivers

1. Support concurrent HTTP and worker transactions without a single file writer.
2. Enforce store-scoped keys and relational integrity in the production database.
3. Make schema and backfill changes repeatable through Alembic.
4. Fit the existing async FastAPI code without blocking the event loop on DB I/O.
5. Keep local execution and operational cost manageable on one host.

## Options considered

| Option | Concurrent writes | Integrity | Migrations | Async fit | Operating simplicity |
| --- | --- | --- | --- | --- | --- |
| Keep SQLite | - | ~ | ~ | + | + |
| PostgreSQL + async SQLAlchemy | + | + | + | + | ~ |
| PostgreSQL + synchronous SQLAlchemy | + | + | + | ~ | ~ |

**SQLite:** inexpensive to run and useful for local tests through aiosqlite.
Its serialized writes and dialect differences make it a weaker production fit
for multiple jobs and tenants; passing SQLite tests does not validate PostgreSQL.

**Async PostgreSQL:** keeps store relations and concurrent writes in one database
while fitting async handlers. The cost is a database service, connection-pool
limits and careful session lifetimes; async access does not prevent exhaustion.

**Synchronous PostgreSQL:** offers the same relational and migration guarantees.
It would require synchronous execution boundaries or explicit thread offloading
in the existing async application, adding another queue to manage without
removing the database's connection limit.

## Decision

Use SQLAlchemy 2 async sessions and asyncpg in production through
[database setup](../../backend/db/database.py). The pool has 10 persistent slots
and up to 20 overflow connections, with connection checks. Keep schema changes
in [Alembic](../../alembic/versions/001_initial_schema.py).

Retain SQLite when `DATABASE_URL` is absent for local execution and tests.
[CI](../../.github/workflows/ci-cd.yml) does not provision production PostgreSQL;
its results are a fast check, not a migration compatibility guarantee.

## Consequences

- API and jobs share relational state and explicit migration history.
- SQLite accepted integer Boolean values and string timestamps that asyncpg
  rejected during the billing deployment. The fixes in
  [migration 027](../../alembic/versions/027_billing_tariffs.py) corrected the types;
  production migration checks remain necessary.
- Long external calls under open sessions exhausted all 10+20 slots: 1,061 pool
  timeouts were observed in a week. [Store context](../../backend/security/store_context.py)
  now closes its read session before returning, and
  [the order cache](../../backend/kaspi_wrapper.py) coalesces concurrent refreshes.
  A later recurrence means that fix is not proof that every leak is eliminated.
- A historically tracked SQLite file made CI depend on local schema/data; it is
  absent now. Startup still calls `create_all`, so migration ownership needs care.
- A dev server with live credentials once queued real customer messages. SQLite
  fallback alone is not an integration sandbox; jobs start with the application.

## Revisit triggers

- Add a PostgreSQL migration gate before another schema/backfill release; SQLite
  has already missed production type failures.
- Reassess pool sizing and transaction boundaries if pool waits recur after
  closing sessions before external I/O; increasing the pool alone hides that bug.
- Consider a synchronous boundary only for a measured workload or dependency
  that justifies it; reassess database placement when one-host recovery is inadequate.
