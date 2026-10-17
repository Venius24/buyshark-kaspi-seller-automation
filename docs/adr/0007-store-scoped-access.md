# ADR-0007: Resolve store context and permissions at the API boundary

- Status: Accepted
- Date: 2026-07-28

## Context

An audit found two incompatible authorization paths: a shared file-backed
session and JWT users in PostgreSQL. Product data, API keys, order markers and
timers also had shared state; some tables lacked `store_id` or keyed rows only
by SKU, so another store could overwrite them. The tracked 2026-07-28 migration
introduced a dual-auth resolver. With one live tenant and no staging, the
author first hardened deployment and added price and characterization tests,
then migrated authorization. This order protected the live order flow while
the routes and workers changed.

## Decision

Resolve a JWT user and an authorized store in
[one dependency](../../backend/security/store_context.py). An explicit
`X-Store-Id` must belong to that user; otherwise the resolver chooses the user's
oldest connected store. Requests without an eligible user and store fail closed.
During migration, the resolver temporarily supported a shared-session fallback
while routers and workers were converted one at a time. Store-scoped state
moved to database rows keyed by store; the fallback was removed after the
clients sent JWT and store selection consistently. Store-scoped reads did not
fall back to the shared legacy session.

Keep permission checks server-side.
[Store roles](../../backend/security/store_roles.py) define VIEWER, MANAGER and
OWNER depth; section access is a separate axis.
[Public API authentication](../../backend/routers/public_api/auth.py) resolves
the owning store from the hashed API key, and
[public routes](../../backend/routers/public_api/groups.py) scope reads and
writes to it.

## Alternatives considered

- Dual-auth migration, chosen: the resolver accepted both paths while the
  frontend, routers and workers moved in tested slices. It preserved live
  requests but required a temporary isolation-sensitive fallback.
- Coordinate the frontend first: listed as a cutover option, without a
  recorded detailed comparison against the chosen sequence.
- Hard cutover: would remove the old path at once, but risked breaking live
  calls before all routes and workers were converted.
- Stop the migration: would leave the audited cross-store hazards in place.
- A single role enum for both job type and privilege depth: the
  [role module](../../backend/security/store_roles.py) explains why sections and
  rank are separate.

## Consequences

- Store identity and permissions are checked at the HTTP boundary rather than
  trusting a frontend selection or an arbitrary store identifier.
- Routers and state access must consistently use the resolved store ID. The
  [store-scoping tests](../../tests/test_public_api_store_scoping.py) cover
  cross-store key behavior; the audit found that protecting a router alone did
  not fix shared files, cache keys or worker state.
- A user with multiple stores must send the explicit header to select anything
  other than the default oldest store.
