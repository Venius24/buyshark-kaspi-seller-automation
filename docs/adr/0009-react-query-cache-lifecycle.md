# ADR-0009: Keep dashboard queries across tab changes

- Status: Accepted
- Date: 2026-07-30

## Context

Dashboard tabs were conditionally mounted. Switching away destroyed component
state, and returning fetched orders, groups or status again. Users saw a fresh
loading screen on every click. The change was deployed on 2026-07-30; cache
persistence followed on 2026-08-02.

## Decision

Use one [TanStack Query client](../../frontend/src/queryClient.ts) above the
tabs. Put shared fetch functions in [queries.ts](../../frontend/src/queries.ts)
and assign polling intervals by freshness need. Keep selected queries observed
outside the conditional tabs with
[BackgroundSync](../../frontend/src/components/BackgroundSync.tsx), because an
unmounted query does not continue interval polling.

Persist the cache for fast initial rendering, then refresh stale data in the
background. Clear in-memory and persisted data on session boundaries; version
the persisted snapshot so an old open tab can discard incompatible cache data.
Include the selected store in store-scoped query keys.
For new dashboard data loading, use the shared query functions and invalidation
instead of adding component-local `useEffect`/`fetch`/`useState` polling.

## Alternatives considered

- Component-local fetching on every mount: caused the observed repeated loading
  and was replaced.
- Query caching only inside each tab: intervals stop when the tab unmounts; the
  always-mounted observer was added after that behavior was observed.
- One persistent cache shared across login sessions: initially implemented,
  then rejected after it displayed another session's stale store list.

## Consequences

- Revisiting a tab can render cached data immediately; displayed data may be
  stale until the next fetch completes.
- Persisted data is sensitive to account and store boundaries. The
  [cache lifecycle](../../frontend/src/queryClient.ts) clears at login/logout
  and uses a cache version for already open tabs.
- Polling every large query at a short interval created needless traffic;
  [BackgroundSync](../../frontend/src/components/BackgroundSync.tsx) now polls
  the full product list less often and uses invalidation after changes.
