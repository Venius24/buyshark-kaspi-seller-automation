# ADR-0006: Deploy the core on one host with verified rollback

- Status: Accepted
- Date: 2026-07-28

## Context

One operator ran a live tenant on one VPS without staging. Early CI deployed
from any branch, scripts contained a database password, and checkout resets
had no verified recovery path. Containment restricted CI, removed/rotated the
password and hardened deployment before changing authorization.
Commit `238d06d2` added rollback and the frontend release switch on 2026-07-28.

## Decision drivers

1. Reject an unhealthy release and restore service with a checkable outcome.
2. Keep deployment and diagnosis executable by one operator.
3. Stay within the cost and scope of the first manually onboarded customers.
4. Coordinate backend, schema and frontend changes without staging.
5. Make the served revision observable, not just the checkout's Git HEAD.

## Options considered

| Option | Recovery | One-operator fit | Cost/scope | Release coordination | Revision visibility |
| --- | --- | --- | --- | --- | --- |
| VPS + systemd + deploy script | + | + | + | ~ | + |
| Docker Compose on the same VPS | ~ | ~ | ~ | ~ | + |
| PaaS | ~ | ~ | ~ | ~ | + |
| Kubernetes | + | - | - | - | + |

**VPS/systemd:** reuses the installed application and database with a small
number of operating concepts. Health checks and release directories add recovery
without a platform migration; host failure remains outside that protection.

**Compose:** would make runtime packaging more repeatable and separate process
configuration. On the same host it adds image/volume management and still needs
health checks, database compatibility and rollback; it provides no host failover.

**PaaS:** could transfer some runtime supervision and release management to a
provider. Persistent state, long-running jobs and the current deployment layout
would need adaptation; managed hosting does not resolve external interface failures.

**Kubernetes:** provides stronger scheduling and independent rollout mechanisms.
Cluster operation, manifests and stateful-service recovery exceed this stage's
needs; the priority was stability and observability before infrastructure for
50 projected customers.

## Decision

Run [one systemd unit](../../deploy/kaspi-saas.service) behind
[nginx](../../deploy/nginx.conf), using [deploy.sh](../../deploy.sh) from
[CI](../../.github/workflows/ci-cd.yml). Apply Alembic and switch the frontend
release link, restart, then check HTTP health.

Write a passing revision to `.last_good_sha`. On a failed health check, restore
the pre-deploy `PREV_SHA`, dependencies and previous frontend link, then check
health again. The marker records success; the script's rollback target is the
pre-deploy revision, not the marker. Git reset remains part of this mechanism.

## Consequences

- One command coordinates the release and rejects an unhealthy restart.
- [Silent rollbacks](../../README.md#post-mortems) left expected changes unserved:
  startup grew to 50–57 seconds while the health window was 45 seconds. The
  script now permits 120 attempts; inspect the healthy marker and served release,
  since Git HEAD alone did not establish which version users received.
- Code rollback does not undo database migrations; they must remain compatible
  with the previous application. Startup schema creation makes this discipline
  especially important; a release switch is not a database recovery plan.
- The health check establishes HTTP availability, not every worker's readiness.
  One host remains a common failure domain and all core jobs restart together.

## Revisit triggers

- Move toward multiple hosts or managed hosting when required recovery time
  cannot tolerate one-host loss or manual restoration.
- Reassess Compose when repeatable environments or a second operator justify it;
  add staging when releases cannot be verified safely with the current checks.
- Extract a separately supervised worker when one job needs independent recovery;
  require orchestration only when scheduling several hosts becomes a real need.
