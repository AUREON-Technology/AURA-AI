# Phase H — Live Voice Chat

**Status: planned, not started.** Nothing in this document is built. It is a design record
written against commit `bac3110`, following the same standing rule the roadmap sets out
(`docs/AURA_UPGRADE_ROADMAP.md:19-21`): a phase may not be marked complete without the evidence
its exit criteria name, and "built" and "tested" are not "proven."

---

## Context

AURA already has voice, but it is **push-to-talk and turn-shaped**: `VoiceRecorderButton`
records a WAV → `POST /api/v3/voice/transcribe` → the transcript is dropped into the chat
text field for the user to review → they hit send → the reply text is rendered → a speaker
button on the bubble calls `POST /api/v3/voice/speak` and plays the returned WAV. Every step
is a discrete HTTP request; there is no continuous session, no automatic turn-taking, and no
way to interrupt a reply. The roadmap contains no realtime concept at all — a repo-wide search
for `barge-in`, `VAD`, `duplex` and `websocket` returns zero hits.

What ChatGPT's Advanced Voice Mode and Gemini Live actually do differently is one thing:
**a persistent bidirectional stream**. The mic runs continuously, a voice-activity detector
decides when the user stopped talking, generation begins immediately, speech is synthesized and
played *while the answer is still being written*, and speaking over the assistant cancels it
mid-sentence (barge-in). Both wrap that in a single full-screen surface with an
amplitude-reactive orb, a waveform, and a live transcript — the visual is what makes the
remaining latency legible rather than awkward.

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
the platforms that count for this to be considered done.

---

## Architecture

```
mic ──PCM16/16k──▶ WebSocket /api/v3/voice/live ──▶ LiveVoiceSession
                                                      │
                            EnergyVad + EndpointDetector (speech start/end, barge-in)
                                                      │
                                       SpeechToText.transcribe(segment)   ← existing port
                                                      │
                                       AIKernel.stream(TurnRequest)       ← existing kernel
                                                      │
                                    incremental sentence splitter
                                                      │
                                       TextToSpeech.synthesize(sentence)  ← existing port
                                                      │
speaker ◀──PCM16 frames + typed JSON frames ──────────┘
```

Nothing in that diagram below `LiveVoiceSession` is new. The session is the only new
orchestration, and it lives in the **engine layer** (domain + ports only, no adapters), which
is what lets it be tested end to end with zero infrastructure.

### Why the ports stay batch-shaped

`SpeechToText.transcribe(bytes) -> str` and `TextToSpeech.synthesize(text) -> bytes`
(`aura_core/ports/speech.py`) are **not changed**. Whisper is not a streaming model; the
standard realtime approach is VAD-segmented chunking, which means the session accumulates a
speech segment, endpoints it, and calls the existing batch `transcribe`. Likewise TTS is called
per *sentence* rather than per answer, which is what produces speech that starts before
generation finishes.

Adding a streaming Protocol would also risk
`tests/architecture/test_dependency_rules.py::test_ports_are_async`, which requires every port
I/O method to be `async def` — an `events() -> AsyncIterator` is not.

---

## Backend

### New: `aura_backend/aura_core/voice/` (engine package)

| File | Contents |
|---|---|
| `vad.py` | `VadConfig`, `EnergyVad` — RMS + zero-crossing over 20 ms PCM16 frames, adaptive noise floor. Pure functions over `bytes`, no dependencies, deterministic. |
| `turn_taking.py` | `EndpointDetector` — the state machine: `min_speech_ms` (reject coughs), `silence_hangover_ms` (end of turn), `max_utterance_ms` (hard cap), and the barge-in rule: speech sustained ≥ `barge_in_ms` **while the session is SPEAKING** cancels the turn. |
| `sentences.py` | `SentenceAccumulator` — incremental splitter over a token stream. Feeds tokens, yields complete sentences on `. ! ? \n` and Arabic `؟ ۔`, holds back abbreviations, flushes the remainder at stream end. This is what lets speech start before generation finishes. |
| `session.py` | `LiveVoiceSession` — the orchestrator and the state machine (`CONNECTING → LISTENING → THINKING → SPEAKING`). Consumes PCM, drives the above, calls the STT port, builds the `TurnRequest`, consumes `AIKernel.stream()`, calls the TTS port per sentence, emits typed outbound frames. Owns barge-in cancellation (cancels the kernel task and drops queued audio). |

Add `"voice"` to `_ENGINE_PACKAGES` in `tests/architecture/test_dependency_rules.py:32-36` so
the layering rule is enforced on it like every other engine.

### New: `aura_backend/aura_core/api/voice_live.py`

`register_voice_live(router, container, *, authenticator, rate_limiter)` — mirrors the existing
`register_admin_ops` pattern already called from `v3.py:36`, keeping the 838-line `v3.py` from
growing further. Called from inside `build_router`.

