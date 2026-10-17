# ADR-0005: Encrypt stored integration credentials

- Status: Accepted
- Date: 2026-07-29 (persistent-key fix)

## Context

Store integrations need credentials such as API tokens and session data.
[Store context resolution](../../backend/security/store_context.py) decrypts the
stored API key before passing it to store-scoped operations. The encryption
module was present by late July; the original encryption decision date is not
recorded. On 2026-07-29, a process-generated key was found to make stored
credentials unreadable after a restart, so a persistent configured key became
an operational requirement.

The separate JWT signing secret also had a hard-coded fallback. Production
configuration was corrected in the same hardening period; the source fallback
still exists.

## Decision

Encrypt sensitive strings with [Fernet](../../backend/security/crypto.py) before
storage. The module reads `ENCRYPTION_KEY` from the environment, loading the
local `.env` when needed, and constructs one cipher at import time. Decryption
returns an empty string and logs an error if it fails.

The code has one active key; it does not implement multi-key decryption or a
key-rotation workflow. If the key is missing or invalid, it generates a
process-local replacement. That fallback is not a durable production key source.

## Alternatives considered

- Plaintext credential storage: not used by the encrypted store field, but the
  repository does not record a formal comparison.
- External key management or multi-key rotation: outside the scope chosen for
  the then single-tenant, single-host deployment. The repository does not
  record a comparison or a design for either option.

## Consequences

- Database contents alone do not expose the plaintext of encrypted credentials.
- Losing or silently replacing the key makes existing ciphertext unreadable. The
  [crypto module](../../backend/security/crypto.py) documents an earlier
  initialization-order failure and now loads the local environment itself before
  choosing a key.
- A one-off script that imports the module before its environment is loaded can
  still select a new key when configuration is absent or invalid. That failure
  later damaged local development ciphertext; the production key was verified
  separately after a real restart.
- Operators must keep the configured key stable and protect it separately from
  the database. There is no automatic rotation path in this code.
- The JWT signing secret is separate from the Fernet key. Its remaining
  [source fallback](../../backend/security/auth.py) means production still
  depends on correct environment configuration.
