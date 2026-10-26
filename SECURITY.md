# Security Policy

## What this software is, and what it therefore exposes

The engine is a **stateful, single-tenant affect server** intended to sit behind
a reverse proxy on a private network or on `127.0.0.1`. It was not designed as a
multi-tenant public API. Read `deploy/ai-endocrine-v5.service` and
`deploy/environment.example` before exposing it anywhere.

Threat model assumptions baked into the code (each verified against the
shipped source, line references included):

- Every endpoint except `GET /health` requires `X-Token`, compared with
  `hmac.compare_digest` (`endocrine/api.py:107`).
- **Fail-closed binding**: starting the server on a non-loopback host without
  `ENDOCRINE_TOKEN` raises `ValueError` at construction (`endocrine/api.py:76`).
  An unauthenticated server therefore cannot be exposed by accident.
- Administrative routes (`/expect`, `/expect_resolve`, and system/schedule/
  profile events under `/v2/events`) require a separate `X-Admin-Token`, and
  return **503 `admin_auth_not_configured`** when no admin token is set rather
  than falling back to the normal token (`endocrine/api.py:165`).
- `POST /reset` is not wired to anything: it always returns
  **409 `reset_requires_explicit_offline_migration_or_new_agent`**
  (`endocrine/api.py:209`). Wiping state is an offline operation on purpose.
- Unknown paths 404 instead of silently serving a snapshot (the legacy
  catch-all behaviour is gone in v5.7).
- Access logging excludes query text, bodies and headers; message bodies, model
  reasoning and keys are not logged. Event ids are logged only as a 12-hex-char
  digest.
- The OB bridge binds `127.0.0.1` by default (`ob_bridge/api.py:9`).
- Outbound delivery is disabled unless `DELIVERY_GATEWAY_URL` is set; enabling it
  is an explicit authorization to push the `ready` queue to that gateway.

## Known hard limits — do not "fix" these by weakening them

| Issue | Status |
| --- | --- |
| `POST /emotion` (legacy v1 path) performs no input validation and is **not idempotent**: an empty or malformed body is still ingested as a neutral stimulus, and a missing `event_id` is backfilled with a fresh `uuid4` (`endocrine/api.py:25`), so a retry becomes a second real event | Documented behaviour of the legacy route. Use `/v2/messages`, which claims `event_id` inside a `BEGIN IMMEDIATE` transaction. Never wire the legacy route into a health check or an auto-retry loop. |
| Transport is plain HTTP in the reference deployment | Terminate TLS at your proxy. `deploy/` does not ship TLS. |
| `ob_bridge` archive signing key is a file path you must protect | `deploy/ob_bridge.example.json` points at `/ABS/PRIVATE/affect_archive.key`; derived archives are signed, so a leaked key lets an attacker forge them. |
| Third-party `ob_extension/staged_ob/server.py` is a fork of unknown revision | Do not run it as-is on real memory data. See `NOTICE.md`. |


## Reporting a vulnerability

Open a **private** security advisory on the repository rather than a public
issue: https://github.com/Jeff-NJJ/event-affect-engine/security/advisories/new

Include the endpoint, the request shape, what you expected to be enforced, and
what actually happened. Reproduction scripts are welcome; please redact your own
tokens from them before attaching.

## Before you paste anything into an issue or a PR

This repository is published as a sanitized snapshot. Keep it that way:

- Never commit a real `ENDOCRINE_TOKEN`, `CLASSIFIER_KEY`, provider key, host,
  chat/card/agent id, or a real conversation transcript.
- `*.example` files are the only place configuration belongs in a commit.
- If you attach a report from `tools/`, check it for absolute local paths and
  message text first.
