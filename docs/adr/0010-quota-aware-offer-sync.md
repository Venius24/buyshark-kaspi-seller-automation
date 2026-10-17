# ADR-0010: Queue price and stock intent behind one writer

- Status: Accepted
- Date: 2026-08-12

## Context

Independent repricing and stock writers repeatedly submitted unchanged offers.
The seller-cabinet limit was 250 offers per 30 minutes; a batch request reduced
network overhead but still counted every offer. Once the limit was exhausted,
blind retries kept the writers in a failure loop although few values had
actually changed.

The later bulk path had a separate file quota of two uploads per 30 minutes;
one complete XML file could cover the catalogue, while point writes remained
the urgent path.

## Decision

Let repricing and stock sync declare desired state to
[one offer writer](../../backend/services/offer_sync.py). Store one row per
store and offer with desired, last-pushed and observed values in
[the migration](../../alembic/versions/031_kaspi_offer_sync.py). The pending
queue is the difference between those values, rather than a separate queue
table. Coalesce repeated changes, discard no-op writes, order by business
priority and flush in batches.

Read remaining quota from Kaspi through
[the quota module](../../backend/services/kaspi_quota.py). Reserve capacity for
withdrawals; if quota cannot be read, defer ordinary writes. Keep pending
intent when a session is unavailable or a write fails.

## Alternatives considered

- Each producer writes immediately and retries at a fixed delay: generated
  duplicate writes and repeated limit errors; it could also lose a decision
  when a session was unavailable.
- Batch requests alone: measured faster, but the quota counts offers, not HTTP
  requests, so batching does not raise the limit.
- A separate durable queue table or local quota counter: the chosen row already
  represents pending state, and Kaspi's counter is the quota source of truth.
- Bulk XML for all updates: deferred until the separate full-catalogue safety
  work in [ADR-0003](0003-guarded-xml-price-feed.md). The resulting hybrid
  uses files for guarded bulk updates and point writes for urgent changes;
  one bad file can affect the whole catalogue.

## Consequences

- Repeated changes collapse to the latest desired state, and no-op writes no
  longer spend quota. A production flush skipped five of seven pending writes
  because they changed nothing.
- A write may wait for the next flush or quota window. Withdrawal priority and
  reserved capacity keep urgent inventory actions ahead of cosmetic repricing.
- The queued row records latest state, not a complete history of every send;
  it must not be used as a quota audit log. See
  [the writer](../../backend/services/offer_sync.py) and
  [its tests](../../tests/test_offer_sync.py).
- The XML worker reads pending intent from the same state but sends a guarded
  file through its own uploader. Settlement must reconcile the file result
  with point writes to avoid duplicate or contradictory updates.
