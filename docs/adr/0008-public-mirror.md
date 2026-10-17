# ADR-0008: Publish a filtered mirror with interface stubs

- Status: Accepted
- Date: 2026-10-10

## Context

After the business closed, the project was prepared as a public portfolio
repository. The private working repository contains operational material and
integrations that are inappropriate for the public version. Commit `846b64d4`
introduced the public mirror builder and scanner on 2026-10-10; the
[README](../../README.md#what-is-not-published) describes the visible limits.
The recorded publication goal was a reproducible public copy with history,
while keeping the private repository as the source of truth.

## Decision

Build a deterministic mirror from an allowlist of publishable paths while
rewriting the history. Scrub private data from published text and block
publication if a final scan finds sensitive material. Replace selected
integration modules throughout the public history with interface-compatible
stubs marked `WITHHELD`.

The public repository keeps the surrounding decision logic and tests where they
remain meaningful. It does not publish code whose purpose is to get around
another platform's protections. The categories of withheld modules are listed in
[What is not published](../../README.md#what-is-not-published).

## Alternatives considered

- Publishing the private tree unchanged: inconsistent with the documented public
  scope and private data review.
- Publishing only a current source snapshot: it would omit the evolution that
  the chosen history-preserving mirror exposes. A detailed comparison is not
  recorded.

## Consequences

- Readers can inspect the architecture and its evolution without receiving the
  withheld implementations or private records.
- The public version cannot exercise every production integration end to end;
  stubs state the boundary explicitly.
- Any change to publication rules can rewrite historical commit identities. The
  mirror must pass its scan gate before publication; stable rules let a later
  run extend the public branch without rewriting earlier published commits.