**Auth.** WebSockets cannot rely on `Depends(principal)`, and mobile/browser WS clients cannot
always set an `Authorization` header. The handshake is: `accept()`, then wait ≤ 5 s for a first
text frame `{"type":"auth","token":"<jwt>", ...attestation}`. Rather than writing a second auth
implementation, wrap that frame's headers in a small header-carrier object and pass it to the
**same injected `authenticator`** that `aura_service.py:34-101` builds — one JWT verification
path, one `Principal`. Failure closes with `4401`.

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

### Wire protocol

Typed JSON frames for control, binary frames for audio — the same "typed, never string-split"
discipline `/chat/stream` established (TD-12, `v3.py:300-302`).

**Client → server:** `auth`, `start` (`session_id`, `language`, `voice`), **binary** PCM16 mono
16 kHz 20 ms frames, `barge_in`, `commit` (manual end-of-turn), `stop`.

**Server → client:** `ready` (announces `{sample_rate, channels, format:"pcm_s16le"}` once),
`state`, `speech_started`, `speech_ended`, `transcript` (`text`, `final`), `token`,
`citations`/`step`/`actions`/`tool_calls` (passed through from the kernel unchanged), **binary**
PCM audio, `audio_end`, `cancelled`, `confirmation`, `done`, `error`.

Audio downstream is **raw PCM16 with the 44-byte WAV header stripped**, not a WAV per sentence
— the client then feeds one continuous sink and never re-primes a player between sentences.

**Confirmation is never automatic.** When the kernel reports `needs_confirmation` (ADR-006's
taint rule), the session speaks the request and emits a `confirmation` frame; the client shows
the existing confirmation UI. A live voice session must not become a way around the
side-effecting-tool gate.

### Modified backend files

- `aura_core/config/settings.py` — extend `VoiceSettings` (L704-775) with `live_enabled`,
  `live_max_sessions`, `live_sample_rate`, `live_frame_ms`, `live_silence_hangover_ms`,
  `live_min_speech_ms`, `live_barge_in_ms`, `live_max_utterance_ms`, `live_max_output_tokens`
  (default 400 — spoken answers should be short). Same `AURA_VOICE_*` env prefix and the same
  bounded `_int(..., minimum=, maximum=)` style as the existing fields.
- `aura_core/container.py` — a `_build_live_voice()` factory attribute next to
  `_build_stt_engine`/`_build_tts_engine` (L502-572), `None` unless the setting is on **and**
  both engines exist. Surface `live` in `container.health()`'s `"voice"` block (L755-758).
- `aura_core/api/v3.py` — one call to `register_voice_live(...)` inside `build_router`.
- `deploy/nginx/templates/aura.conf.template` — a dedicated `location /api/v3/voice/live` block
  **before** `/api/` (which sets `proxy_set_header Connection ""` and would kill the upgrade):
  `proxy_http_version 1.1`, `Upgrade $http_upgrade`, `Connection "upgrade"`,
  `proxy_buffering off`, `proxy_read_timeout 3600s`. Model it on the Grafana Live block at
  L169-171, which already does exactly this.
- `docs/AURA_UPGRADE_ROADMAP.md` — add Phase H. While there, correct the stale F2 prose
  (L349-353 still names espeak-ng as the TTS engine; eleven post-baseline commits made Piper
  primary for EN and AR with prosody tuning, with espeak-ng as the last-resort fallback).

### Landmine to avoid

`tests/architecture/test_no_external_inference.py` layer 2 lowercases **every `.py` file in the
backend and substring-matches hosted-inference hostnames — including comments and docstrings**.
Documenting this feature by writing "unlike \<vendor\>'s realtime audio API…" in a docstring
fails the build. Describe the design in terms of what it does, not who else does it. (Markdown
is deliberately excluded from that scan, which is why this document may name them.)

---

## Client (Flutter)

### New dependencies

- `web_socket_channel` — cross-platform WS transport.
- `flutter_soloud` — a real streaming PCM sink. `audioplayers` cannot play a queue of short PCM
  chunks without re-priming the platform player between each, producing an audible stutter at
  every sentence boundary that would undermine the whole feature. soloud also exposes playback
  wave/FFT data, which the waveform ring needs during the speaking state.
  **Risk:** it is a new native dependency and the `build-android` release-APK job is the gate.
  Mitigation: put it behind a `LiveAudioSink` interface so an `audioplayers`-backed
  per-sentence fallback can be swapped in without touching the UI if the gate fails.

`record ^7.1.1` is already present and already pinned for a real reason
(`pubspec.yaml:66-74` — 5.2.1/6.0.0 Linux-backend breakage). Do not touch the pin. Its
`startStream()` provides the raw PCM stream this needs.

