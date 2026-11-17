# Plan: Arabic (Egyptian) across the whole system

Status: **DRAFT, awaiting owner approval** (2026-11-17). Base: `main` at `0d6c53e`, branch
`anas/kind-rubin-dpm0sq`. Nothing below is built yet.

## Goal

A user can use AURA entirely in Arabic: the Flutter app (mobile, desktop, web) in Egyptian Arabic
and right-to-left, the AI answering in Arabic, Arabic documents searchable, Arabic speech
understood, server-written insights and briefings in Arabic, and the operator console in Arabic.
Arabic should stop being invisible to the text-processing code that silently drops it today.

## User decisions (do not re-ask)

Recorded 2026-11-17 by the owner.

1. **Delivery**: one plan, shipped as staged PRs (stages 1–4 below), each reviewable and shippable
   on its own.
2. **Choosing the language**: follow the device language by default. Settings → Appearance gets
   a Language option (System / English / العربية). It is stored on the device *and* on the
   account (`users.language`, like `users.timezone`), so server-written text and AI replies follow
   it.
3. **AI reply language**: mirror the language of the user's message. If a message has no clear
   language (a link, code only), use the user's chosen app language.
4. **Arabic variety**: **Egyptian Arabic** for UI text and written replies.
5. **AI register**: reply in Egyptian colloquial by default. If the user clearly writes in فصحى,
   reply in فصحى. Formal outputs the user asks for (a letter, a report) follow the request.
6. **UI register**: Egyptian phrasing throughout. Keep tech words Egyptians already use as-is
   (Git, Terminal, API, and established forms like «الإعدادات»).
7. **Digits**: Western 0–9 everywhere, including dates and times in Arabic.
8. **Translations**: Claude drafts all Arabic strings; the owner (or a native reviewer) reviews
   them during the manual test.
9. **Server-written text** (insights, daily briefing): the server writes them in the user's saved
   language when it generates them. Insights already stored keep the language they were written
   in. No contract change.
10. **Embedder**: build `intfloat/multilingual-e5-small` plus a full re-index. Measure Arabic and
    English retrieval before and after, and switch the default only if English does not regress.
    `BAAI/bge-m3` is **documented as the future upgrade** with its costs, not built now.
11. **Speech-to-text**: faster-whisper `small` (multilingual), with a WER report for Arabic and
    English (GAP-020's exit criterion). Whisper `large-v3-turbo` on the GPU inference plane is
    **documented as the future upgrade**, not built now.
12. **Console and daemon**: the console gets Arabic, RTL and a language toggle, with e2e tests
    pinned to English. The daemon's terminal output stays English. Its error reasons gain codes
    that the desktop app translates.
13. **Arabic font**: **Cairo**, bundled in the app (and served locally by the console), as the
    fallback after Outfit and Inter.
14. **Week start**: follow the locale (Arabic/Egypt starts on Saturday) with localized day names.
15. **Currency**: language changes words and number formatting only, never the currency. A
    per-user currency setting is a separate follow-up.
16. **OCR**: Tesseract reads Arabic and English (`ara+eng`).

Already decided before this plan (still binding):
- Arabic should be "a translation file, not a code change". The two source-scan checks
  (`hardcoded_text_ratchet_test.dart`, `rtl_layout_test.dart`) may only get stricter
  (`docs/ux/TRANSFORMATION_STATUS.md`).
- Piper `ar_JO-kareem-medium` stays the Arabic TTS voice.
- The Arabic Postgres `tsvector` configuration must be settled before Postgres FTS lands
  (`docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`). That stays out of scope here; stage 1's Arabic
  normalizer is written so FTS can reuse it.

---

## Stage 1: Language preference, Arabic AI replies, Arabic-blind text processing (backend)

**Why first:** these are correctness bugs in production today. An Arabic document is invisible
to keyword search, and an Arabic prompt is counted as almost zero tokens. Stage 2 also needs the
header and the column.

### Backend (`aura_backend/`)

**Shared Arabic text utilities** (`aura_core/text/arabic.py`, stdlib only, so engines may import
it):
- `fold()`: strip tashkeel and tatweel; fold أ/إ/آ→ا, ى→ي, ة→ه; map Arabic-Indic digits to
  Western. Lift the folding that `safety/guards.py` already does (EV-7) into here. The guards then
  import it, so the two cannot drift apart.
- `is_arabic_char`, `dominant_script(text) -> "ar" | "en" | None`. This is the same rule as
  `adapters/speech_language.py` and the client's `speech_language.dart` (Arabic wins when its
  count is above zero and at least the Latin count). The adapter then delegates to it.
