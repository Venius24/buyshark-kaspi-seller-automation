# ADR-0002: Separate official and unofficial Kaspi interfaces

- Status: Accepted
- Date: approx. July 2026

## Context

Per the project owner, the official Shop API was too narrow to reach feature
parity with competing autopilots, which offered pricing, chat and bulk operations.
Attempts to obtain those capabilities through official channels, including
repeated contacts with Kaspi support, yielded help only with order processing.

The product needed order handling, repricing, stock synchronization, buyer chat
and competitor data. [The Shop API client](../../backend/kaspi_wrapper.py) covers
part of the order workflow: order and entry/product-detail reads and an
order-status POST. It also reads cities and sales points; cargo assembly uses
cabinet operations, and buyer chat needs its own session.
The implemented catalogue reads use cabinet interfaces; price/stock writes use
cabinet operations or XML, and competitor information comes from public pages.

The official coverage used here therefore cannot deliver the whole product.
Cabinet and chat sessions also have different login flows and cannot substitute
for each other. A failed write can affect a seller's availability or revenue,
so feature coverage must be weighed against an externally controlled interface.

## Decision drivers

1. Reach competitive feature parity that the official API alone cannot provide.
2. Prefer the supported token API wherever it fits an operation.
3. Limit the effect of an expired session or changed external response.
4. Avoid turning missing market data into an unsafe price or stock write.
5. Keep several integrations diagnosable and maintainable by one operator.

## Options considered

| Option | Competitive feature coverage | Supported contract | Failure containment | Write safety | Maintenance |
| --- | --- | --- | --- | --- | --- |
| Official API only | - | + | + | + | + |
| Cabinet/browser for everything | ~ | - | - | ~ | - |
| Hybrid with module/session boundaries | + | ~ | ~ | ~ | ~ |
| Drop chat and repricing | - | + | + | + | + |

**Official only:** fits the implemented order reads and supported status calls.
Official-channel attempts did not unlock the broader capabilities needed for
competitive parity. It would leave cabinet assembly, chat and the current
market-driven price/stock workflows unimplemented or manual.

**Cabinet/browser only:** could reuse seller-visible workflows but would discard
working token API reads. More features would then depend on session expiry and
changing cabinet behavior, increasing the work needed to diagnose a failure.

**Hybrid:** retains token-backed operations and adds narrower integrations for
the competitive workflows that official-channel attempts could not supply.
It needs separate health signals, sessions and parsers;
module boundaries reduce the repair scope but cannot ensure availability.

**Drop the dependent features:** removes their integration and write risks.
It also removes pricing, chat and bulk capabilities needed to compete, leaving
the same functional gap that official-channel attempts could not resolve.
This becomes reasonable if supporting those interfaces costs more than they earn.

## Decision

Use the official API for the operations it serves. Keep cabinet operations in
[the wrapper](../../backend/kaspi_wrapper.py) and
[repricing executor](../../backend/dumper/executor.py), with chat calls in
[the chat adapter](../../backend/chatbot/chat_client.py).
Persist cabinet and chat sessions separately through
[state helpers](../../backend/state_helpers.py). Enforce price and completeness
checks before writes; see [ADR-0003](0003-guarded-xml-price-feed.md).

Selected implementations are stubbed in the public version, as described in
[What is not published](../../README.md#what-is-not-published).

## Consequences

- Token-backed work need not stop solely because cabinet or chat cookies expire;
  [active-store resolution](../../backend/security/store_context.py) preserves
  that distinction. This is feature containment, not process isolation.
- Chat initially failed when given cabinet cookies. A separate chat session and
  storage field fixed that mismatch; a single generic "connected" flag is inadequate.
- A platform-side block made public market data unavailable while the application
  and other integrations remained usable. Availability of the market view was lost;
  adapters cannot repair the external dependency. The
  [README](../../README.md#why-it-did-not-take-off) identifies this structural risk.
- The wrapper still mixes official and cabinet methods, so the boundary requires
  care during changes; the public mirror cannot run every integration end to end.

## Revisit triggers

- If the platform expands its official API to cover pricing, stock or chat with
  equivalent functionality and usable limits, move the corresponding adapters;
  preserve their surrounding domain contracts and tests.
- Restrict or drop a feature when repeated outages make safe operation or manual
  recovery untenable for the seller and operator.
