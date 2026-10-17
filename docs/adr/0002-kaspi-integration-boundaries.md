# ADR-0002: Separate official and unofficial Kaspi interfaces

- Status: Accepted
- Date: approx. July 2026

## Context

BuyShark needed orders and product data as well as seller-cabinet actions,
buyer chat and public offer information. The token-authenticated Shop API
supplies order and order-entry reads used by
[the API client](../../backend/kaspi_wrapper.py). Order assembly and price
changes required seller-cabinet operations; chat required a separate session.
The repository does not record a single date or a formal comparison of every
integration option.

## Decision

Use the official Shop API where its available endpoints fit the operation. Keep
other integrations behind domain modules and adapters, including
[the chat worker](../../backend/chatbot/worker.py),
[the repricing executor](../../backend/dumper/executor.py) and
[the order router](../../backend/routers/orders.py). Treat the unofficial
interfaces as changeable external dependencies.

In the public mirror, selected integration modules are interface stubs. Their
scope is stated in
[What is not published](../../README.md#what-is-not-published).

## Alternatives considered

- Official API only: it covers the documented order reads, but the
  recorded assembly, repricing and chat flows use other interfaces. No
  complete official-only path for those features is recorded.
- Seller-cabinet operations for all reads: unnecessary for the documented
  token-authenticated Shop API calls. A full browser-only replacement was not
  evaluated in depth.

## Consequences

- The official API remains usable for supported operations while other features
  can be organized around narrower module boundaries.
- Unofficial interfaces can change without notice. The
  [public README](../../README.md#why-it-did-not-take-off) names this as a
  structural product risk; module boundaries limit where integration changes
  land but cannot remove that risk.
- A platform-side block later made one data source unreliable. Integration
  boundaries could contain the failure but could not guarantee availability.
- Some integrations cannot run from the public mirror because their modules are
  stubbed; this is explicit in the README.
