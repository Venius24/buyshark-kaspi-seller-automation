# ADR-0002: Separate official and unofficial Kaspi interfaces

- Status: Accepted
- Date: approx. July 2026

## Context

BuyShark needed orders and product data as well as seller-cabinet actions, buyer
chat and public offer information. The official Shop API supplies order
endpoints used by [the API client](../../backend/kaspi_wrapper.py). The
repository's [architecture overview](../../README.md#architecture) also shows
cabinet and chat integrations outside that API. It does not record a single date
or a formal comparison of all interface options.

## Decision

Use the official Shop API where its available endpoints fit the operation. Keep
other integrations behind domain modules and adapters, including
[the chat package](../../backend/chatbot/),
[the repricing package](../../backend/dumper/) and
[the application routers](../../backend/routers/). Treat the unofficial
interfaces as changeable external dependencies.

In the public mirror, selected integration modules are interface stubs. Their
scope is stated in
[What is not published](../../README.md#what-is-not-published).

## Alternatives considered

- Official API only: the tracked application includes cabinet and chat
  capabilities outside the order endpoints; the repository does not record a
  complete official-only replacement for them.
- Browser-only integration: not evaluated in depth in the repository as a
  replacement for the available Shop API operations.

## Consequences

- The official API remains usable for supported operations while other features
  can be organized around narrower module boundaries.
- Unofficial interfaces can change without notice. The
  [public README](../../README.md#why-it-did-not-take-off) names this as a
  structural product risk; module boundaries limit where integration changes
  land but cannot remove that risk.
- Some integrations cannot run from the public mirror because their modules are
  stubbed; this is explicit in the README.
