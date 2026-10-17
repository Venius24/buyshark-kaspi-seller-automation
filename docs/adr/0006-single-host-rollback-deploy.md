# ADR-0006: Deploy the core on one host with verified rollback

- Status: Accepted
- Date: 2026-07-28

## Context

The core service and frontend were deployed to one host with systemd and nginx.
Commit `05bb3090` added that deployment package on 2026-07-26; commit `238d06d2`
hardened the pipeline with rollback and an atomic frontend swap on 2026-07-28.

## Decision

Run the FastAPI app through [one systemd unit](../../deploy/kaspi-saas.service),
route traffic with [nginx](../../deploy/nginx.conf), and deploy through
[the script](../../deploy.sh) from [CI](../../.github/workflows/ci-cd.yml).
Publish the frontend through a release directory switch.

After restart, check application health. Save the passing commit in
`.last_good_sha`; on a failed check, restore that recorded revision and check
health again. The source of truth for the running revision is the last healthy
marker, not merely Git HEAD.

## Alternatives considered

- Kubernetes, PaaS or a containerized core deployment: the repository does not
  record an in-depth evaluation.
- Manual deployment without a health gate: superseded by the tracked rollback
  hardening; a detailed original comparison is not recorded.

## Consequences

- A failed release can be rejected automatically, and frontend release switches
  are atomic.
- The one-host layout has a common failure domain; the deployment script does
  not provide host-level failover.
- The health window must accommodate startup time. A later fix in commit
  `c7cb24c4` extended it after slow startup triggered repeated false rollbacks.
- Rollback may leave Git HEAD ahead of the running revision. This is called out
  in [Post-mortems](../../README.md#post-mortems); operators must inspect
  `.last_good_sha` when diagnosing a deployment.