### New files

```
lib/core/ai/voice/live/
  live_voice_protocol.dart    # sealed frame types, encode/decode (pure, heavily tested)
  live_voice_socket.dart      # connect, auth handshake, reconnect, frame dispatch
  live_audio_sink.dart        # playback interface + soloud implementation
  live_mic_source.dart        # record.startStream + rolling RMS meter

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
`DeviceValidationService.getAttestationHeaders()`, sent inside the `auth` frame rather than as
HTTP headers. `live_voice_view_model.dart` follows the `ChatViewModel` house style exactly —
`@immutable` state class with `copyWith` and pure predicates, `Notifier<T>`, mutations reassign
`state`, cross-feature work via `ref.read`.

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
`barge_in_ms` above an *elevated* threshold before cancelling while speaking.

**Backgrounding:** end the session on `AppLifecycleState.paused` rather than adding a
foreground service. A continuous-mic foreground service is a separate piece of work with its own
permission and store-review surface; out of scope for v1.

### Routing and entry point

A **top-level** `GoRoute(path: '/voice')` in `core/routing/router.dart` — outside the
`ShellRoute`, so it presents full-screen like a call, without the nav bar. Entry from the chat
header via a new `ChatHeaderActionButton` (the component already exists). On session end, the
turns are written into the existing Drift chat tables through `ChatRepository`, so a live
conversation appears in normal chat history rather than vanishing.

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
toggleable from the controls, auto-scrolling.

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
  and barge-in transitions while SPEAKING.
- `tests/unit/test_voice_sentences.py` — splitting across token boundaries, EN + AR punctuation,
  abbreviations, end-of-stream flush.
- `tests/unit/test_live_voice_session.py` — **the behaviour test that matters.** Drive the whole
  session with the `echo` provider and deterministic in-memory STT/TTS doubles that satisfy the
  real ports (the codebase's in-memory-adapters-not-mocks convention), and assert the exact
  outbound frame sequence, including a barge-in that cancels mid-answer.
- `tests/unit/test_voice_live_endpoint.py` — `TestClient.websocket_connect`: auth required, bad
  token → 4401, happy-path frame order, engines absent → 4503, session cap → 4429.
- `tests/architecture/test_dependency_rules.py` — extended with the `voice` engine package.

**Backend, opt-in (`-m load`, real engines):**

- `tests/load/test_live_voice_roundtrip.py` — real Piper synthesizes a sentence; that WAV's PCM
  is fed through the live session as if it were the mic; assert faster-whisper transcribes it, a
  full turn completes, and audio comes back. This is the *proof*, not the *build* — the
  roadmap's standing rule requires it before Phase H can be marked done.

**Client:** `live_voice_protocol_test.dart` (frame round-trip), `live_voice_socket_test.dart`
(against a local `dart:io` `HttpServer` upgrading to WebSocket — no new test dependency),
`live_voice_view_model_test.dart` (state transitions from injected frames, no audio),
`aura_voice_orb_test.dart` (pumps at fixed amplitudes; asserts the reduced-motion static
branch) — following the existing `MockClient`/provider-override pattern in
`test/voice_client_test.dart`.

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
writes the returned PCM to a file — play it back to confirm the answer is intelligible, and that
a second burst of speech mid-answer produces a `cancelled` frame.

**Client honesty:** there is no Flutter toolchain in this environment. `flutter analyze` +
`flutter test` + the release-APK build in Client CI are the only available gate, and they do not
exercise audio at all. Echo cancellation, barge-in feel, and end-to-end latency must be checked
on a real Android and a real iOS device before Phase H is called done — that will be recorded as
an open item, not claimed as verified.

---

## Sequencing

1. Engine package + its unit tests — no transport, fully verifiable on its own.
2. WebSocket adapter, auth handshake, rate limiting, settings, container, nginx/compose.
3. Client transport + audio I/O (`live/` directory), protocol tests.
4. Client UI: orb, waveform, transcript, controls, route, chat entry point.
5. Roadmap Phase H entry + the real round-trip proof + the stale-F2 correction.

---

## Noted, not fixed

Two pre-existing issues found while mapping this, both out of scope:

- `PayloadSizeLimitMiddleware(max_bytes=5 MB)` (`aura_service.py:136`) sits *below* the
  `/voice/transcribe` route's own `MAX_AUDIO_BYTES = 25 MB` (`v3.py:471`), so a clip between 5
  and 25 MB is refused by the middleware with a different error than the route's own validation
  would give. The live path streams frames and is unaffected.
- `test_no_external_inference.py`'s banned lists are LLM-vendor-shaped rather than
  speech-vendor-shaped, and no `.dart` file is scanned by any layer. Nothing in this change
  relies on either gap — it adds no vendor of any kind — but the gaps are worth recording.
