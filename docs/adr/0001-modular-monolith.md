# ADR-0001: Keep the core as a modular monolith

- Status: Accepted
- Date: approx. July 2026

## Context

The seller cabinet combines repricing, orders, stock, chat, billing and
analytics. The repository's original FastAPI entry point dates to July 2026; no
dated evaluation of service decomposition is recorded.

## Decision

Keep these domains in one FastAPI application. Register domain routers in
[the application entry point](../../backend/main.py), with business logic in
`backend/dumper/`, `backend/chatbot/`, `backend/services/` and
`backend/security/`.

Run periodic jobs as tasks started by the same application. The production
[systemd unit](../../deploy/kaspi-saas.service) runs one Uvicorn worker. This is
a core application boundary, not a claim that every component shares one
process: [the offer service](../../offer_service/main.py),
[the API ping-pong component](../../api-ping-pong/app.js) and
[the landing page](../../buyshark-landing/package.json) are separate.

## Alternatives considered

- Independent microservices for each domain: the repository does not record a
  detailed evaluation or a reason for rejecting them.
- A separate scheduler or worker fleet: the tracked worker layout uses
  application startup tasks; no comparative evaluation is recorded.

## Consequences

- A single deployment can change API contracts and domain logic together. Shared
  store context and database models remain in one codebase.
- Worker and HTTP workloads share a process and resources. A service restart
  restarts the periodic jobs too; one job cannot be restarted independently. See
  [startup registration](../../backend/main.py) and
  [the unit](../../deploy/kaspi-saas.service).
- The small independent services and static sites need their own deployment
  boundaries; the core decision does not cover them.
