# event-affect-engine · 情绪系统 v5.7

**A stateful affect engine for a persistent AI persona.** It turns incoming
messages into *appraisals*, appraisals into slowly-decaying affect state
(hormones, mood, arousal, per-person relationship evidence), and that state into
a bounded prompt suffix that changes *how* the model talks — without letting the
model rewrite the state, double-count a retried message, or claim a message was
delivered when nothing proves it.

Pure Python standard library, no runtime dependencies. Runs as a single
SQLite-backed HTTP service plus an optional MCP bridge, and integrates with an
Android chat host.

一个给持久 AI 人格使用的**状态化情绪引擎**：把来信转成认知评价，把评价转成缓慢衰减的情绪状态
（激素、心境、唤起、针对具体人的关系证据），再把状态编译成有预算的提示后缀去改变"怎么说"。
它不允许模型反过来改写状态、不允许重试的重复消息被记两次、也不允许在没有送达回执时冒充"已发送"。
核心只依赖 Python 标准库，无第三方运行时依赖。

---

## Table of contents

- [Why it is built this way](#why-it-is-built-this-way)
- [Architecture](#architecture)
- [The state, in one screen](#the-state-in-one-screen)
- [Quick start](#quick-start)
- [HTTP surface](#http-surface)
- [Expression layer](#expression-layer)
- [Memory-side bridge (MCP)](#memory-side-bridge-mcp)
- [Android host integration](#android-host-integration)
- [Tests, CI, and what was actually measured](#tests-ci-and-what-was-actually-measured)
- [Honest boundaries](#honest-boundaries)
- [Provenance of this snapshot](#provenance-of-this-snapshot)
- [Repository layout](#repository-layout)
- [License](#license)

---

## Why it is built this way

Five decisions carry most of the design, and each one exists because a real
failure produced it:

| Problem | Design response | Where |
| --- | --- | --- |
| A retried HTTP POST must not feel like two conversations | `event_id` is claimed inside a `BEGIN IMMEDIATE` transaction; a replay returns the same persisted receipt with `deduplicated: true` and does not advance state | `endocrine/store.py:138`, `endocrine/service.py` |
| A message about *someone else* must not land on the person you are talking to | Every fact carries object attribution; relationships are separate beta-distributed evidence budgets per `actor_id`, and dissatisfaction with a third party is not transferred to the current one | `endocrine/appraisal.py`, `relationships` in the snapshot |
| "The model said something nice, so affinity +1" is not evidence | Relationship updates pass an evidence gate (`relation_evidence_gate = 0.70`) and are recorded as *assessments* with kind, source and provenance | `endocrine/model.py:54`, `endocrine/facts.py` |
| Prompt time and world time are different clocks | Dual time: `occurred_at` (when it happened) vs `observed_at` (when the system first learned). Late ingestion of an old fact is an explicit `ingestion_mode=historical_replay`, and future-skew beyond 300 s is refused | `endocrine/time_sync.py`, `endocrine/clock.py`, `docs/线上GLM与时钟修复.md` |
| `success: true` from a chat host is not proof of delivery | A persistent delivery ledger with `operation_id`, claim tokens, `ready → sending → accepted → delivered` states, and reconciliation; an unconfirmed send stays `unknown_delivery` forever instead of being retried blindly | `endocrine/delivery.py`, `docs/delivery_protocol.md` |

Emotion is modelled as *fast* and *slow* channels with different half-lives
(0.75 h fast trace, 1 h arousal, 3 h emotion, 12 h mood-arousal, 24 h mood
valence), coupled weakly to each other, so a single sharp event spikes state and
fades without permanently rewriting the persona's baseline.

## Architecture

```
                     ┌──────────────────────────────────────────────┐
 chat host (Operit,  │  endocrine/  — the engine :5003              │
 any IM adapter) ───►│   api.py     routing, X-Token, X-Admin-Token │
   POST /v2/messages │   classifier.py  cloud LLM → structured facts │
                     │   appraisal.py   facts → goals → affect       │
                     │   model.py       Config, half-lives, domains  │
                     │   store.py       SQLite ledger, dedup, outbox │
                     │   memory.py      salience-ranked long memory  │
                     │   semantic.py    cloud whole-paragraph vectors│
                     │   grounding.py   draft→review dialogue, 2 phase│
                     │   expression.py  affect → bounded prompt      │
                     │   delivery.py    idempotent outbound ledger   │
                     └───────────┬──────────────────┬───────────────┘
                                 │                  │
                    ┌────────────▼───────┐  ┌───────▼────────────────┐
                    │ ob_bridge/ :5020   │  │ ob_extension/          │
                    │ MCP ⇄ HTTP bridge, │  │ affect tools registered│
                    │ signed archives,   │  │ onto the memory server;│
                    │ consolidation      │  │ staged_ob/ = reference │
                    └────────────────────┘  └────────────────────────┘
```

| Path | Role |
| --- | --- |
| `endocrine/` | The engine. HTTP service, appraisal pipeline, SQLite state, expression compiler, delivery ledger. |
| `ob_bridge/` | Standalone MCP⇄bridge service (`/bridge/*`) that lets the memory server pull affect context and push consolidation work. |
| `ob_extension/` | The affect tools that register onto a memory MCP server, plus a staged reference copy of that server (see [License](#license)). |
| `operit/` | Client-side adapters for the Android chat host: bridge, native schedule registry, expression hook, and the LGPL-derived guarded host file. |
| `mobile/` | The importable phone-side toolpkg (no connection configuration inside). |
| `tests/`, `integration_tests/` | 274 offline Python tests + Node contract tests + official-SDK MCP tests. |
| `tools/` | CLI utilities: run checks, calibrate, migrate v3→v4→v5, replay events, evaluate expression/social against a provider, verify manifests. |
| `deploy/` | systemd unit, environment and config templates (all placeholders). |
| `research/` | Earlier-generation design code and the paper notes the social model is built on. |
| `docs/` | Protocol, dual-time repair notes, expression acceptance, deployment, and the release record of this snapshot. |

## The state, in one screen

`GET /v2/snapshot` after two user messages on this exact tree, with no classifier
configured — real values, nothing hand-tuned:

```json
{
  "engine": "event_affect_v5_1",
  "config_version": "affect-v5.1-research-v2", "schema_version": 3,
  "events_processed": 2, "legacy_events_processed": 0, "persisted_revision": 2,

  "hormones": {"oxytocin": 0.4, "dopamine": 0.3, "serotonin": 0.5,
               "cortisol": 0.2, "melatonin": 0.1017},
  "hormones_semantics": "synthetic_compatibility_display_only",
  "emotions": ["平静"], "mood": {"valence": 0.0, "arousal": 0.0},
  "arousal": 0.17962289364944406,
  "core_affect": {"valence": 0.0, "arousal": 0.17962289364944406},
  "social_stress": 0.0, "rel_temp": 0.5,

  "personality": {"sensitivity": 0.5, "resilience": 0.5, "sociability": 0.5,
                  "curiosity": 0.5, "regulation": 0.5},
  "appraisal_goals": {"connection": 1.0, "respect": 1.0, "autonomy": 0.8,
                      "competence": 0.8, "exploration": 0.7, "rest": 0.7},

  "relationships": {"u1": {
      "closeness": 0.5, "trust": 0.5, "satisfaction": 0.5,
      "alpha": 3.0, "beta": 3.0, "contact_count": 2,
      "evidence_count": 0, "positive_evidence": 0, "negative_evidence": 0,
      "confidence": 0.0, "familiarity": 0.004, "observed_interaction_count": 0,
      "perceived_responsiveness": {"care": 0.5, "understanding": 0.5, "validation": 0.5},
      "expressed_boundaries": [], "open_violations": [], "trustworthiness": {},
      "trust_semantics": "uncalibrated_belief_index_not_a_probability_or_permission"}},

  "expectations": [], "disappointment": null,
  "prompt_suffix": "[模拟情绪状态] 平静；效价0.000，激活0.180。语气建议：自然平稳。归因对象：unknown。疲劳0.30；作息模式automatic。混合感受的来源分别见emotions_detail；稳定风格见personality。这是状态参考；事实以事件证据为准，行动另行决定。"
}
```

Two things this output is designed to tell you:

- `hormones_semantics` and `trust_semantics` are **inline disclaimers carried in
  the response itself**. The hormone block is a compatibility readout, and trust is
  an uncalibrated belief index — not a probability, not a permission.
- `归因对象：unknown` and `evidence_count: 0` with mood still at `0.0` is **correct
  offline behaviour**. With no `CLASSIFIER_ENDPOINT`, `UnknownClassifier` ingests the
  event and refuses to guess an appraisal. Affect does not move on unread text, and
  nothing invents a feeling to look alive.

`prompt_suffix` is what the host injects before generation; the affect channels
(`core_affect`, mood, arousal, per-relationship evidence) are computed first, and
`endocrine/core.py:221` derives the hormone readout from them.

## Quick start

Requires Python 3.10+ and (for the bridge) `deploy/requirements-ob.txt`.
IANA tzdata must be resolvable (`tzdata` on bare systems).

```bash
# 1. run the engine with a stub classifier (works offline, classifies as "unknown")
python -B -m endocrine --db data/affect.sqlite3 --host 127.0.0.1 --port 5003

# 2. with a real cloud classifier
export ENDOCRINE_TOKEN='your-own-secret'
export CLASSIFIER_ENDPOINT='https://YOUR-PROVIDER/chat/completions'
export CLASSIFIER_MODEL='your-model'
export CLASSIFIER_KEY='your-key'
export CLASSIFIER_TIMEOUT_SECONDS=60
python -B -m endocrine --db data/affect.sqlite3

# 3. talk to it
curl -H "X-Token: $ENDOCRINE_TOKEN" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:5003/v2/messages -d '{
    "agent_id":"example_agent","event_id":"demo:1","actor_id":"alice",
    "conversation_id":"c1","role":"user","source":"user_message",
    "text":"今天把实验做完了，终于",
    "occurred_at":"2026-10-26T20:00:00+08:00"}'

curl -H "X-Token: $ENDOCRINE_TOKEN" http://127.0.0.1:5003/v2/snapshot

# 4. verify the tree you downloaded
python -B tools/verify_manifest.py
```

Binding anything other than `127.0.0.1`/`localhost`/`::1` without
`ENDOCRINE_TOKEN` raises at startup — the server cannot be left open by accident
(`endocrine/api.py:76`).

For a persistent deployment, `deploy/ai-endocrine-v5.service` is a working
systemd unit and `deploy/environment.example` documents every variable. Nothing
in `deploy/` contains a real value.

## HTTP surface

All routes except `GET /health` require `X-Token`. Administrative ingestion
(system/schedule/time/profile events, `/expect*`) additionally requires
`X-Admin-Token`, and returns **503** rather than falling back when no admin
token is configured. Unknown paths **404**.

| Method / path | Purpose |
| --- | --- |
| `GET /health` | process + storage alive only. It does **not** prove the classifier works. |
| `GET /v2/time` | authoritative server time, `max_future_skew_seconds: 300` |
| `GET /`·`/snapshot`·`/v2/snapshot` | full affect state, optional `?as_of=` replay point |
| `POST /v2/messages` | supported ingestion path (`role` ∈ user/assistant), transactional dedupe |
| `POST /v2/events` | explicit event submission with source/provenance; internal types rejected |
| `GET /v2/integration/changes` | cursor over state conclusions, for clients that poll |
| `POST /v2/memory/search` · `GET /v2/memory` | salience/keyword access to long memory |
| `POST /v2/memory/reflect` | review a hypothesis against real records — 150 s deadline, and any citation whose quote is not a verbatim substring of a stored record is rejected (`endocrine/grounding.py:230`) |
| `POST /v2/expression/context` | read-only expression prompt block; never creates events |
| `POST /v2/expression/review` · `/respond` | reviewed dialogue generation |
| `POST /v2/deliveries` · `GET /v2/deliveries/status` · `POST /v2/deliveries/dispatch` · `/reconcile` | idempotent outbound ledger; **503 until `DELIVERY_GATEWAY_URL` is set** |
| `GET /v2/operit/receipt` · `POST /v2/operit/messages` · `/v2/operit/context` | Android host contract with event receipts |
| `POST /emotion` `/event` `/expect` `/expect_resolve` | legacy v1 compatibility. Unvalidated, **non-idempotent**. Do not retry, do not health-check. |
| `POST /reset` | always **409** — state wiping is offline-only by design |

## Expression layer

`endocrine/expression.py` compiles affect into five graded tendencies
(warmth, directness, playfulness, verbosity, formality) plus situational
verbal habits. Two things worth knowing:

- **Habits are cooled down deterministically, not keyword-matched.** If any
  configured habit appeared in the last two assistant turns, all candidates
  cool; the same habit twice in the last eight also cools. Read-only calls,
  retries and snapshot views never consume a use (`docs/表达层接入与验收.md`).
- **Examples are compiled, not pasted.** First-round cloud testing showed the
  model copied supplied example lines verbatim and overrode the affect signal.
  The compiler now keeps only rhythm statistics (mean sentence length etc.) from
  `examples`; the example sentences themselves never enter the prompt.

Profiles are optional and read from `EXPRESSION_PROFILES_FILE` at startup;
malformed profiles fail startup loudly, and an HTTP request can never change
them. `deploy/expression_profiles.example.json` ships a clearly fictional role.

## Memory-side bridge (MCP)

`ob_bridge/` is a small stdlib service (`127.0.0.1:5020`) exposing
`/bridge/health`, `/bridge/search`, `/bridge/poll`, `/bridge/consolidation`,
`/bridge/archive`, and a signed, scope-partitioned archive of derived affect
conclusions. `ob_extension/affect_bridge_tools.py` registers the affect tools
onto an MCP memory server so that the persona's *memory* and its *mood* are
queryable in one session — with the deliberate rule that the system may not cite
its own earlier inference as evidence for a conclusion.

Semantic retrieval uses whole-paragraph cloud vectors, cached per
(model, actual dimension, full-text digest); there is no local embedding model
and no keyword prefilter pretending to be semantic search
(`endocrine/semantic.py`).

## Android host integration

`operit/` contains the client bridge (`endocrine_api.js`, `endocrine_bridge.js`),
a native schedule registry, and a pre-generation prompt hook. The host-side file
`daily_life.guarded.js` is a **modification of Operit's own `daily_life.js`**, and
stays under LGPL-3.0 with its provenance pinned in `operit/UPSTREAM.json`.

`mobile/情绪工具_脱敏_v1.1.3.toolpkg` is importable into the host, but ships with
**no connection configuration**: you must supply your own API base, token and
exact `chat_id` / `card_id` / `agent_id` / `actor_id` bindings
(`operit/expression_client.example.json` shows the shape). Unbound chats request
nothing.

The hard truth recorded in `docs/delivery_protocol.md`: public Operit v1.12.2's
`sendMessageToAI` returns `chatId/message/aiResponse/receivedAt` and **no external
message receipt**, so mapping its `success=true` to `delivered` would be a fake
confirmation. This release does not fake it; `operit/NATIVE_FIX_GUIDE.md` lists
what the APK must expose first.

## Tests, CI, and what was actually measured

CI runs the offline suite on `ubuntu-latest` (py3.10, py3.12) and
`windows-latest` (py3.12), always Node 22 — see `.github/workflows/checks.yml`.

The numbers below were measured on **this exact tree** before publishing, not
copied from the original release record:

| Check | Result |
| --- | --- |
| `unittest discover -s tests` | **274 tests, 0 skipped, OK** in 75.2 s — Windows 10 / Python 3.12.9 / Node v24.21.0 / SQLite 3.45.3 |
| `tools/run_checks.py` end to end | `all_checks_passed: true` across 13 checks (syntax + Python regression + 5 Node contract suites + generated-Python parse). Its JSON report is generated output and stays untracked; CI uploads one per platform |
| Same suite without resolvable IANA tzdata | 2 errors, `ZoneInfoNotFoundError: Asia/Shanghai` (this is why `tzdata` is documented as a prerequisite, not a code defect) |
| `python_syntax` over every `.py` | passed (94 files) |
| Node syntax + contract suites (`bridge`, `daily_life` creation graph, `expression_hook`, `grounding_bridge`, `native_client`) | all passed |
| Delivery manifests | 137/137 and 131/131 recorded SHA-256 digests matched with 0 mismatches and 0 missing files |
| Live server probe (this tree, loopback, temp DB) | `GET /health` 200 with no token; snapshot with no token **403**; unknown path **404** `not_found`; `POST /reset` **409**; `/expect` with no admin token configured **503** `admin_auth_not_configured`; malformed `/v2/messages` body **400** `invalid_message_role`; non-loopback bind with no token → `ValueError` at construction |
| Idempotency probe | duplicate `POST /v2/messages` with the same `event_id`: receipt byte-identical except `deduplicated: false → true`, and `revision` stayed at `1` instead of advancing |
| Legacy hazard probe | `POST /emotion {}` twice really did move state twice — the non-idempotency warning above is reproduced, not inherited |

## Honest boundaries

Stated plainly, because the interesting claims here are the negative ones:

- **These are engineering parameters, not psychological measurements.** The
  half-lives, coupling constants, evidence gates and expression coefficients are
  design choices. Passing 274 tests means the mechanism behaves as specified. It
  does **not** mean the affect model is psychologically valid, and no human
  validity study was run.
- **No cloud or Android verification travels with this snapshot.** The
  three-platform acceptance in `docs/release-record/` belongs to the original
  deployment. The cloud reports referenced by `docs/` (`reports/expression_cloud_*.json`)
  were not included because they carry provider usage evidence.
- **`tools/evaluate_expression.py` is 8 synthetic scenarios, unseeded.** It can
  show samples; it cannot support "statistically better". Two limitations survived
  into the final run and are recorded rather than hidden: open-ended rest
  discussions still drift long, and one irony sample got an apology line the
  transcript never justified.
- **External social delivery was never invoked** by the packaged code paths, and
  the gateway is disabled by default.
- **The role persona is not auto-promoted**: standalone server-side generation
  requires you to author an explicit `persona.identity`; the phone keeps the
  character card you already have.
- **Third-party files are not this project's claims.** `ob_extension/staged_ob/server.py`
  is a fork of unknown revision and `operit/daily_life.guarded.js` was never
  byte-verified against an installed APK (`installed_binary_match_verified: false`).

## Provenance of this snapshot

This repository is the **v5.7 sanitized share edition**, built from the archive
`情绪系统_v5.7_脱敏分享版_20261020.zip` (138 files), restructured for publication:

- `source/*` promoted to the repository root; the phone toolpkg moved to
  `mobile/`; the archive's release record kept verbatim under
  `docs/release-record/` (its digests intentionally still refer to the original
  archive layout — `tools/verify_manifest.py` covers the published tree).
- Added: `LICENSE` (pure MIT text so license tooling detects it), `NOTICE.md`
  with the license-scope statement, this `README.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `.github/workflows/checks.yml`, `tools/verify_manifest.py`,
  `ob_extension/staged_ob/LICENSE.OMBRE-BRAIN`, and an expanded `.gitignore`
  plus a `.gitattributes` that keeps the tree LF (without it a Windows clone with
  `core.autocrlf=true` checks out CRLF and `MANIFEST.sha256` no longer matches).

The share edition removed real hosts, credentials, personal identifiers, private
workflow templates and raw operations records. That claim was **re-verified
against the original archive** (140 files including the nested toolpkg) with a
byte-level scanner, and re-verified again against the complete published tree
(147 files, generated report included) before the commit below: zero email
addresses, phone numbers, national ID numbers, UUIDs, API keys, PATs, ngrok URLs
or absolute user paths; zero non-loopback IPv4 addresses; no `.env`, key, pem or
sqlite artifacts shipped.
The 14 remaining hits are all deliberate — 7 test-fixture tokens literally named
`secret-fixture-never-output`, `local-test-token`, `virtual-test-only`, and 7
occurrences of the author's own first name in comments inside
`research/legacy_databrain/`.

Every URL authority in the tree is one of: loopback (`127.0.0.1`, `0.0.0.0` for
the systemd unit), reserved-for-documentation hosts (`example.invalid`,
`provider.invalid`, `evil.invalid`, `server.example.invalid`), `YOUR-*`
placeholders, `github.com` attribution links, `fsf.org` (license text), the
public Volcengine ARK endpoint in `deploy/environment.example`, or academic
paper pages (affective-science.org, arxiv, ACL anthology, PubMed, Yale's Clark
Relationship Lab, NWU, AOM). What remains identifying is intentional: product
names, that sample endpoint, and third-party attribution.

## Repository layout

```
.
├── LICENSE  NOTICE.md  README.md  SECURITY.md  CONTRIBUTING.md  MANIFEST.sha256
├── pyproject.toml            # event-affect-engine 5.7.0, stdlib-only
├── endocrine/                # engine: api, service, store, appraisal, classifier,
│                             #   semantic, grounding, expression, delivery, model
├── ob_bridge/                # MCP<->HTTP bridge + signed archives
├── ob_extension/             # affect MCP tools + staged reference server (MIT)
├── operit/                   # Android host adapters, hook, LGPL-derived guarded file
├── mobile/                   # importable toolpkg, no connection config
├── tests/                    # 274 offline tests
├── integration_tests/        # official MCP SDK client/server over JSON + SSE
├── tools/                    # run_checks, calibrate, migrate_v4/v5, replay_events,
│                             #   evaluate_*, verify_manifest, package_expression_hook
├── deploy/                   # systemd unit + *.example config, no real values
├── research/                 # legacy engine + social-affect paper notes
├── docs/                     # protocol, dual-time, expression acceptance, deployment
│   └── release-record/       # original manifests, acceptance summary, sanitization report
└── reports/                  # sanitized_local_checks.json (this snapshot's own re-run)
```

## License

- **Original code: MIT** (`LICENSE`), © 2026 the event-affect-engine authors.
- **`operit/daily_life.guarded.js` and its patch: LGPL-3.0** (Operit,
  `AAswordman/Operit`, tag `v1.12.2`, commit `dbf71916…`); text in
  `operit/LICENSE.upstream`, provenance in `operit/UPSTREAM.json`.
- **`ob_extension/staged_ob/server.py`: MIT** (Ombre Brain, `P0lar1zzZ/Ombre-Brain`);
  text in `ob_extension/staged_ob/LICENSE.OMBRE-BRAIN`, with its provenance caveats.
- Design ideas were read from seven public projects with **no code copied**,
  recorded per-commit in `docs/github_sources.json` and
  `docs/GitHub_OB相关项目核对.json`.

Read `NOTICE.md` before relicensing. Related work by the same author lineage:
[`databrain`](https://github.com/Jeff-NJJ/databrain) (the generation this engine
grew out of, kept in `research/legacy_databrain/`).

---

*Provided as-is, with no warranty, and with the limitations above written down
instead of buried. If you use this to build a persona that someone talks to,
please inherit the habit of recording what did not work.*
