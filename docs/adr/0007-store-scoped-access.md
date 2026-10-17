# ADR-0007: Resolve store context and permissions at the API boundary

- Status: Accepted
- Date: 2026-07-28

## Context

The initial single-store assumptions were unsuitable once users could manage
multiple stores. The tracked 2026-07-28 migration introduced a dual-auth
resolver while routes moved to store-scoped access; later slices removed the
legacy fallback and tightened public API isolation.

## Decision

Resolve a JWT user and an authorized store in
[one dependency](../../backend/security/store_context.py). An explicit
`X-Store-Id` must belong to that user; otherwise the resolver chooses the user's
oldest connected store. Requests without an eligible user and store fail closed.
The former shared-session fallback was removed after the transition.

Keep permission checks server-side.
[Store roles](../../backend/security/store_roles.py) define VIEWER, MANAGER and
OWNER depth; section access is a separate axis.
[Public API authentication](../../backend/routers/public_api/auth.py) resolves
the owning store from the hashed API key, and
[public routes](../../backend/routers/public_api/groups.py) scope reads and
writes to it.

## Alternatives considered

- A permanent shared-session fallback: used during migration, then removed
  because it could grant access outside the requesting user's store.
- A single role enum for both job type and privilege depth: the
  [role module](../../backend/security/store_roles.py) explains why sections and
  rank are separate.

## Consequences

- Store identity and permissions are checked at the HTTP boundary rather than
  trusting a frontend selection or an arbitrary store identifier.
- Routers and state access must consistently use the resolved store ID. The
  [store-scoping tests](../../tests/test_public_api_store_scoping.py) cover
  cross-store key behavior; gaps in unconverted paths remain a regression risk.
- A user with multiple stores must send the explicit header to select anything
  other than the default oldest store.
