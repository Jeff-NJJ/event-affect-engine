# Third-Party Components and License Map

Everything in this repository is open source, but not under one single license.
This file is authoritative for which path is under what terms.

## 1. Original code — MIT

All directories and files not listed below are written for this project and are
released under the MIT license (`LICENSE`). This includes `endocrine/`,
`ob_bridge/`, `ob_extension/affect_*.py`, `operit/endocrine_*.js`,
`operit/native_schedule*.js`, `operit/expression_hook/`, `tests/`,
`integration_tests/`, `tools/`, `deploy/`, `research/`, `docs/` and
`mobile/情绪工具_脱敏_v1.1.3.toolpkg`.

## 2. `operit/daily_life.guarded.js` and `operit/daily_life-v1.12.2-guard.patch` — LGPL-3.0

Modified copies of a file from **Operit** (https://github.com/AAswordman/Operit),
which is `LGPL-3.0`. Full license text: `operit/LICENSE.upstream`.
Provenance record (upstream tag, commit, original file path, original and
modified SHA-256, description of the change): `operit/UPSTREAM.json`.

Upstream reference pinned in that record:

| field | value |
| --- | --- |
| repository | `AAswordman/Operit` |
| tag | `v1.12.2` |
| commit | `dbf71916fae9750cfdc9f9a774f5a0fee56633fb` |
| original file | `app/src/main/assets/packages/daily_life.js` |
| original SHA-256 | `d4572145cfe890ed0b9b8d74395cd6891bfe6e060e7b4f6440797c77d99cbaa4` |
| modified SHA-256 | `81d48216585d45c2fa6eb2898502b44dfbab27785cf8a31ccca33f09d12143be` |

Because these are modifications of an LGPL library rather than a separate
program linking to it, they stay under LGPL-3.0. The companion code in this
repository talks to that file over its documented hook contract and is not a
derivative of it; it is MIT. `UPSTREAM.json` also records that the packaged
copy was **not** byte-verified against the installed APK binary
(`installed_binary_match_verified: false`).

## 3. `ob_extension/staged_ob/server.py` — MIT (Ombre Brain)

A staged, modified working copy of the entrypoint of **Ombre Brain**
(https://github.com/P0lar1zzZ/Ombre-Brain), an MIT-licensed MCP memory server.
License text: `ob_extension/staged_ob/LICENSE.OMBRE-BRAIN`.

Honest provenance limits, so nobody mistakes this for upstream code:

- It is included because it is the integration surface the affect extension in
  `ob_extension/affect_bridge_tools.py` attaches to; the archived delivery
  record in `docs/release-record/` describes it as a staged candidate that was
  never executed against production (`server_executed: false`,
  `production_modified: false`).
- It does **not** hash-match `server.py` on `Ombre-Brain@main`, and the recorded
  source/candidate digests in `ob_extension/staged_ob/staging_report.json` do
  not match the file shipped here either. It is therefore a fork of unknown
  revision, kept for reference.
- Do not assume its architecture, tool list, or behaviour matches any released
  Ombre Brain. If you want the real thing, install
  `P0lar1zzZ/Ombre-Brain`; if you want the affect extension, read
  `ob_extension/affect_bridge_tools.py` and `ob_extension/staged_ob/staging_report.json`
  and port it yourself onto the upstream version you actually run.

## 4. Ideas read from public source, no code copied

`docs/github_sources.json` and `docs/GitHub_OB相关项目核对.json` record the
specific commits of seven other projects (MaiBot, SillyTavern, AIRI,
Open-LLM-VTuber, persona-engine, emotion-engine, graphiti, mem0, MCP python-sdk,
FAtiMA-Toolkit) that were reviewed for architecture. Per those records, only
design ideas were taken and **no source code was copied**, so they impose no
license obligation on this repository. `docs/GitHub借鉴与取舍.md` explains each
adopt/reject decision.

## 5. `research/legacy_databrain/`

Earlier-generation research code from the same author lineage, shipped under the
same MIT license as the rest of this repository. Its module names line up with
the separately published https://github.com/Jeff-NJJ/databrain (also MIT), but
the file digests here differ from that repository, so treat this as a **different
revision**, not a copy of it. `research/legacy_source_hashes.json` carries the
digests of the generation this snapshot was taken from. It is kept for design
context only; nothing in `endocrine/` imports it.
