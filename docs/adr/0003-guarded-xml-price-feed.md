# ADR-0003: Guard full-catalogue XML price updates

- Status: Accepted
- Date: 2026-08-23

## Context

Kaspi interprets an XML price list as the full offer catalogue. A two-item test
file caused roughly 800 omitted offers to be withdrawn; see the first
[Post-mortem](../../README.md#post-mortems). The guarded pipeline's
dry-run stage, which diffs the generated price list against the live catalogue,
landed on 2026-08-23.

## Decision

Use XML for bulk updates only after constructing a full snapshot and a
previewable [plan](../../backend/dumper/feed_plan.py). Parse the generated file
back and compare its offer identities with the snapshot. Refuse incomplete or
unsafe plans.

Keep upload behind explicit controls and revalidate the plan immediately before
sending it in [the uploader](../../backend/dumper/feed_uploader.py). Preserve
snapshots and backups, verify the accepted result, and
[disable the feed](../../backend/dumper/feed_state.py) on suspected loss until a
human re-enables it. The [guard](../../backend/dumper/feed_guard.py) also checks
whether active offer identities survive file normalization.

## Alternatives considered

- Point updates through the seller cabinet: retained alongside the feed for
  smaller or individual changes. The code records a separate per-offer update
  quota and uses a minimum catalogue size before considering XML; see
  [feed state](../../backend/dumper/feed_state.py).
- An unguarded partial XML file: rejected by the observed withdrawal incident.
- A legacy spreadsheet export: the repository does not record an in-depth
  comparison with the guarded XML pipeline.

## Consequences

- Bulk updates can cover a catalogue within the file-upload path rather than
  sending each offer individually.
- Snapshot collection, identity checks, upload verification and
  [settlement](../../backend/dumper/feed_settle.py) add code and operational
  delay. A suspected mismatch stops automation and requires a person to inspect
  it.
- A missing or transformed offer identity is a safety failure, not a permissible
  partial success. The corresponding [Post-mortem](../../README.md#post-mortems)
  also records offer loss from normalized identifiers.
