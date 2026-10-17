# ADR-0005: Encrypt stored integration credentials

- Status: Accepted
- Date: approx. July 2026

## Context

Store integrations need credentials such as API tokens and session data.
[Store context resolution](../../backend/security/store_context.py) decrypts the
stored API key before passing it to store-scoped operations. The encryption
module was present by late July; the repository does not record the original
decision date.

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
- External key management or multi-key rotation: not evaluated in depth in the
  repository.

## Consequences

- Database contents alone do not expose the plaintext of encrypted credentials.
- Losing or silently replacing the key makes existing ciphertext unreadable. The
  [crypto module](../../backend/security/crypto.py) documents an earlier
  initialization-order failure and now loads the local environment itself before
  choosing a key.
- Operators must keep the configured key stable and protect it separately from
  the database. There is no automatic rotation path in this code.
