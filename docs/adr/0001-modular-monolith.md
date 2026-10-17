# ADR-0001: Keep the core as a modular monolith

- Status: Accepted
- Date: approx. July 2026

## Context

Per the project owner, the application was built as one deployable from the
start. Its domains revolve around one core, and the product is sold as a single
bundle under one tariff, so no natural domain boundary or need for microservices
emerged.

Repricing, orders, stock, chat and billing share store identity and relational
models. At the July hardening stage there was one developer/operator, one live
tenant, one VPS and no staging environment. The first roughly ten customers
were to be onboarded manually; safe changes to a revenue system came first.
Packaging sync and auto-assembly share a polling cycle rather than independent
scheduler loops; see [the loop](../../backend/services/stock_sync_worker.py).

## Decision drivers

1. Retain the existing structure built around one deployable and one core.
2. Match a product sold and used as one bundle, without a natural domain split.
3. Preserve store-scoped data consistency with local database transactions.
4. Keep changes, deployment and recovery manageable by one developer/operator.
5. Coordinate expensive platform reads across dependent jobs.
6. Fit the next onboarding stage while leaving a path out of process limits.

## Options considered

| Option | Fit with how the product is built and sold | Local transactions | Change speed | Operations | Shared reads | Capacity isolation |
| --- | --- | --- | --- | --- | --- | --- |
| Modular monolith, in-process jobs | + | + | + | + | + | - |
| Shared codebase, separate worker/scheduler | + | + | ~ | ~ | ~ | + |
| Microservices by domain | - | - | - | - | ~ | + |

**Modular monolith:** preserves the original application structure and matches
the single-bundle product. API and jobs use the same models and store context
without network contracts between domains. One release and rollback fit the
operator's capacity, at the cost of a shared process and restart boundary.

**Separate worker process:** a Celery/APScheduler-like arrangement could isolate
long jobs while retaining the bundled product, codebase and database. It adds
process supervision, job ownership and coordination so two workers do not repeat a platform action;
this is the nearest extraction step when independent operation becomes necessary.

**Domain microservices:** domains are sold and used together under one tariff
and share store context and models, so domain names do not provide natural
service boundaries. Each service would need its own deployment, migration
coordination, store authorization and correlated diagnostics. That multiplies
the operating surface without a current product need; shared writes would also
need explicit consistency rules.

## Decision

Keep domain routers and application tasks in [one FastAPI app](../../backend/main.py).
The [systemd unit](../../deploy/kaspi-saas.service) runs one Uvicorn worker;
fifteen job tasks are registered at startup, plus optional loop monitoring.
Keep models and services modular inside that deployment.

Separate components already exist: [offer service](../../offer_service/main.py),
[API ping-pong](../../api-ping-pong/app.js) and [landing pages](../../buyshark-landing/package.json).
These are independent applications, not one service per cabinet domain.
The offer service now has [its own release path](../../deploy/deploy-offer.sh);
use release independence, rather than cabinet domain names, as the extraction
criterion.

## Consequences

- Cross-domain changes remain local and dependent jobs share a coordinated cycle.
- Resource contention is real: a market scan filled the common thread executor
  and delayed unrelated `asyncio.to_thread` calls for about 60 seconds. A dedicated
  six-thread radar pool now isolates that queue; it does not make the scan faster.
- Long external calls under open DB sessions exhausted the shared pool and broke
  both HTTP and jobs. [Store resolution](../../backend/security/store_context.py)
  now closes its read session before returning; the order cache coalesces refreshes.
- A restart restarts all startup jobs; they have no independent systemd restart.
  [Whole-app rollback](0006-single-host-rollback-deploy.md) remains necessary.

## Revisit triggers

- Extract a worker when chat or repricing requires independent restart or release.
- Review capacity before approaching the author's estimated 40–60-store ceiling
  for one process; this is a forecast, not a benchmark. Start with measured,
  bounded in-process parallelism, preserving external request limits.
- Reassess domain services when parts become independently sold or used, a second
  team needs independent ownership, or executor/DB contention persists after
  queue isolation and shorter transactions.
