# ADR-0011: Classify customer pickup from raw delivery attributes

- Status: Accepted
- Date: 2026-08-13

## Context

The system reported no customer pickups although the seller's cabinet showed
them. Both locker delivery and customer pickup had `DELIVERY_PICKUP` as
`deliveryMode`, so the existing parser labeled every such order as a locker
delivery. This also made the pickup message campaign unreachable.

## Decision

Use `isKaspiDelivery` and the delivery block to distinguish whether Kaspi
carries the order. For `DELIVERY_PICKUP` without Kaspi carriage, classify
customer pickup; with carriage, classify locker or pickup-point delivery.
Preserve explicit express indicators ahead of this distinction in
[the parser](../../backend/kaspi_wrapper.py).

Reconcile the classification against seller-visible counts before changing
downstream behavior. Replace an unproductive poll of transient new orders with
a poll of pickup-state orders for stores that have pickup points; filter the
same-state locker orders using the corrected type. See
[the worker](../../backend/services/stock_sync_worker.py).

## Alternatives considered

- `deliveryMode` alone: the two order classes share its value; it caused the
  original misclassification.
- `pickupPointId` alone: it is populated for both classes and identifies the
  seller's point, so it cannot distinguish them.
- Keep the old new-order poll for the campaign: observed no usable new orders
  while making repeated requests; the pickup-state poll was chosen instead.

## Consequences

- Pickup-specific automation can now select its actual orders. The
  [regression tests](../../tests/test_pickup_delivery_type.py) preserve both
  cases.
- Historical derived `deliveryType` values in logs or snapshots remain
  unreliable; interpretation should use raw order attributes.
- The first corrected pass queued old pickup orders as well as recent ones.
  Those messages were held rather than sent automatically, exposing the need
  for an order-age gate in addition to a queue-age gate.