- `light_stem(token)`: strip the common prefixes ال، وال، بال، كال، فال، لل، و and the suffixes
  ها، ات، ون، ين، ية, keeping at least 2 letters. This is a light stemmer, not a root extractor.

**Fixes, each with a test using Arabic fixtures:**

| File | Today | Change |
|---|---|---|
| `knowledge/retrieval.py:35` (BM25) | `[a-z0-9]+` drops all Arabic | Tokenize Arabic runs too: fold, then light-stem. Add a short Arabic stopword list (في، من، على، إلى، عن، أن، ما، هذا، هذه، اللي، ده، دي) |
| `rag/verification.py:29` | Same regex | Same tokenizer, shared |
| `context/tokenizer.py:55` (token counter) | Arabic counts as ~0 tokens, so prompt-budget overflow | Count Arabic words at a conservative tokens-per-word ratio. Pin it against tiktoken-free reference counts in a test, so the budget over-estimates rather than under-estimates |
| `knowledge/chunking.py:58` | A split needs `.!?` then a Latin capital | Also split on `؟ ! . ؛` and a newline when the next character is Arabic |
| `safety/guards.py:629` (groundedness) | Reads `[a-z0-9']` only, so it always allows Arabic | Use the shared tokenizer, so Arabic answers are checked like English ones |
| `llm/complexity.py:45-55` | Exact whole-word match misses ال/و-prefixed forms | Match on folded and light-stemmed tokens |
| `rag/adaptive.py:72-91` | English-only routing regexes | Add Arabic (Egyptian and فصحى) cues for the same routes |
| `learning/curation.py:50` | Detects English refusals only | Add Arabic refusal phrases |

- `knowledge/extraction.py` (relies on English capitalisation and verbs) is **not changed**:
  Arabic has no capitalisation, so this needs a model-based extractor. It is recorded as a
  follow-up, and Arabic simply yields fewer extracted entities, as today.
- The encoding guard's removal of LRM/RLM/ZWNJ (`guards.py:204`) is a security control
  (Trojan-Source class). It is **left as is**.

**Language preference:**
- Migration `0021_users_language.py`:
  - adds `users.language VARCHAR(16) NULL`;
  - is additive with no backfill: NULL means "unknown", which resolves to English for
    server-written text, as today;
  - `downgrade()` drops the column.
- `services/user_language.py`, mirroring `services/user_time.py`:
  - `LANGUAGE_HEADER = "X-Client-Language"`;
  - `normalize_language()` accepts BCP-47 (`ar`, `ar-EG`, `en-US`, …), returns `"ar"`, `"en"`
    or None, and rejects anything else.
- Remember the value wherever the timezone is remembered (`api/v2/insights.py`), plus on the v2
  profile/session call the app makes at start. The scheduled briefing has no request, so it reads
  the column.

**Reply language** (decisions 3 and 5):
- The v3 chat request reads `X-Client-Language` into the turn as the *preferred* language. It
  also sets `RoutingHint.language`, which already exists and is never set today
  (`domain/llm.py:222`).
- `prompts/library.py` `_CORE_IDENTITY` gains a language rule rendered with
  `{{preferred_language}}`:
  > Reply in the language of the user's latest message. For Arabic, write Egyptian colloquial
  > Arabic, unless the user writes Modern Standard Arabic; then match it. Formal documents the
  > user asks for follow their request. If the message has no clear language, reply in
  > {{preferred_language}}. Keep code, commands, file paths and identifiers unchanged.
- Bump every template version that embeds `_CORE_IDENTITY`. The kernel computes
  `dominant_script(message)` and passes a concrete hint ("Arabic" or "English"), so a small model
  does not have to infer it.

**Tests:**
- BM25 finds an Arabic document from an Arabic query, with and without ال/diacritics.
- The token counter over-estimates Arabic against a reference.
- Chunking splits Arabic sentences.
- The groundedness guard flags an ungrounded Arabic answer and passes a grounded one.
- Complexity scores prefixed forms.
- Header normalization, and the remember-on-request path.
- The prompt renders with each preferred language.
- `test_no_runtime_ddl` and the architecture fitness tests stay green.
- Migration up/down/up against a throwaway Postgres (`tests/postgres_migrations.py`), if Docker
  is available here.

