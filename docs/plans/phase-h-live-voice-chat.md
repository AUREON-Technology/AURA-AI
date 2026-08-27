# Phase H — Live Voice Chat

**Status: planned, not started.** Nothing in this document is built. It is a design record
written against commit `bac3110` (backend citations re-checked at `93a4ac5`), following the
same standing rule the roadmap sets out (`docs/AURA_UPGRADE_ROADMAP.md:19-21`): a phase may not
be marked complete without the evidence its exit criteria name, and "built" and "tested" are
not "proven."

---

## Context

AURA already has voice, but it is **push-to-talk and turn-shaped**: `VoiceRecorderButton`
records a WAV → `POST /api/v3/voice/transcribe` → the transcript is dropped into the chat
text field for the user to review → they hit send → the reply text is rendered → a speaker
button on the bubble calls `POST /api/v3/voice/speak` and plays the returned WAV. Every step
is a discrete HTTP request; there is no continuous session, no automatic turn-taking, and no
way to interrupt a reply. The roadmap contains no realtime voice concept — a repo-wide search
for `barge-in`, `VAD`, and `duplex` returns zero hits. (`websocket` appears only in the nginx
Grafana Live comment and as a transitive `websockets` pin in `requirements.lock`; there is no
application WebSocket route.)

What a full-duplex live voice product does differently is one thing: **a persistent
bidirectional stream**. The mic runs continuously, a voice-activity detector decides when the
user stopped talking, generation begins immediately, speech is synthesized and played
*while the answer is still being written*, and speaking over the assistant cancels it
mid-sentence (barge-in). That wraps in a single full-screen surface with an amplitude-reactive
orb, a waveform, and a live transcript — the visual is what makes the remaining latency
legible rather than awkward.

This plan adds that: a new WebSocket session endpoint, a new voice engine package that owns
VAD/endpointing/barge-in, and a new full-screen Flutter screen carrying the app's first
`CustomPainter` work.

Two facts shape every decision below:

- **Everything stays self-hosted.** The existing `faster-whisper` (STT) and Piper/espeak-ng
  (TTS) engines are reused unchanged — no new inference dependency, no vendor. The live
  session is orchestration around ports that already exist.
