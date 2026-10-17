# ADR-0001: Keep the core as a modular monolith

- Status: Accepted
- Date: approx. July 2026

## Context

The seller cabinet combines repricing, orders, stock, chat, billing and
analytics. The repository's original FastAPI entry point dates to July 2026; no
dated evaluation of service decomposition is recorded.

At the July 2026 hardening stage, there was one live tenant, one operator, no
staging environment, and a plan to onboard roughly the first ten customers
manually. The stated priority was to make the live service stable and
observable before scaling its infrastructure.

The operational layout registers periodic jobs at application startup. Where
two jobs need the same order data, the packaging sync and auto-assembly share
one polling cycle rather than doubling requests to Kaspi.

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
  detailed comparison. The author's stated priority at this stage was stability
  of the live deployment, not a service split sized for future customers.
- A separate scheduler or worker fleet: the tracked worker layout uses
  application startup tasks. A comparison with a separate scheduler is not
  recorded; the documented choice to reuse the packaging poll avoids another
  expensive order scan.

## Consequences

- A single deployment can change API contracts and domain logic together. Shared
  store context and database models remain in one codebase.
- Worker and HTTP workloads share a process and resources. A service restart
  restarts the periodic jobs too; one job cannot be restarted independently. See
  [startup registration](../../backend/main.py) and
  [the unit](../../deploy/kaspi-saas.service).
- Blocking work can stall the shared event loop; later performance work moved
  blocking calls away from it. The small offer service eventually received its
  own release pipeline, so changes to offers need not redeploy the core; see
  [its deployment script](../../deploy/deploy-offer.sh).
- The author's later estimate was that one process might handle roughly 40–60
  stores; this was a planning ceiling, not a measured capacity result. The
  proposed first scaling step was bounded parallelism inside the process. The
  radar worker currently uses a bounded thread pool rather than a separate
  service per store.