### Client (stage 1 part only)
- Every API client sends `X-Client-Language` with the app's effective language (the device
  locale until stage 2 adds the picker), wherever it attaches `X-Client-Timezone` and the auth
  headers today.

---

## Stage 2: The Flutter app in Egyptian Arabic, RTL (`aura_client/`)

**Locale selection:**
- `core/locale/locale_controller.dart`: a Riverpod `Notifier<AppLanguage>` (system / en / ar),
  persisted in secure storage under `aura_app_language`, the same as `ThemeModeController`.
- `main.dart` passes `locale:` to `MaterialApp.router` and sets `Intl.defaultLocale`.
- Settings → Appearance gets a Language row with three choices, each in its own script
  ("English", "العربية"). The change applies immediately.
- The effective language feeds the `X-Client-Language` header from stage 1.

**Strings:**
- Extract every hard-coded user-visible string into `app_en.arb` (with `@key` descriptions):
  - `lib/features`: ~210 `Text` plus ~160 named args and snackbars;
  - `lib/desktop`: ~215;
  - `lib/core`, including the 37 messages in `core/network/aura_failure.dart`, keyed by the
    backend's `error` kind (~33 kinds);
  - the `voice_client.dart` failure strings;
  - the `main.dart:110` banner.
- `app_ar.arb` holds the full Egyptian Arabic translation (decisions 4, 6 and 8), including the
  six Arabic plural forms via ICU `plural` wherever a count appears.
- Raw daemon `reason` strings map to codes (see Contract) and to l10n text.
- New `test/ux/arb_parity_test.dart`: every `en` key exists in `ar` with the same placeholders,
  and no `ar` value is empty or identical to English, except an allowlist of kept tech words.
- Tighten the ratchet:
  - `hardcoded_text_ratchet_test.dart` also scans `lib/desktop` and `lib/core`, and named args
    (`label:`, `hintText:`, `tooltip:`, `title:`, `message:`), with a small allowlist for
    non-copy literals;
  - the ceiling is lowered to the post-extraction count, with a target of 0 in `lib/features`.

**Font:**
- Bundle Cairo (OFL; weights 400/500/600/700) under `assets/fonts/` with its `OFL.txt`.
- Add `fontFamilyFallback: ['Cairo']` to `AuraThemeV2` and the legacy theme. Outfit stays
  primary for Latin.

**Direction:**
- Pin `TextDirection.ltr` on:
  - the terminal, code editor, diff, debug and output panes;
  - chat code blocks and inline code;
  - file paths, URLs and shell commands shown in the UI.
- Chat bubbles and the composer pick their direction from the text's first strong character, so
  an Arabic message in an English UI, or the reverse, aligns and reads correctly.
- Check every directional icon under RTL (`arrow_back`/`arrow_forward`, chevrons, `send`) and
  mirror where the meaning is "back/next". Explorer tree expand chevrons follow the platform
  convention for RTL trees.
- `rtl_layout_test.dart` pumps the same screens with the real `ar` locale, not only a forced
  `Directionality`, and adds the Today, Calendar, Settings → Language and desktop IDE shell
  screens.

**Dates and numbers** (decisions 7 and 14):
- One helper, `core/format/aura_format.dart`, wraps `DateFormat` and `NumberFormat` with the
  active locale and forces Western digits (`DateFormat.useNativeDigitsByDefaultFor('ar', false)`
  and the matching NumberFormat setting). A test asserts that "13:30" and "1,234" use Western
  digits in `ar`.
- Replace the 27 formatter uses and their fixed English patterns (`'h:mm a'`, `'MM/dd'`, …) with
  locale skeletons (`jm`, `MMMd`, `yMMMd`).
- The calendar takes day names and the first day of the week from `MaterialLocalizations`
  (Saturday for `ar`).
- One localized relative-time helper replaces the hand-built "Today HH:mm / Yesterday / N days
  ago / Removed in Xh / in Ns".
- Currency: no change of symbol or currency (decision 15). Only the formatting locale changes.

**Voice:** live voice sends the active language instead of the hard-coded `'en'`
(`live_voice_view_model.dart:71`). `/transcribe` sends it as a hint once stage 4 lands, and the
field is ignored by servers that don't know it.

**Platform:**
- iOS and macOS: `CFBundleLocalizations` gets en and ar, `knownRegions` gets ar, and
  `ar.lproj/InfoPlist.strings` holds the 4 + 1 permission prompts.
