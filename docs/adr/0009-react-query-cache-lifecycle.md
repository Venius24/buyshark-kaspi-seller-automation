# ADR-0009: Keep dashboard queries across tab changes

- Status: Accepted
- Date: 2026-07-30

## Context

Conditional tab mounting destroyed component state. Returning to orders, groups
or status fetched everything again and displayed another loading screen.
The query-client change deployed on 2026-07-30; persistence followed on 2026-08-02.
Caching also had to respect account changes and the selected store.

## Decision drivers

1. Render previously fetched dashboard data immediately on a tab return.
2. Prevent account/store changes from displaying another session's snapshot.
3. Keep important data fresh while its screen is unmounted.
4. Avoid redundant large requests and polling after every navigation.
5. Give components one shared loading and invalidation convention.

## Options considered

| Option | Tab return | Session isolation | Off-screen freshness | Request cost | Shared convention |
| --- | --- | --- | --- | --- | --- |
| Component-local fetch/polling | - | ~ | - | - | - |
| Query cache observed only in tabs | + | ~ | - | + | + |
| Shared queries + persistent observers/cache | + | ~ | + | ~ | + |

**Local fetching:** makes each tab self-contained but repeats loading state,
timers and requests. That was the observed behavior; navigation erased useful
data even when no underlying value had changed.

**Tab-only query cache:** preserves results across unmounts and simplifies fetches.
It does not keep interval polling alive without a mounted observer, so a configured
`refetchInterval` alone did not provide the expected background freshness.

**Shared observers and persistence:** keep selected queries active outside tabs
and make reloads render the last snapshot. They require explicit account/store
boundaries and tuned intervals, since persistence and observers can also retain
stale data or generate unnecessary traffic.

## Decision

Use one [query client](../../frontend/src/queryClient.ts) above the tabs, with
[shared fetchers](../../frontend/src/queries.ts). Keep selected queries observed
by [BackgroundSync](../../frontend/src/components/BackgroundSync.tsx).
Use store-aware query keys, invalidate after mutations/events and retain slower
fallback intervals for expensive data.

Persist the cache, clear it on session boundaries and version its semantics so
old open tabs discard incompatible snapshots. New dashboard loading should use
queries/invalidation instead of new manual `useEffect`/`fetch`/`useState` polling.

## Consequences

- Revisiting a tab can show data immediately, then refresh it; cached data is not
  a freshness guarantee and must not be the authority for server permissions.
- The first tab-local interval setup stopped refreshing when tabs unmounted;
  the always-mounted observer corrected that lifetime mismatch.
- A shared persisted snapshot displayed stale session/store state. Cache clearing
  at login/logout and a version buster repaired both new and already open tabs;
  a single localStorage key still depends on those lifecycle rules.
- Polling the full catalogue every 30 seconds was a major traffic source.
  BackgroundSync now uses a five-minute catalogue safety net and invalidation,
  rather than treating every query as equally urgent.

## Revisit triggers

- Reassess persistence if sensitive fields enter the cache or multiple accounts
  must coexist in one browser; session clearing alone may no longer suffice.
- Tune freshness when measured request cost or user-visible staleness rises;
  prefer targeted invalidation over adding another manual timer.
- Remove an observer when reliable events make its background polling unnecessary.
