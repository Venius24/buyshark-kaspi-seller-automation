# ADR-0006: Deploy the core on one host with verified rollback

- Status: Accepted
- Date: 2026-07-28

## Context

The core service and frontend were deployed to one host with systemd and nginx.
Commit `05bb3090` added that deployment package on 2026-07-26; commit `238d06d2`
hardened the pipeline with rollback and an atomic frontend swap on 2026-07-28.
Before the hardening phases, CI could deploy from any branch, deployment reset
the checkout without a verified recovery path, and deployment configuration
contained a hard-coded database password. The password was removed and rotated;
the current script still uses Git reset to select a revision.

## Decision

Run the FastAPI app through [one systemd unit](../../deploy/kaspi-saas.service),
route traffic with [nginx](../../deploy/nginx.conf), and deploy through
[the script](../../deploy.sh) from [CI](../../.github/workflows/ci-cd.yml).
Publish the frontend through a release directory switch.

After restart, check application health. Save the passing commit in
`.last_good_sha`; on a failed check, reset to the pre-deploy Git revision,
restore dependencies and the frontend release link, then check health again.
The marker records the last passing revision; it is not the rollback target in
the current [script](../../deploy.sh).

## Alternatives considered

- Kubernetes, PaaS or a containerized core deployment: the repository does not
  record an in-depth evaluation. At this stage, the author's explicit priority
  was stability and observability for the live service, rather than building
  infrastructure for a projected 50 customers.
- Relying on Git HEAD or a successful code copy as proof of deployment:
  rejected by failures after restart and frontend builds that left an older
  release serving traffic. The health gate and release link must be checked.

## Consequences

- A failed release can be rejected automatically, and frontend release switches
  are atomic.
- The one-host layout has a common failure domain; the deployment script does
  not provide host-level failover. There was no staging environment, so
  containment, tests and a verified rollback path carried extra weight.
- The health window must accommodate startup time. A later fix in commit
  `c7cb24c4` extended it after slow startup triggered repeated false rollbacks.
- Rollback may leave Git HEAD ahead of the running revision. This is called out
  in [Post-mortems](../../README.md#post-mortems); operators must inspect the
  healthy marker and served frontend link when diagnosing a deployment. The
  script does not automatically reverse an already applied database migration.
