# ADR-0010: Queue price and stock intent behind one writer

- Status: Accepted
- Date: 2026-08-12

## Context

Independent repricing and stock writers repeatedly submitted unchanged offers.
The cabinet quota was 250 offers per 30 minutes; batching reduced request time
but still counted each offer. At exhaustion, blind retries stalled useful writes.
The later XML path had a separate quota of two catalogue-sized files per window.

## Decision drivers

1. Spend scarce offer quota only on meaningful changes.
2. Prioritize withdrawals and revenue-sensitive changes over cosmetic updates.
3. Preserve the latest desired state across failures and missing sessions.
4. Reconcile accepted writes against actual external state.
5. Keep durable coordination small enough for the existing database/codebase.

## Options considered

| Option | Quota efficiency | Priorities | Durable intent | Reconciliation | Simplicity |
| --- | --- | --- | --- | --- | --- |
| Producers write/retry immediately | - | - | - | - | ~ |
| Batch requests only | - | - | - | - | + |
| Desired-state rows + one point writer | + | + | + | + | + |
| Separate append-only job queue | ~ | + | + | ~ | - |

**Immediate producers:** avoid queue latency but compete for the same quota.
Their duplicate writes exhausted the window even though relatively few values
changed, and an unavailable session could lose a producer's decision.

**Batch only:** one observed eight-offer batch took about 0.5 seconds instead of
roughly 40 seconds sequentially. That improves transport overhead, not the offer
quota; batching alone preserves the duplicate-write problem.

**Desired-state rows:** coalesce changes by store/offer and derive pending work
from desired versus pushed/observed values. They fit latest-state convergence,
but deliberately do not retain a replayable event for every intermediate change.

**Separate job queue:** can preserve each request and support independent consumers.
It adds duplicate delivery, obsolete-job cancellation and coordination with observed
state; those costs are unnecessary while only the latest offer state matters.

## Decision

Producers declare intent to [offer sync](../../backend/services/offer_sync.py),
backed by [one row per store/offer](../../alembic/versions/031_kaspi_offer_sync.py).
Flush every 45 seconds, coalesce no-ops and order by priority. Reserve 30 units
for withdrawals through [quota management](../../backend/services/kaspi_quota.py).

Use Kaspi's counter as the quota authority, with optimistic local consumption
between refreshes; do not rely on a local-only limit. Preserve intent on failures.
Reuse a same-window quota snapshot if refresh fails; without one, defer writes.
Keep guarded XML for the bulk tail; see [ADR-0003](0003-guarded-xml-price-feed.md).

## Consequences

- A production flush skipped five of seven pending writes because nothing changed.
  Repeated producer decisions can converge without consuming another quota unit.
- HTTP acceptance was once mistaken for application: accepted prices stayed old,
  while local no-op suppression hid the discrepancy. The writer now rechecks
  unconfirmed state; [tests](../../tests/test_offer_sync.py) cover its decisions.
- A zero-price regression withdrew offers; shared extraction and nonpositive-price
  guards now protect the baseline, decision and outgoing batch.
- Low-priority work can starve under sustained quota pressure, and only withdrawal
  priority has reserved capacity. A row is latest state, not a complete send audit.
- The XML worker reads the same pending state but uploads separately. Settlement
  and observed-state checks coordinate it with point writes; all sends do not
  literally pass through the point-writer module.

## Revisit triggers

- Rebalance priorities or catalogue participation when the queue persistently
  exceeds a quota window, especially if revenue-sensitive writes wait behind it.
- Add an event ledger/queue if exact send history, replay or multiple independent
  consumers become requirements; the current rows cannot supply those semantics.
- Reassess writer ownership before running multiple app processes, and revisit
  the transport mix when platform quotas or official write coverage change.