- Android:
  - `android:supportsRtl="true"`;
  - `res/xml/locales_config.xml` (Android 13 per-app language);
  - `values/strings.xml` plus `values-ar/strings.xml` for the app label.
- Web: `index.html` gets `lang="en"`, and Dart updates `lang` and `dir` when the locale changes.
- The Windows `.rc` metadata and the Linux window title stay as they are (out of scope).

**Tests:**
- The locale controller persists and restores the language.
- The picker changes the app language live.
- Direction detection in chat.
- The format helper's digits and week start.
- ARB parity.
- RTL screen pumps under `ar`.
- The existing suite stays green: `flutter analyze` (info-level counts too) and `flutter test`
  with the actual counts quoted.

---

## Stage 3: Server-written text and the operator console

### Backend
- `services/messages.py`: an `en`/`ar` (Egyptian) catalog for the ~10 insight templates
  (`services/insights.py:77-122`) and the daily briefing (`triggers/briefing.py:33-51`), with an
  Arabic plural helper (0 / 1 / 2 / 3–10 / 11–99 / 100+). The `$` and `ml` units stay (decision
  15).
- Generation picks the language from `users.language`. Rows already stored are untouched
  (decision 9).
- The live-voice error messages (`voice/session.py:270-276`) already carry codes, and the app
  translates them by code (stage 2).
- Backend `message` prose is not translated: the app and console translate by `error` kind.

### Daemon (`aura_hostd/`)
- Add a stable `code` next to every English `reason`/`error` the app can show:
  - `command_policy.dart` verdicts;
  - `containment.dart` `InvalidRootError`;
  - `control_server.dart` errors.

  The change is additive, and older apps keep reading `reason`. CLI output stays English
  (decision 12).
- `dart analyze --fatal-infos` and `dart test`, with a test that every refusal carries a code.

### Console (`aura_admin/`)
- A small in-house i18n with no new dependency:
  - `src/lib/i18n/{en,ar}.ts` dictionaries, a typed `t()` and a React context;
  - the language is stored in an `aura_console_lang` cookie, read by the root layout to set
    `<html lang dir>` on the server, so there is no flash of LTR.
- A toggle in the shell header (EN / ع). All ~160 strings are moved into the dictionaries,
  including the 13 nav labels.
- Backend errors shown in the banner are mapped by `error` kind, falling back to the raw
  message.
- CSS:
  - the ~12 physical rules in `app/globals.css` become logical properties (`margin-inline-start`,
    `border-inline-end`, `inset-inline-start`, `text-align: start`);
  - Cairo is served through `next/font/local` from committed files (no build-time network) and
    added to `--font` after Inter;
  - the Inter-only `font-feature-settings` is scoped to Latin.
- Playwright: the existing specs keep running in English (the default with no cookie). One new
  spec toggles Arabic and checks `dir="rtl"`, a translated heading and the nav.
- `npm run lint`, `npm run build` and `npm run test:e2e` must pass.

---

## Stage 4: Arabic-capable models (measured)

### Embedder (decision 10)
- Configure `intfloat/multilingual-e5-small`: 384 dimensions, the same as `bge-small-en-v1.5`,
  with `query_prefix="query: "` and `document_prefix="passage: "`. The embedder already supports
  these prefixes (`llm/embeddings.py:169-183`).
- **Re-index command** (new, `scripts/reembed_corpus.py`):
  - reads every active chunk from the `DocumentStore` (the source of truth, ADR-007);
  - embeds it into a **new vector collection named for the model**;
  - switches config only after it completes.

  It is resumable and idempotent, and the old collection is kept for rollback.
- **Evaluation**:
  - English: the existing phase-a3 corpus (nDCG@k 0.6082 baseline).
  - Arabic: a new labelled Arabic set of ~100 queries, mixing Egyptian and فصحى, under
    `evaluation/`.
  - The default switches only if English nDCG stays within 1 point (the R2.4 rule) and Arabic
    improves. The result is committed as a report in `docs/reports/`.
- **Reranker**: `ms-marco-MiniLM-L-6-v2` is English-only. Evaluate
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual, similar size) under the same rule.
  If it fails, skip reranking for Arabic-dominant queries rather than rerank them badly.
