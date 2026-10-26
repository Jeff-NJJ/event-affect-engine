# Contributing

Thanks for reading this far — the engine has no UI to polish, so the useful
contributions are protocol, test, and deployment ones.

## Environment

- Python **3.10+** (stdlib only for the core engine; `deploy/requirements-ob.txt`
  is needed only for the MCP bridge).
- Node **22+** for the JavaScript contract tests. `run_checks.py` skips them and
  records `not_run` if Node is missing, so a Node-less box still validates Python.
- IANA timezone data. `tools/nightly_consolidation.py` and the calendar tests
  resolve `Asia/Shanghai` via `zoneinfo`. On a box without system tzdata, install
  the `tzdata` package (2 of the 274 tests error with `ZoneInfoNotFoundError`
  otherwise — measured on a Windows Python that lacked it).

## Run the offline suite

```bash
python -B tools/run_checks.py --timeout-seconds 600 --report reports/verification.json
```

It parses every `.py` file, runs the 274-test `unittest` suite, and executes the
Node syntax/contract checks (`tests/test_bridge.js`, `test_expression_hook.js`,
`test_grounding_bridge.js`, `test_native_client.js`, `test_daily_life.js`), then
writes a machine-readable report and exits non-zero if anything failed. Nothing
in this suite touches the network, a real provider, or an Android device.

MCP-SDK integration tests live in `integration_tests/` and need
`tools/run_ob_checks.py` plus the pinned official SDK.

Before opening a PR, also verify the shipped digests still match:

```bash
python tools/verify_manifest.py
```

## Ground rules

1. **No real values in commits.** No hosts, tokens, provider keys, chat/card ids,
   or transcripts. Configuration changes go in `deploy/*.example`. See
   `SECURITY.md`.
2. **Keep the license map honest.** If you add vendored or adapted third-party
   code, add its license text next to it and a section in `NOTICE.md`.
3. **Fix a failing acceptance case by keeping the report.** `tools/` evaluators
   write JSON reports; when a change is driven by a bad sample, keep the failing
   report in your PR description so the tradeoff is visible.
4. **Do not turn engineering checks into psychology claims.** Half-lives,
   coupling constants and expression coefficients are design parameters. A green
   test suite means the mechanism behaves as specified, not that it is
   affectively valid. If you change a parameter, say what you measured.
5. **Never wire legacy `/emotion` into a retry or health check.** It is
   unvalidated and non-idempotent; `/v2/*` is the supported surface.
6. Small, single-purpose PRs. The store (`endocrine/store.py`) and the protocol
   (`docs/delivery_protocol.md`) carry the invariants — read them before changing
   anything that touches event ingestion or delivery.

## What still needs real-world validation

These are open, and PRs with actual measurements here are the most valuable
contribution you can make:

- Native Android adaptation of the delivery receipts (`operit/NATIVE_FIX_GUIDE.md`
  lists what has to change in the APK; `sendMessageToAI` gives no external
  message id, so `success=true` must not be mapped to `delivered`).
- A reference implementation of the idempotent delivery gateway.
- Re-running the cloud classifier acceptance on a non-Volcengine provider.
