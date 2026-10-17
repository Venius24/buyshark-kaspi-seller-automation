# ADR-0005: Encrypt stored integration credentials

- Status: Accepted
- Date: 2026-07-29 (persistent-key fix)

## Context

Store integrations persist API tokens and session data so background work can
continue after a restart. The early deployment had one tenant, one host and no
separate secret-management infrastructure. On 2026-07-29, process-generated keys
were found to make previously stored credentials unreadable after a restart.
A durable key source was therefore necessary for availability as well as secrecy.

## Decision drivers

1. Keep a database-only disclosure from exposing integration credentials.
2. Decrypt the same stored values reliably across restarts and maintenance tools.
3. Keep key custody manageable for one operator and one host.
4. Avoid adding a remote dependency to every credential read.
5. Leave an explicit path toward rotation and stronger access separation.

## Options considered

| Option | DB disclosure | Restart durability | Simplicity | Local availability | Rotation/access separation |
| --- | --- | --- | --- | --- | --- |
| Plaintext fields | - | + | + | + | - |
| Fernet + configured key | + | ~ | + | + | - |
| KMS/Vault-backed keys | + | + | - | ~ | + |
| Database/disk encryption alone | - | + | ~ | + | ~ |

**Plaintext:** avoids key lifecycle failures and is easy to operate.
It exposes usable marketplace credentials with any logical database copy,
which is an unacceptable expansion of a seller's account exposure.

**Fernet with an environment key:** protects encrypted fields in a database-only
copy without another service. It fits one-host operation, but the operator must
preserve the key separately; losing it makes ciphertext unusable.

**KMS/Vault:** can separate key access and improve rotation/auditing.
It adds provisioning, permissions, recovery and service availability work;
that was outside the chosen single-host scope, rather than a failed experiment.

**Database/disk encryption:** protects storage media while keeping application
access transparent. Alone it does not protect credentials in a logical dump or
an authorized database read, so it cannot replace field encryption here.

## Decision

Encrypt stored strings with [Fernet](../../backend/security/crypto.py).
Load `ENCRYPTION_KEY` from the environment, with local `.env` loading in the
crypto module before cipher initialization. Use one configured key across
processes; the code has no multi-key decryption or automatic rotation workflow.

## Consequences

- A database-only copy does not reveal encrypted plaintext. An attacker with
  both the running application's access and key can still decrypt it.
- Missing/invalid configuration still generates a process-local replacement;
  decryption errors return an empty string. The restart incident was repaired
  by persisting a key, re-encrypting credentials and verifying a real restart.
- A maintenance import before environment loading later damaged local ciphertext.
  The crypto module now loads `.env` itself, but an absent or invalid key remains
  a footgun; startup does not yet fail closed on that configuration error.
- JWT signing uses a separate secret. Production configuration was hardened too,
  but [its source fallback](../../backend/security/auth.py) remains; encryption
  does not compensate for a weak signing configuration.
- Operators must back up and protect the stable key separately from ciphertext;
  rotation requires an explicit re-encryption and recovery procedure.

## Revisit triggers

- Introduce managed key custody or a versioned keyring before a second host,
  multiple operators or compliance requirements demand access separation.
- Revisit immediately after suspected key disclosure, or before routine rotation;
  replacing the only key without migrating ciphertext repeats the incident.