- **The live turn goes through `AIKernel`.** Today `container.py:226-227` notes that the
  speech ports are reached directly from the `/voice/*` routes and bypass the kernel. That is
  fine for "read this bubble aloud"; it is not fine for a conversation. A live turn is a real
  turn — `TurnRequest(route="voice_live")` into `AIKernel.stream()` — so safety guards, trust
  channels, memory, tools and citations all apply unchanged (ADR-006, roadmap L68: "new
  capability goes *through* the kernel, not beside it").

Scope decisions taken up front: full live duplex rather than a UX veneer over the existing
HTTP endpoints; orb + waveform + transcript rather than one of the three; Android and iOS are
the platforms that count for this to be considered done. Web and desktop hide the entry point
the same way today's recorder already snacks "not available."

**Honest product framing.** This is orchestration around batch Whisper + Piper on a host whose
measured chat p50 is ~56 s (Phase A4, `qwen2.5:3b`, `OLLAMA_NUM_PARALLEL=1`). Sentence-level
TTS improves *time-to-first-audio*, not time-to-full-answer. Sub-second commercial live voice
is out of scope for v1. See [Will not claim](#will-not-claim) before marking the phase done.

---

## Architecture

```
mic ──PCM16/16k──▶ WebSocket /api/v3/voice/live ──▶ LiveVoiceSession
                                                      │
                            EnergyVad + EndpointDetector (speech start/end, barge-in)
                                                      │
                         wrap PCM → WAV ──▶ SpeechToText.transcribe   ← existing port
                                                      │
                                       TurnStreamer.stream(TurnRequest)  ← protocol over kernel
                                                      │
                                    incremental sentence splitter
                                                      │
                         TextToSpeech.synthesize ──▶ wave-decode → PCM  ← existing port
                                                      │
speaker ◀──PCM16 frames + typed JSON frames ──────────┘
```

Nothing in that diagram below `LiveVoiceSession` is new infrastructure. The session is the only
new orchestration, and it lives in the **engine layer** (domain + ports only, no adapters),
which is what lets it be tested end to end with zero infrastructure.

`LiveVoiceSession` depends on a small **`TurnStreamer` protocol** (`async def stream(TurnRequest)
-> AsyncIterator[Mapping]`) rather than the concrete `AIKernel` class. Engines must not import
adapters (`test_dependency_rules.py`); engine→kernel is allowed today, but the protocol keeps
tests injecting an echo-backed stream without a layering smell. The API adapter wires
`container.kernel` as the implementation.

### Why the ports stay batch-shaped

`SpeechToText.transcribe(bytes) -> str` and `TextToSpeech.synthesize(text) -> bytes`
(`aura_core/ports/speech.py`) are **not changed**. Whisper is not a streaming model; the
standard realtime approach is VAD-segmented chunking, which means the session accumulates a
speech segment, endpoints it, **wraps the PCM16 in a WAV container** (see [Audio formats](#audio-formats)),
and calls the existing batch `transcribe`. Likewise TTS is called per *sentence* rather than
per answer, which is what produces speech that starts before generation finishes — subject to
the confirmation and post-hoc-guard buffering rules below.

Adding a streaming Protocol would also risk
`tests/architecture/test_dependency_rules.py::test_ports_are_async`, which requires every port
I/O method to be `async def` — a sync `events() -> AsyncIterator` is not. (A method named
`stream` is already on the non-I/O exempt set; that does not justify changing the speech ports.)

---

## Backend

### New: `aura_backend/aura_core/voice/` (engine package)

| File | Contents |
|---|---|
| `vad.py` | `VadConfig`, `EnergyVad` — RMS + zero-crossing over 20 ms PCM16 frames, adaptive noise floor. Pure functions over `bytes`, no dependencies, deterministic. v1 only — not equivalent to a neural VAD; barge-in uses an *elevated* threshold while SPEAKING. |
| `turn_taking.py` | `EndpointDetector` — the state machine: `min_speech_ms` (reject coughs), `silence_hangover_ms` (end of turn), `max_utterance_ms` (hard cap), and the barge-in rule: speech sustained ≥ `barge_in_ms` **while the session is SPEAKING or THINKING** cancels the in-flight turn task. Cancelling during THINKING does **not** roll back tools that already ran (today's tools are mostly `propose_*`). There is no kernel abort API — wrap `TurnStreamer.stream()` in an `asyncio.Task` and cancel it; `ProviderRouter` already lets `CancelledError` unwind without failover. |
| `sentences.py` | `SentenceAccumulator` — incremental splitter over a token stream. Feeds tokens, yields complete sentences on `. ! ? \n` and Arabic `؟ ۔`, holds back abbreviations, flushes the remainder at stream end. |
| `wav_pcm.py` | Pure helpers: `pcm16_mono_to_wav(pcm, sample_rate)` for STT input; `wav_to_pcm16(wav_bytes) -> (pcm, sample_rate, channels)` via the stdlib `wave` module for TTS output. **Never** assume a fixed 44-byte header. |
| `session.py` | `LiveVoiceSession` — the orchestrator and the state machine (`CONNECTING → LISTENING → THINKING → SPEAKING`, plus a short `AWAITING_CONFIRMATION` when needed). Consumes PCM, drives the above, calls the STT port, builds the `TurnRequest`, consumes `TurnStreamer.stream()`, calls the TTS port per sentence (subject to buffering rules), emits typed outbound frames. Owns barge-in cancellation (cancels the kernel task and drops queued audio). |

Add `"voice"` to `ENGINE_PACKAGES` (not `_ENGINE_PACKAGES`) in
`tests/architecture/test_dependency_rules.py:32-36` so the layering rule is enforced on it like
every other engine.

### New: `aura_backend/aura_core/adapters/in_memory_speech.py`

House rule: every storage/I/O port has a real in-memory adapter so default `pytest` needs no
model. Speech today is **real engine or `None`** — that gap must close for Phase H:

- `InMemoryStt` — `transcribe` accepts WAV (or wraps raw PCM if needed), returns a deterministic
  transcript from a configurable map / echo of fixture bytes.
- `InMemoryTts` — `synthesize` returns a minimal valid WAV (correct `wave` header, configurable
  sample rate, short PCM tone or silence) so session tests exercise the same decode path
  production uses.

These are the doubles `test_live_voice_session.py` injects. Do **not** invent mocks inside the
test file.

### New: `aura_backend/aura_core/api/voice_live.py`

`register_voice_live(router, container, *, authenticator, rate_limiter)` — mirrors the existing
`register_admin_ops` pattern already called from `v3.py:36`, keeping the 838-line `v3.py` from
growing further. Called from inside `build_router`.

**Auth.** WebSockets cannot rely on `Depends(principal)`, and mobile/browser WS clients cannot
always set an `Authorization` header. The handshake is: `accept()`, then wait ≤ 5 s for a first
text frame `{"type":"auth","token":"<jwt>", ...attestation}`. Rather than writing a second auth
implementation, wrap that frame's headers in a small header-carrier object and pass it to the
**same injected `authenticator`** that `aura_service.py:34-101` builds — one JWT verification
path, one `Principal`. Failure closes with `4401`. Attestation in the frame is parity with
`VoiceClient`'s HTTP headers; device attestation is enforced at token *exchange*, not on every
v3 call (`services/jwt_auth.py`).

**JWT TTL.** Access tokens last **15 minutes** (`ACCESS_TOKEN_TTL`). A call-shaped session longer
than that needs a mid-session `reauth` frame (new token after the client refreshes) or a
distinct close code the client uses to refresh and reconnect. Reconnect **drops in-memory
server session state** (VAD/turn machine); the client must send `start` again with the same
`session_id` (chat conversation id) so episodic history still lines up.

**Rate limiting.** `RateLimitMiddleware` and `PayloadSizeLimitMiddleware` are
`BaseHTTPMiddleware` and pass non-HTTP scopes straight through, so the WS route is unmetered by
default. Enforce inside the handler: `rate_limiter.check_all(..., route_class="voice_live")` at
session start and once per completed turn. Add `"voice_live": 12` to `ROUTE_COSTS`
(`security/rate_limit.py:36-44`) — a live turn is one STT pass plus a full chat turn plus N
synthesis subprocesses, so it costs more than `chat`'s 10.

**Capacity.** Each session pins a Whisper forward-pass budget and Piper subprocesses. Cap one
live session per user and `AURA_VOICE_LIVE_MAX_SESSIONS` globally; refuse with close code
`4429`. Close with `4503` when `container.stt_engine` or `tts_engine` is `None`, matching the
existing routes' refusal to silently degrade.

**Workers.** Production gateway runs `--workers ${AURA_WORKERS:-4}`
(`deploy/docker-compose.prod.yml`). An in-process session set is **per worker**, so the
advertised global cap is effectively up to 4× unless the set lives in Redis (the cache port
already backs `SlidingWindowRateLimiter`). **v1 decision:** use a Redis set via the cache port
for per-user and global live-session keys when `AURA_CACHE_BACKEND=redis`; document in-process
fallback as "cap × worker count" when the cache is in-memory. Unit tests inject a fake shared
counter.

### Audio formats

**STT input.** `FasterWhisperEngine` feeds `io.BytesIO(audio)` to `model.transcribe`. The HTTP
`/voice/transcribe` route already uploads **WAV**. Raw PCM16 from the live socket will fail or
decode as garbage unless the session wraps each endpointed segment with
`pcm16_mono_to_wav(pcm, live_sample_rate)` before `transcribe`. Do not change the STT port.

**TTS output.** Piper/espeak return real WAV files. The `fmt` / `fact` chunk layout means the
header is **not always 44 bytes**. Downstream audio is raw PCM16 obtained via stdlib `wave`
(`wav_to_pcm16`), never a fixed slice. Playback sample rate is the **voice's rate** (Piper
lessac-medium is typically 22050 Hz), **not** the 16 kHz mic clock.

**`ready` frame** announces both clocks once:

```json
{
  "type": "ready",
  "capture": {"sample_rate": 16000, "channels": 1, "format": "pcm_s16le"},
  "playback": {"sample_rate": 22050, "channels": 1, "format": "pcm_s16le"}
}
```

(`playback.sample_rate` is taken from the first TTS WAV or from settings once the voice is
known; if a later sentence differs, emit an updated `playback` hint before those frames.)

### Wire protocol

Typed JSON frames for control, binary frames for audio — the same "typed, never string-split"
discipline `/chat/stream` established over **SSE** (TD-12, `v3.py:300-302`). Live voice uses
WebSocket for duplex audio; the frame-typing rule is the analogy, not the transport.

**Client → server:**

| Frame | Role |
|---|---|
| `auth` | JWT + attestation headers (handshake) |
| `start` | `session_id` (must be the chat conversation id), `language`, `voice` |
| binary | PCM16 mono 16 kHz 20 ms frames |
| `barge_in` | optional explicit interrupt (server also detects via VAD) |
| `commit` | manual end-of-turn |
| `confirm` | `tools: [...]` — resume after ADR-006 confirmation (maps to `TurnRequest.confirmed_tools`) |
| `reject` | decline confirmation; return to LISTENING without a second tool turn |
| `reauth` | fresh JWT before the 15-minute access-token TTL expires |
| `stop` | end session |

**Server → client:**

| Frame | Role |
|---|---|
| `ready` | dual capture/playback clocks (see above) |
| `state` | session state machine updates |
| `speech_started` / `speech_ended` | VAD endpointing |
| `transcript` | `text`, `final` — user speech is **post-endpoint only** (batch STT; no partials) |
| `token` | kernel token passthrough (for transcript panel) |
| `citations` / `step` / `actions` / `tool_calls` / `revision` | kernel passthrough unchanged |
| binary | PCM16 at `playback.sample_rate` |
| `audio_end` | end of assistant audio for this turn |
| `cancelled` | barge-in / cancel completed |
| `confirmation` | spoken request + tool names; UI shows existing `ConfirmationCard` |
| `done` | mirrors kernel `done` (includes `needs_confirmation` when present) |
| `error` | failures |

Kernel stream contract today is
`token | step | actions | citations | tool_calls | revision | done | error`
(`kernel.py:388-394`). There is **no** mid-stream `confirmation` frame from the kernel —
`needs_confirmation` is a field on `done`. The session's outbound `confirmation` frame is
**new orchestration** built from that field (and from tool reports), not a rename of an
existing kernel event.

### Confirmation and post-hoc guards (TTS buffering)

**Confirmation is never automatic.** HTTP chat resumes via `ChatRequest.confirmed_tools`
(`v3.py:235`). Live voice needs the same path over the wire.

v1 rule: **do not synthesize until `done` if `needs_confirmation` is non-empty.** Buffer tokens
for the transcript panel; do not call TTS. Then speak a short confirmation request (or emit
text-only if TTS is skipped for that line), emit `confirmation`, wait for `confirm` / `reject`.
On `confirm`, run a second `TurnRequest` with `confirmed_tools` set. A live session must not
become a way around the side-effecting-tool gate.

**Post-hoc `revision` / `error`.** Output guards that need the full answer run *after* tokens
(`kernel.py:396-401`). Chat can replace on-screen text; **speech cannot unsay**. v1 choice:
**hold TTS until `done` when safety is enabled and the turn may be revised** — practically,
buffer sentences until `done` arrives with `guards_complete` (or an `error` / `revision`). That
trades away "speech before generation finishes" on guarded turns for not speaking unredacted
content. When guards are off / `guards_complete` and no revision, sentence-level TTS during the
token stream remains allowed. If audio was already queued before a late `revision` (should not
happen under the hold rule), drop remaining queued PCM and update the transcript panel to the
revised text; do not claim the spoken audio was corrected.

### Language policy (STT)

`AURA_VOICE_STT_MODEL` defaults to **`tiny.en`**. Piper already has Arabic (`ar_JO-kareem-medium`).
Phase F could hide bad Arabic transcripts (user reviews the composer). Phase H cannot.

**v1 decision:** live voice is **English-only** unless the deployment sets a multilingual
Whisper size (e.g. `tiny` / `base`) via `AURA_VOICE_STT_MODEL`. If `start.language` is `ar` (or
any non-English) and the configured model is English-only, refuse the session (or the turn)
with an `error` frame naming the limit — do not silently produce English-noise transcripts.
Arabic punctuation in `SentenceAccumulator` remains for bilingual *answers* when the model and
TTS support them; Arabic **STT** is not claimed until a multilingual model is configured and
proven in the load round-trip.

### Modified backend files

- `aura_core/config/settings.py` — extend `VoiceSettings` (L704-775) with `live_enabled`,
  `live_max_sessions`, `live_sample_rate`, `live_frame_ms`, `live_silence_hangover_ms`,
  `live_min_speech_ms`, `live_barge_in_ms`, `live_max_utterance_ms`, `live_max_output_tokens`
  (default 400 — spoken answers should be short), and optionally `live_playback_sample_rate`
  as a hint until the first TTS WAV is seen. Same `AURA_VOICE_*` env prefix and the same
  bounded `_int(..., minimum=, maximum=)` style as the existing fields.
- `aura_core/container.py` — a `_build_live_voice()` factory attribute next to
  `_build_stt_engine`/`_build_tts_engine` (L502-572), `None` unless the setting is on **and**
  both engines exist. Surface `live` in `container.health()`'s `"voice"` block (L755-758).
- `aura_core/api/v3.py` — one call to `register_voice_live(...)` inside `build_router`.
- `deploy/nginx/templates/aura.conf.template` — a dedicated `location /api/v3/voice/live` block
  **before** `/api/` (which sets `proxy_set_header Connection ""` and would kill the upgrade):
  `proxy_http_version 1.1`, `Upgrade $http_upgrade`, `Connection "upgrade"`,
  `proxy_buffering off`, `proxy_read_timeout 3600s`. Model it on the **Grafana Live** WebSocket
  block at **L216-218** (not L169-171, which is `/console`).
- `docs/AURA_UPGRADE_ROADMAP.md` — add Phase H. While there, correct the stale F2 prose
  (L349-353 still names espeak-ng as the TTS engine; post-baseline work made Piper primary for
  EN and AR with prosody tuning, with espeak-ng as the last-resort fallback).

### Landmine to avoid

`tests/architecture/test_no_external_inference.py` layer 2 lowercases **every `.py` file in the
backend and substring-matches hosted-inference hostnames — including comments and docstrings**.
Documenting this feature by writing "unlike \<vendor\>'s realtime audio API…" in a docstring
fails the build. Describe the design in terms of what it does, not who else does it. (Markdown
is deliberately excluded from that scan, which is why this document may name them. Layer 4
scans `pubspec.yaml` for banned packages; individual `.dart` sources are not scanned.)

---

## Client (Flutter)

### New dependencies

- `web_socket_channel` — **declare as a direct** `pubspec.yaml` dependency (it may already be
  transitive in the lockfile; that is not enough).
- `flutter_soloud` — a real streaming PCM sink. `audioplayers` cannot play a queue of short PCM
  chunks without re-priming the platform player between each, producing an audible stutter at
  every sentence boundary that would undermine the whole feature. soloud also exposes playback
  wave/FFT data, which the waveform ring needs during the speaking state.
  **Risk:** it is a new native dependency and the `build-android` release-APK job is the gate.
  Mitigation: put it behind a `LiveAudioSink` interface so an `audioplayers`-backed
  per-sentence fallback can be swapped in without touching the UI if the gate fails.
  **Second device risk:** iOS `AVAudioSession` fights between `record` (`playAndRecord` /
  `.voiceChat`) and soloud — verify both capture and playback on a real iPhone before calling
  the feature done.

`record ^7.1.1` is already present and already pinned for a real reason
(`pubspec.yaml:66-74` — 5.2.1/6.0.0 Linux-backend breakage). Do not touch the pin. Its
`startStream()` provides the raw PCM stream this needs — the existing recorder only uses file
WAV `start()` today; AEC/`startStream` fields are **new work**, not something already wired.

### New files

```
lib/core/ai/voice/live/
  live_voice_protocol.dart    # sealed frame types, encode/decode (pure, heavily tested)
  live_voice_socket.dart      # connect, auth handshake, reauth, reconnect, frame dispatch
  live_audio_sink.dart        # playback interface + soloud implementation (uses ready.playback)
  live_mic_source.dart        # record.startStream + rolling RMS meter

lib/features/voice_live/data/
  live_voice_history.dart     # thin wrapper: persist turns via ChatRepository

lib/features/voice_live/presentation/
  live_voice_page.dart
  live_voice_view_model.dart  # Notifier<LiveVoiceState>
  widgets/aura_voice_orb.dart
  widgets/aura_voice_waveform.dart
  widgets/live_transcript_panel.dart
  widgets/live_voice_controls.dart
```

`live_voice_socket.dart` copies `VoiceClient`'s shape (`core/ai/voice/voice_client.dart`) for
auth: `tokenManagerProvider.getValidAccessToken()` plus
`DeviceValidationService.getAttestationHeaders()`, sent inside the `auth` / `reauth` frames
rather than as HTTP headers. `live_voice_view_model.dart` follows the `ChatViewModel` house
style exactly — `@immutable` state class with `copyWith` and pure predicates, `Notifier<T>`,
mutations reassign `state`, cross-feature work via `ref.read`.

**MVVM.** Persist history through `features/voice_live/data/` calling existing
`ChatRepository` — do not stuff Drift writes into the ViewModel. **`start.session_id` must be
the chat conversation id** so kernel episodic persist (`kernel.py:547-554`) and Drift history
agree. Reuse `pendingConfirmationProvider` + `ConfirmationCard`; do not duplicate the sheet.
Hide `/voice` on web/desktop.

### Audio capture, and the one thing most likely to go wrong

The mic hears the assistant's own speech. Without platform echo cancellation, server-side VAD
sees that as user speech and the assistant interrupts itself in a loop. **This is the
highest-risk item in the change.**

`record`'s `RecordConfig` must request the voice-communication path, which is what enables
platform AEC/NS:

- **Android:** `AndroidAudioSource.voiceCommunication`, plus `echoCancel` / `noiseSuppress` /
  `autoGain`. Add `MODIFY_AUDIO_SETTINGS` to the manifest (`RECORD_AUDIO` is already there).
- **iOS:** `AVAudioSession` `playAndRecord` with `.voiceChat` mode, `.defaultToSpeaker`,
  `.allowBluetooth`. `NSMicrophoneUsageDescription` already exists but its copy should be
  updated — it currently says "record voice notes for transcription".

Verify the exact `record` 7.x field names during implementation; these config fields moved
between 5.x and 7.x. Defence in depth regardless: the server requires speech sustained for
`barge_in_ms` above an *elevated* threshold before cancelling while speaking. Energy VAD is the
right testable choice and the wrong production VAD for Arabic + speaker echo — treat it as v1
with that elevated threshold, not as equivalent to Silero.

**Backgrounding:** end the session on `AppLifecycleState.paused` rather than adding a
foreground service. A continuous-mic foreground service is a separate piece of work with its own
permission and store-review surface; out of scope for v1.

### Routing and entry point

A **top-level** `GoRoute(path: '/voice')` in `core/routing/router.dart` — outside the
`ShellRoute`, so it presents full-screen like a call, without the nav bar. Entry from the chat
header via a new `ChatHeaderActionButton` (the component already exists). On session end (and
incrementally as turns complete), the turns are written into the existing Drift chat tables
through `ChatRepository` via `live_voice_history.dart`, so a live conversation appears in
normal chat history rather than vanishing.

### The animation

This is the app's **first `CustomPainter`** — there are currently none anywhere in `lib/`. Both
painters take `(state, amplitude, phase)` and implement `shouldRepaint` against those values
only.

**`AuraVoiceOrb`** — a gradient sphere built from `AuraPalette.brandGradient` (indigo→violet)
with an inner specular highlight. The detail that separates a professional orb from a pulsing
circle is that the radius is not uniform: it is deformed by three summed low-frequency sines at
different phases and speeds, so the silhouette is organic and never visibly repeats.

| State | Behaviour |
|---|---|
| Connecting | 4 s breathing scale 0.96↔1.00, desaturated |
| Listening | Radius follows mic amplitude through a spring follower (fast attack, slow release); deformation amplitude scales with input |
| Thinking | Amplitude input drops to zero; a conic shimmer sweeps the orb at ~1.2 rev/s and deformation frequency rises — reads as *working*, not *stalled* |
| Speaking | Radius follows TTS output amplitude; concentric rings emit and fade on syllable-scale peaks |
| Barge-in | 150 ms contraction + desaturation, then straight to Listening |

**`AuraVoiceWaveform`** — 64 bars on a ring around the orb, heights from a banded rolling
history of the active source (mic while listening, soloud's wave data while speaking), each band
with its own smoothing offset so the ring flows rather than jumping.

**`LiveTranscriptPanel`** — user/assistant turns built from `transcript` and `token` frames,
toggleable from the controls, auto-scrolling. User lines appear only after `speech_ended` +
final `transcript` (batch STT).

House rules both painters must follow:

- Reduced motion: `AuraMotion.respecting(context, ...)` and an early static branch under
  `MediaQuery.disableAnimations`, matching `aura_skeleton.dart:43` and `tool_call_chip.dart:102`.
- Colors only from `AuraPalette` roles; any new text color added to `aaTextPairs`
  (`aura_palette.dart:179`), which `tool/check_contrast.dart` reads in CI.
- Cache `Shader`s keyed by size — never allocate a gradient per frame.

---

## Testing

**Backend, default `pytest` (no Docker, no network, no model):**

- `tests/unit/test_voice_vad.py` — synthesized PCM (silence, tone bursts, noise-floor drift)
  against a frame clock; assert exact endpoint timings.
- `tests/unit/test_voice_turn_taking.py` — hangover, min-speech rejection, max-utterance cap,
  and barge-in transitions while SPEAKING and THINKING.
- `tests/unit/test_voice_sentences.py` — splitting across token boundaries, EN + AR punctuation,
  abbreviations, end-of-stream flush.
- `tests/unit/test_wav_pcm.py` — PCM→WAV wrap round-trip; `wave`-based header strip; assert
  playback sample rate can differ from capture rate (no fixed 44-byte assumption).
- `tests/unit/test_live_voice_session.py` — **the behaviour test that matters.** Drive the whole
  session with the `echo` provider and `InMemoryStt` / `InMemoryTts` (real in-memory adapters,
  not mocks), and assert the exact outbound frame sequence, including: barge-in mid-answer;
  `confirm` / `reject` after `confirmation`; TTS held until `done` when
  `needs_confirmation` is set; `revision` drops remaining queued audio.
- `tests/unit/test_voice_live_endpoint.py` — `TestClient.websocket_connect`: auth required, bad
  token → 4401, happy-path frame order, engines absent → 4503, session cap → 4429, mid-session
  `reauth` / expiry close, English-only STT rejects non-English `start.language`.
- `tests/architecture/test_dependency_rules.py` — extended with the `voice` engine package
  (`ENGINE_PACKAGES`).

**Backend, opt-in (`-m load`, real engines):**

- `tests/load/test_live_voice_roundtrip.py` — real Piper synthesizes a sentence; that WAV's PCM
  is fed through the live session as if it were the mic; assert faster-whisper transcribes it, a
  full turn completes, and audio comes back at the playback rate. This is the *proof*, not the
  *build* — the roadmap's standing rule requires it before Phase H can be marked done. It does
  **not** prove AEC, barge-in feel, or 15-minute JWT.

**Client:** `live_voice_protocol_test.dart` (frame round-trip, including `confirm` / `reauth` /
`ready` dual clocks), `live_voice_socket_test.dart` (against a local `dart:io` `HttpServer`
upgrading to WebSocket — no new test dependency), `live_voice_view_model_test.dart` (state
transitions from injected frames, no audio), `aura_voice_orb_test.dart` (pumps at fixed
amplitudes; asserts the reduced-motion static branch) — following the existing
`MockClient`/provider-override pattern in `test/voice_client_test.dart`.

---

## Will not claim

Phase H is **not** done when unit tests pass and the orb animates. Explicitly out of "proven":

- Sub-second time-to-first-audio on the production VPS (Phase A4 chat p50 ~56 s remains the
  generation floor; v1 aims for intelligible first PCM after `speech_ended`, measured on device).
- Partial / streaming user transcripts (STT is batch after VAD endpoint).
- Arabic live STT on the default `tiny.en` deployment.
- Energy VAD parity with commercial neural VADs.
- Echo cancellation and barge-in *feel* (must be checked on a real Android **and** a real iOS
  device; Client CI does not exercise audio).
- Session continuity across JWT expiry without `reauth` / reconnect.
- Global session caps that are exact under multi-worker uvicorn without Redis-backed keys.
- That spoken audio can be unsaid after a post-hoc `revision` (v1 holds TTS until `done` when
  guards apply).

Exit criterion for latency: record **first PCM after `speech_ended`** on device under a named
model and hardware profile; do not extrapolate from unit tests or the orb alone.

---

## Verification

```bash
cd aura_backend
pytest                                                      # aura_core suite
pytest tests/architecture -q                                # layering + self-hosting fitness
TESTING=True python -m unittest discover -p "test_*.py"     # legacy suite — also gated in CI
pytest tests/load/test_live_voice_roundtrip.py -m load -s   # real Piper → real Whisper proof
```

Then prove the wire protocol without Flutter: run `uvicorn aura_service:app`, and drive
`/api/v3/voice/live` with a small throwaway Python WS client that streams a WAV's PCM up and
writes the returned PCM to a file — play it back to confirm the answer is intelligible at the
**playback** sample rate, and that a second burst of speech mid-answer produces a `cancelled`
frame.

**Client honesty:** there is no Flutter toolchain in this environment. `flutter analyze` +
`flutter test` + the release-APK build in Client CI are the only available gate, and they do not
exercise audio at all. Echo cancellation, barge-in feel, `AVAudioSession` + soloud coexistence,
and end-to-end latency must be checked on a real Android and a real iOS device before Phase H
is called done — that will be recorded as an open item, not claimed as verified.

---

## Sequencing

1. Engine package (`voice/` + `wav_pcm.py`) + `InMemoryStt`/`InMemoryTts` + unit tests — no
   transport, fully verifiable on its own.
2. WebSocket adapter, auth/`reauth` handshake, confirm/reject, rate limiting, Redis-or-honest
   session caps, settings, container, nginx/compose.
3. Client transport + audio I/O (`live/` directory), protocol tests, dual-clock sink.
4. Client UI: orb, waveform, transcript, controls, route, chat entry point, history via
   `ChatRepository`, confirmation reuse.
5. Roadmap Phase H entry + the real round-trip proof + the stale-F2 correction + device checks
   recorded under [Will not claim](#will-not-claim).

---

## Noted, not fixed

Two pre-existing issues found while mapping this, both out of scope:

- `PayloadSizeLimitMiddleware(max_bytes=5 MB)` (`aura_service.py:136`) sits *below* the
  `/voice/transcribe` route's own `MAX_AUDIO_BYTES = 25 MB` (`v3.py:471`), so a clip between 5
  and 25 MB is refused by the middleware with a different error than the route's own validation
  would give. The live path streams frames and is unaffected.
- `test_no_external_inference.py`'s banned lists are LLM-vendor-shaped rather than
  speech-vendor-shaped, and no `.dart` file is scanned by any layer (only `pubspec.yaml` at
  layer 4). Nothing in this change relies on either gap — it adds no vendor of any kind — but
  the gaps are worth recording.
