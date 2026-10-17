# ADR-0003: Guard full-catalogue XML price updates

- Status: Accepted
- Date: 2026-08-23

## Context

Kaspi interprets a price-list XML file as the complete offer catalogue. A two-item
file withdrew roughly 800 omitted offers; see [Post-mortems](../../README.md#post-mortems).
File feeds were prohibited after that incident. The prohibition was lifted on
2026-08-23 under completeness and rollout conditions: point writes were capped at
250 offers per 30 minutes, while two file uploads could each cover the catalogue.

## Decision drivers

1. Never withdraw offers merely because a source read or generated file is partial.
2. Reject unknown prices and transformed offer identities before submission.
3. Refresh large catalogues within the platform's separate offer/file quotas.
4. Preserve an urgent path for withdrawals and time-sensitive price changes.
5. Make detection, recovery and human intervention possible after acceptance.

## Options considered

| Option | Completeness | Price/identity safety | Bulk capacity | Urgency | Recovery |
| --- | --- | --- | --- | --- | --- |
| Point writes only | + | ~ | - | + | ~ |
| Unguarded XML/old generated file | - | - | + | - | - |
| Guarded full XML + urgent point writes | + | + | + | ~ | + |

**Point only:** bounds a bad request to the offers being changed and supports
urgent updates. It cannot refresh a large catalogue within one offer window;
queued low-priority changes already competed with time-sensitive writes.

**Unguarded XML:** uses the bulk quota but makes omission a destructive command.
The test-feed incident demonstrated that price acceptance does not prove catalogue
safety; the old generated file also differed from an accepted full-catalogue file.

**Guarded hybrid:** combines catalogue-sized uploads with urgent point writes.
It costs snapshot collection, diffing, identity checks and post-upload settlement,
but makes completeness and recovery prerequisites rather than manual cleanup.

## Decision

Build a full snapshot and previewable [plan](../../backend/dumper/feed_plan.py).
Parse the generated file back, compare identities and reject incomplete reads or
unsafe prices. The [guard](../../backend/dumper/feed_guard.py) checks whether
normalization preserves active offer identities.

Revalidate before [upload](../../backend/dumper/feed_uploader.py), preserve backups
and [settle against the resulting catalogue](../../backend/dumper/feed_settle.py).
[Disable the feed](../../backend/dumper/feed_state.py) on suspected loss until a
person reviews it. The current minimum catalogue size is 250 offers; smaller
stores retain point writes. Start rollout with an unchanged complete file.

## Consequences

- Bulk writes escape the per-offer bottleneck; point writes remain the urgent path.
- The risk profile grows from selected offers to the whole catalogue. A real
  point-writer regression sent zero prices after reading a flat field that was
  zero for roughly 35% of offers. [Shared price extraction](../../backend/kaspi_offer_price.py)
  and rejection of nonpositive prices prevent that bug from becoming a bulk file.
- Identity normalization caused further offer loss; see
  [Post-mortems](../../README.md#post-mortems). A successful upload counter is
  insufficient: guards compare identities and settlement inspects actual offers.
- Snapshot collection and settlement delay bulk writes. Point writes must wait
  for matching in-flight file updates without losing their pending intent.
- Safety stops require an operator; a disabled feed favors preserved offers over
  automatic continuation with uncertain catalogue state.

## Revisit triggers

- Reassess the transport mix when offer/file quotas or official write coverage change.
- Stop bulk writes after any unexplained omission, identity change or settlement
  mismatch; require a new safe unchanged-file rollout before re-enabling changes.
- Reassess point-only operation if a catalogue falls below the bulk threshold or
  settlement latency prevents required urgent actions.
