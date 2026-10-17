# Architecture decision records

These records were written retrospectively in October 2026 from the code,
incidents and project constraints. Options are assessed against the factors
that actually applied to this project; these are not meeting minutes. A date
marked `approx.` identifies the supported period of a decision.

In option tables, `+` means a good fit, `~` means a trade-off, and `-` means a
poor fit for the stated drivers. Revisit triggers are proposed review criteria,
not claims that those thresholds have already been reached.

| Number | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-modular-monolith.md) | Keep the core as a modular monolith | Accepted | approx. July 2026 |
| [0002](0002-kaspi-integration-boundaries.md) | Separate official and unofficial Kaspi interfaces | Accepted | approx. July 2026 |
| [0003](0003-guarded-xml-price-feed.md) | Guard full-catalogue XML price updates | Accepted | 2026-08-23 |
| [0004](0004-async-postgresql.md) | Use async PostgreSQL in production | Accepted | 2026-07-26 |
| [0005](0005-encrypt-store-credentials.md) | Encrypt stored integration credentials | Accepted | 2026-07-29 (persistent-key fix) |
| [0006](0006-single-host-rollback-deploy.md) | Deploy the core on one host with verified rollback | Accepted | 2026-07-28 |
| [0007](0007-store-scoped-access.md) | Resolve store context and permissions at the API boundary | Accepted | 2026-07-28 |
| [0008](0008-public-mirror.md) | Publish a filtered mirror with interface stubs | Accepted | 2026-10-10 |
| [0009](0009-react-query-cache-lifecycle.md) | Keep dashboard queries across tab changes | Accepted | 2026-07-30 |
| [0010](0010-quota-aware-offer-sync.md) | Queue price and stock intent behind one writer | Accepted | 2026-08-12 |
| [0011](0011-delivery-classification.md) | Classify customer pickup from raw delivery attributes | Accepted | 2026-08-13 |
