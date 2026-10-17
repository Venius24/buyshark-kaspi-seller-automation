# ADR-0011: Classify customer pickup from raw delivery attributes

- Status: Accepted
- Date: 2026-08-13

## Context

The system reported no customer pickups although the seller's cabinet showed
them. Both locker delivery and customer pickup use `DELIVERY_PICKUP` as
`deliveryMode`; labeling that value as locker delivery made pickup campaigns
unreachable. `pickupPointId` is also populated for both classes.

## Decision drivers

1. Distinguish actual customer pickup from carriage by Kaspi.
2. Target pickup automation without messaging locker-delivery customers.
3. Reconcile normalized types against raw fields and seller-visible counts.
4. Keep one parser shared by downstream order workflows.

## Options considered

| Option | Correct distinction | Campaign targeting | Verifiable meaning | Shared normalization |
| --- | --- | --- | --- | --- |
| deliveryMode alone | - | - | - | + |
| pickupPointId alone | - | - | - | + |
| Raw carriage flag + delivery block | + | + | + | + |

**Mode alone:** is simple to normalize but conflates two real workflows.
The observed missing pickups and unreachable campaign were consequences of
exactly this mapping, not an empty seller order queue.

**Pickup-point ID alone:** looks useful because the field names a pickup location.
It identifies the seller's point and appears in both cases, so its presence cannot
determine who carries the order.

**Raw carriage attributes:** distinguish whether Kaspi delivers the parcel.
They require a compound rule, but that rule can be checked against cabinet counts
and preserved in regression fixtures rather than inferred from a label.

## Decision

For `DELIVERY_PICKUP`, use `isKaspiDelivery` and the delivery block to determine
Kaspi carriage. Without carriage, classify customer pickup; with carriage,
classify locker/pickup-point delivery. Preserve explicit express indicators first
in [the shared parser](../../backend/kaspi_wrapper.py).

Poll pickup-state orders for stores with pickup points and filter locker orders
using the corrected type in [the worker](../../backend/services/stock_sync_worker.py).
The earlier transient new-order poll produced no usable campaign orders while
repeating requests; it was not a reliable substitute for pickup-state selection.

## Consequences

- Pickup automation can select its actual orders;
  [regression tests](../../tests/test_pickup_delivery_type.py) preserve both cases.
- Historical derived `deliveryType` values remain unreliable; raw attributes
  must be used when interpreting old logs or snapshots.
- Correct type selection was insufficient for send safety: the first corrected
  pass queued old pickup orders too. Those messages were held, exposing the need
  for an order-age gate as well as a queue-age gate.

## Revisit triggers

- Reconcile counts and revise fixtures if platform fields change or a new delivery
  mode makes the carriage rule ambiguous; do not guess from a renamed label.
- Recheck campaign eligibility before expanding sends to a new order state or
  backlog; correct delivery type alone does not establish message timeliness.