- Both compose files (prod/dev parity) change the default.
- **Railway** serves `hash-embed-v1` today (FSA-031). Changing it there is a dashboard variable
  plus a re-index run. That is an **owner step**, listed in the rollout.
- **Future, documented only**: `BAAI/bge-m3` (568M parameters, 1024 dimensions, roughly 5×
  RAM/CPU, needs a new collection). Its costs and the trigger to adopt it go in
  `docs/production/MODEL_REGISTRY.md`.

### Speech-to-text (decision 11, GAP-020)
- Set `VOICE_STT_MODEL=small` (multilingual) in both compose files and bake the model in the
  image the same way `tiny.en` is baked.
- `voice/session.py`: accept `ar` whenever the model is not an `.en` model, and reject only for
  `.en` models, as today.
- `POST /voice/transcribe` gets an optional `language` form field (additive), and auto-detects
  when it is absent.
- WER harness: Piper-synthesized Arabic and English sentences, plus any real recordings the owner
  supplies, are transcribed and scored. The committed `docs/reports/` report is GAP-020's exit
  condition, which needs a real model run. If this environment cannot download the model, the
  script ships and the run is an owner step, and GAP-020 stays open.
- **Future, documented only**: Whisper `large-v3-turbo` on the HF GPU Space inference plane for
  Egyptian dialect quality.

### OCR (decision 16)
- The Dockerfile installs `tesseract-ocr-ara`, and the OCR language default becomes `ara+eng`
  (`config/settings.py:888,927`).
- A test with a small Arabic image fixture is marked to skip when Tesseract is absent from the
  default suite.

---

## Contract (all additive; what older clients see)

| Change | Older clients |
|---|---|
| Request header `X-Client-Language` (optional; `ar`/`en`/BCP-47) | Absent, so `users.language` stays NULL. Server text stays English, and replies still mirror the message |
| `users.language` column (migration 0021) | Invisible |
| v2 profile response gains optional `language` | Ignored by older apps |
| `/voice/transcribe` optional `language` field | Not sent, so the server auto-detects |
| Live-voice start frame `language: "ar"` accepted when the STT model is multilingual | Older app sends `en`, as today |
| Daemon refusals gain `code` next to `reason` | Older app shows `reason`, as today |
| Insight and briefing text is Arabic for users whose saved language is `ar` | Only users who chose Arabic, which only a new app can set |

No route, field or enum is renamed or removed.

## Rollout order

1. **Stage 1** merges first, and Railway's pre-deploy step applies migration 0021. Check
   `/health/ready` → `schema.revision 0021` after the deploy (CLAUDE.md: first merge that adds a
   migration).
2. **Stage 2** (app) after stage 1 is live, since the header is useless before.
3. **Stage 3** (server text, daemon codes, console). The console has no live target yet.
4. **Stage 4** models. The owner schedules the re-index and changes the Railway embedder variable.
   The VPS needs the image rebuild (STT model and Tesseract pack).

**Branches:** this session may push only to `anas/kind-rubin-dpm0sq`. By default the stages ship
one after another from that branch: one PR per stage, with the next stage starting after the
previous PR merges. With the owner's permission, separate `anas/arabic-stage-N` branches would let
stages be reviewed in parallel.

## Verification

Automated, per layer (run locally and the counts quoted, since CI has not run application suites
since `dc87beb`):
- Backend:
  - `pytest`;
  - `pytest tests/architecture -q`;
  - `TESTING=True python -m unittest discover -p "test_*.py"`;
  - migration round-trip on a throwaway Postgres if Docker is available.
- Client: Flutter 3.44.6 fetched per CLAUDE.md, then `flutter pub get`, `build_runner`,
  `flutter analyze` and `flutter test`.
- Daemon: `dart analyze --fatal-infos` and `dart test`.
- Console: `npm run lint`, `npm run build` and `npm run test:e2e`.
- Stage 4: the retrieval eval report and the WER report, committed.

Manual test checklist (device, browser, desktop) is handed over at the end of each stage.

## Out of scope (recorded follow-ups)

- A per-user currency setting (e.g. EGP).
- Arabic-Indic digits.
- `bge-m3` and Whisper `large-v3-turbo` (documented only).
- Translating backend `message` prose (clients translate by `error` kind).
- Model-based Arabic entity extraction.
- The Arabic Postgres `tsvector` configuration (with Postgres FTS).
- The daemon CLI output.
- Windows `.rc` metadata and the Linux window title.
- Translating `docs/` or the end-user guide artifact.
