# ADR-0008: Publish a filtered mirror with interface stubs

- Status: Accepted
- Date: 2026-10-10

## Context

After the business closed, the project was prepared as a public portfolio with
history. The working repository included operational material and integrations
outside the public scope. Commit `846b64d4` introduced the mirror builder and
scanner; the [README](../../README.md#what-is-not-published) explains the limits.

## Decision drivers

1. Exclude private data and implementations outside the publication scope.
2. Preserve useful architecture, tests and development history.
3. Reproduce the public tree from the private source of truth.
4. Make accidental disclosure detectable before publication.
5. Keep missing implementations explicit to readers.

## Options considered

| Option | Public scope | History | Reproducibility | Disclosure gate | Explicit boundaries |
| --- | --- | --- | --- | --- | --- |
| Publish the working history unchanged | - | + | + | - | - |
| Publish a cleaned current snapshot | + | - | + | + | + |
| Filter/rewrite history and install stubs | + | + | ~ | + | + |

**Unchanged history:** preserves development context with little transformation.
It also exposes material outside the public scope, including earlier versions;
cleaning only the current checkout cannot make that history suitable.

**Current snapshot:** makes review smaller and avoids carrying old content.
It removes the evolution and fixes that help readers understand the engineering
work; it remains an option if historical filtering cannot meet the gate.

**Filtered history:** retains that evolution while enforcing a narrow path scope.
It requires deterministic transformations and scans across the resulting history,
so publication is a maintained build process rather than a one-time file copy.

## Decision

Rewrite history from an allowlist of publishable paths, scrub private text and
block publication when the final scan finds sensitive material. Replace selected
modules throughout history with interface-compatible `WITHHELD` stubs.
Keep surrounding logic and meaningful tests. The public version excludes code
whose purpose is to get around another platform's protections; its boundaries
are listed in [What is not published](../../README.md#what-is-not-published).

## Consequences

- Readers retain code and history with explicit implementation gaps.
- Stubs prevent some production integrations from running end to end; tests of
  withheld implementations cannot establish public-version behavior.
- Filtering mistakes can disclose historical content, so every generated mirror
  must pass the scan gate. The gate reduces disclosure risk, not manual review work.
- Changing transformation rules can change historical commit identities; stable
  rules let later runs extend the public branch without rewriting published commits.

## Revisit triggers

- Rebuild and rescan affected history whenever scope or redaction rules change.
- Stop publication on a detected disclosure; assess already published history
  before treating a clean current tree as sufficient.
- Prefer a reviewed snapshot if maintaining safe reproducible history becomes
  infeasible, or revise stubs when the declared public integration scope changes.
