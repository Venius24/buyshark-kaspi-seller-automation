# ADR-0003: Guard full-catalogue XML price updates

- Status: Accepted
- Date: 2026-08-23

## Context

Kaspi interprets an XML price list as the full offer catalogue. A two-item test
file caused roughly 800 omitted offers to be withdrawn; see the first
[Post-mortem](../../README.md#post-mortems). The guarded pipeline's
dry-run stage, which diffs the generated price list against the live catalogue,
landed on 2026-08-23. The previous prohibition on file feeds was lifted that
day under explicit completeness and rollout conditions. Point updates remained
the default for urgent changes. The point-update quota was 250 offers per 30
minutes, while the file quota was two uploads per 30 minutes; one complete file
could cover the catalogue.

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
  urgent and small-catalogue changes. Kaspi counts individual offers against a
  separate 250-per-30-minute quota; a large catalogue cannot be refreshed
  quickly through that path. Stores below the quota-window size do not use XML;
  see
  [feed state](../../backend/dumper/feed_state.py).
- An unguarded partial XML file: rejected by the observed withdrawal incident.
- Reuse the old generated file or spreadsheet feed: the earlier file path was
  prohibited after the incident, and the old XML generator did not match an
  accepted full-catalogue file. The pipeline was rebuilt around a checked
  snapshot. The spreadsheet import investigated for *new product creation* was
  a different workflow, not a substitute for repricing.

## Consequences

- Bulk updates can cover a catalogue within the file-upload path rather than
  sending each offer individually.
- The risk profile reverses: a bad point update affects an offer, while a bad
  complete file can affect the catalogue. In an earlier catalogue snapshot,
  roughly 35% of offers had a zero price field, so treating raw values as
  ready-to-publish prices would have been unsafe.
- Snapshot collection, identity checks, upload verification and
  [settlement](../../backend/dumper/feed_settle.py) add code and operational
  delay. A suspected mismatch stops automation and requires a person to inspect
  it.
- A missing or transformed offer identity is a safety failure, not a permissible
  partial success. The corresponding [Post-mortem](../../README.md#post-mortems)
  also records offer loss from normalized identifiers.
- File acceptance counters alone proved insufficient: settlement checks the
  resulting catalogue, since an unmapped zero-stock offer need not mean an
  unexpected withdrawal. Point updates must also wait while a matching file
  update is in flight, without discarding the queued intent.
