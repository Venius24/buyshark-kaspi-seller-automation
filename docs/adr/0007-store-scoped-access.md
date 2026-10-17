# ADR-0007: Resolve store context and permissions at the API boundary

- Status: Accepted
- Date: 2026-07-28

## Context

An audit found two incompatible authorization paths: a shared file-backed
session and JWT users in PostgreSQL. Some tables lacked `store_id` or keyed rows
only by SKU; product data, API keys and worker markers also had shared state.
With one live tenant and no staging, a large auth cutover risked the order flow.
Deployment hardening, price tests and characterization tests therefore preceded
router-by-router migration.

## Decision drivers

1. Prevent cross-store reads, writes and credential substitution.
2. Preserve live requests while clients and workers change authorization paths.
3. Verify and roll back small migration slices.
4. Carry store identity into database keys, caches and jobs, not only routers.
5. Keep permissions enforceable independently of frontend selections.

## Options considered

| Option | Isolation during transition | Live compatibility | Small slices | State migration | Server enforcement |
| --- | --- | --- | --- | --- | --- |
| Temporary dual-auth transition | ~ | + | + | + | + |
| Coordinate frontend first | ~ | ~ | ~ | ~ | ~ |
| Hard cutover | + | - | - | + | + |
| Stop migration | - | + | + | - | - |

**Dual-auth:** accepts old calls while routers move to the new resolver one at a
time. It avoids a coordinated switch for every endpoint, but the legacy path must
be temporary and store-scoped reads must never use another store's shared session.

**Frontend first:** can establish the new headers before backend enforcement.
It cannot repair shared backend tables or files by itself and requires coordination
across clients, including already open tabs and non-frontend callers.

**Hard cutover:** removes the compatibility path immediately once all consumers
are ready. Before that point, clients missing Bearer tokens or store selection
would break against a live service; the staged path reduces that cutover risk.

**Stop:** avoids immediate migration risk but retains cross-store data hazards.
It cannot support the intended multi-store product safely.

## Decision

Resolve an authorized user and store through
[store context](../../backend/security/store_context.py). Check explicit
`X-Store-Id` membership; otherwise select the user's oldest connected store.
Migrate routers/workers in slices, then remove the legacy fallback; the current
HTTP resolver fails closed without an eligible JWT user and store.

Scope persistent state and worker markers by store. Enforce role depth and
section access separately through [store roles](../../backend/security/store_roles.py).
[Public API keys](../../backend/routers/public_api/auth.py) resolve the owning
store before [public routes](../../backend/routers/public_api/groups.py) read or write.

## Consequences

- Authorization becomes a reusable server boundary, and the temporary legacy
  HTTP path has been removed after clients adopted JWT/store selection.
- The audit's shared catalogue and API-key leaks showed that protecting a router
  alone was insufficient. Schema keys, caches and worker state also needed scoping;
  [regression tests](../../tests/test_public_api_store_scoping.py) check cross-store behavior.
- A user with several stores must explicitly select one to avoid the oldest-store
  default. Role/section checks must also be applied to each relevant action.
- Migration compatibility had a real isolation cost until the shared path was
  retired; adding a fallback again would reopen the audited class of failure.

## Revisit triggers

- Require explicit store selection if multi-store clients make the default ambiguous.
- Review every new router, cache, job and API key for store scoping before release.
- Reassess the permission model when delegated staff need finer action-level grants;
  do not reintroduce a shared credential fallback to preserve old clients.
