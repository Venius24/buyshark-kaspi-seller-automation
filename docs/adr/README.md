# Architecture decision records

These records describe decisions visible in the published BuyShark code and its history. A date marked `approx.` identifies the period supported by the repository, not a documented decision meeting. Alternatives are listed only to the extent the project records them.

| Number | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-modular-monolith.md) | Keep the core as a modular monolith | Accepted | approx. July 2026 |
| [0002](0002-kaspi-integration-boundaries.md) | Separate official and unofficial Kaspi interfaces | Accepted | approx. July 2026 |
| [0003](0003-guarded-xml-price-feed.md) | Guard full-catalogue XML price updates | Accepted | 2026-08-23 |
| [0004](0004-async-postgresql.md) | Use async PostgreSQL in production | Accepted | 2026-07-26 |
| [0005](0005-encrypt-store-credentials.md) | Encrypt stored integration credentials | Accepted | approx. July 2026 |
| [0006](0006-single-host-rollback-deploy.md) | Deploy the core on one host with verified rollback | Accepted | 2026-07-28 |
| [0007](0007-store-scoped-access.md) | Resolve store context and permissions at the API boundary | Accepted | 2026-07-28 |
| [0008](0008-public-mirror.md) | Publish a filtered mirror with interface stubs | Accepted | 2026-10-10 |
