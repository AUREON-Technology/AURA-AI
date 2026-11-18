import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import 'package:aura_client/core/ai/voice/live/live_audio_sink.dart';
import 'package:aura_client/core/ai/voice/live/live_mic_source.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_protocol.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_socket.dart';
import 'package:aura_client/core/ai/voice/live/pcm_framer.dart';
import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/voice_live/data/live_voice_history.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_state.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Calls the live-voice session is holding for confirmation. Separate from chat's
/// [pendingConfirmationProvider] (UX-05): voice must neither show nor clear chat's pending calls,
/// and approving here approves only what this session's server-side turn is holding (F-04).
class LiveVoicePending extends Notifier<List<PendingToolCall>> {
  @override
  List<PendingToolCall> build() => const [];

  void set(List<PendingToolCall> calls) => state = calls;

  void clear() => state = const [];
}

final liveVoicePendingProvider =
    NotifierProvider<LiveVoicePending, List<PendingToolCall>>(LiveVoicePending.new);

/// The mic a live-voice session records with; overridden in tests (no platform recorder).
final liveMicSourceFactoryProvider = Provider<LiveMicSourceFactory>((ref) => LiveMicSource.new);

/// Builds the playback sink a live-voice session plays replies through (INT-102); overridden in
/// tests (no native audio engine).
final liveAudioSinkFactoryProvider =
    Provider<Future<LiveAudioSink> Function()>((ref) => createLiveAudioSink);

class LiveVoiceViewModel extends Notifier<LiveVoiceState> {
  LiveVoiceSocket? _socket;
  LiveMicSource? _mic;
  LiveAudioSink? _sink;
  StreamSubscription<LiveServerFrame>? _framesSub;
  StreamSubscription<List<int>>? _pcmOutSub;
  StreamSubscription<List<int>>? _micSub;
  StreamSubscription<double>? _micAmpSub;
  StreamSubscription<double>? _sinkAmpSub;

  /// Re-cuts mic audio into the 20 ms frames the server's endpointer counts (INT-116).
  PcmFramer? _framer;

  /// Set by the user's barge-in until the server's `cancelled`: reply audio it had already sent
  /// is still arriving and must not play after the user cut it off.
  bool _dropReplyAudio = false;
  final _uuid = const Uuid();

  /// The language this session asked the server to transcribe: the user's voice language
  /// (the app's language unless the live screen's toggle overrides it; Phase V), forced to
  /// English once a server answers `language_unsupported` (one still on an English-only Whisper
  /// model), so live voice keeps working there instead of failing every time
  /// (docs/plans/arabic-language_plan.md, stage 4). Choosing a language again clears that.
  bool _englishOnlyServer = false;

  String get _sessionLanguage =>
      _englishOnlyServer ? 'en' : ref.read(voiceLanguageProvider).code;

  @override
  LiveVoiceState build() {
    ref.onDispose(_teardown);
    return LiveVoiceState(supported: LiveMicSource.platformSupported);
  }

  Future<void> startSession({String? conversationId}) async {
    if (!state.supported) {
      state = state.copyWith(error: currentL10n.voiceLiveUnsupported);
      return;
    }
    await _teardown();
    state = state.copyWith(phase: 'connecting', clearError: true);

    final activeId = ref.read(activeConversationIdProvider);
    final String sessionId = conversationId?.isNotEmpty == true
        ? conversationId!
        : (activeId.isEmpty ? 'voice_${_uuid.v4()}' : activeId);
    ref.read(activeConversationIdProvider.notifier).setSession(sessionId);

    final socket = ref.read(liveVoiceSocketFactoryProvider)(ref);
    _socket = socket;
    _mic = ref.read(liveMicSourceFactoryProvider)();
    final sink = await ref.read(liveAudioSinkFactoryProvider)();
    // Torn down (or restarted) while the audio engine started.
    if (!identical(_socket, socket)) {
      await sink.dispose();
      return;
    }
    _sink = sink;
    _dropReplyAudio = false;
    // Without native audio the session still works as text; the screen says why it is silent.
    state = state.copyWith(audioUnavailable: !sink.isAvailable);
    _sinkAmpSub = sink.amplitude.listen((a) {
      state = state.copyWith(playbackAmplitude: a);
    });

    // Listen before connecting: a server that refuses the socket outright (4503 feature off,
    // 4429 session already held) closes it before connectAndAuth returns, and the frames
    // stream is broadcast -- an error nobody is subscribed to yet is dropped and the screen
    // would sit on "listening" (INT-015).
    _framesSub = socket.frames.listen(_onFrame);
    _pcmOutSub = socket.pcm.listen((bytes) {
      if (_dropReplyAudio) return;
      unawaited(_sink?.pushPcm(bytes));
    });

    try {
      // A choice saved on an earlier run is read back asynchronously; wait for it so a session
      // opened straight after launch does not start in the device default instead.
      await ref.read(voiceLanguageProvider.notifier).restored;
      await socket.connectAndAuth();
      // Torn down while connecting (the server closed the socket, or the user left).
      if (!identical(_socket, socket) || !socket.isConnected) return;
      socket.start(sessionId: sessionId, language: _sessionLanguage);
      // Still "connecting": the mic starts, and the phase becomes "listening", only once the
      // server's `ready` frame has said which capture rate it expects (INT-020).
      state = state.copyWith(sessionId: sessionId);
    } catch (e) {
      if (!identical(_socket, socket)) return;
      await _fail(e.toString());
    }
  }

  /// Ends the session in the error phase: mic and playback stopped, the socket closed.
  Future<void> _fail(String message) async {
    await _teardown();
    state = state.copyWith(
      phase: 'error',
      error: message,
      micAmplitude: 0,
      playbackAmplitude: 0,
      assistantDraft: '',
    );
  }

  Future<void> _restartInEnglish() async {
    await startSession(conversationId: state.sessionId);
    if (_englishOnlyServer) {
      state = state.copyWith(error: currentL10n.voiceLiveArabicUnavailable);
    }
  }

  /// Starts the mic at the rate the server's `ready` frame announced (INT-020).
  Future<void> _startCapture(int sampleRate) async {
    final mic = _mic;
    if (mic == null || _micSub != null) return;
    try {
      await mic.start(sampleRate: sampleRate);
    } catch (e) {
      _socket?.stop();
      await _fail(e.toString());
      return;
    }
    if (!identical(_mic, mic)) return;
    final framer = PcmFramer(sampleRate: sampleRate);
    _framer = framer;
    _micSub = mic.pcm.listen((chunk) {
      for (final frame in framer.add(chunk)) {
        _socket?.sendPcm(frame);
      }
    });
    _micAmpSub = mic.amplitude.listen((a) {
      state = state.copyWith(micAmplitude: a);
    });
    if (state.phase == 'connecting') {
      state = state.copyWith(phase: 'listening');
    }
  }

  /// Switches the language the server listens for. The start frame fixes a session's language,
  /// so an open session is restarted in the new one -- same conversation, fresh socket.
  Future<void> setLanguage(VoiceLanguage language) async {
    final notifier = ref.read(voiceLanguageProvider.notifier);
    if (ref.read(voiceLanguageProvider) == language && !_englishOnlyServer) return;
    // An explicit choice retries it: the server may have moved to a multilingual model.
    _englishOnlyServer = false;
    await notifier.set(language);
    // An open session restarts in the new language; so does one that just failed (most
    // often because the server refused the previous language). Ended sessions stay ended.
    if (state.phase != 'idle') {
      await startSession(conversationId: state.sessionId);
    }
  }

  void toggleTranscript() {
    state = state.copyWith(transcriptVisible: !state.transcriptVisible);
  }

  Future<void> confirmPending() async {
    final calls = ref.read(liveVoicePendingProvider);
    if (calls.isEmpty && state.pendingTools.isEmpty) return;
    // By approval id, never by tool name; with none (an older server) the session still
    // approves only the turn it is itself holding.
    _socket?.confirm(
      calls.map((c) => c.approvalId).whereType<String>().toList(),
    );
    ref.read(liveVoicePendingProvider.notifier).clear();
    state = state.copyWith(pendingTools: const [], phase: 'thinking');
  }

  Future<void> rejectPending() async {
    _socket?.reject();
    ref.read(liveVoicePendingProvider.notifier).clear();
    state = state.copyWith(pendingTools: const [], phase: 'listening');
  }

  Future<void> bargeIn() async {
    _socket?.bargeIn();
    // Reply audio the server sent before it saw the barge-in is still on its way; it is dropped
    // until the server's `cancelled` says the reply is gone.
    if (_socket != null) _dropReplyAudio = true;
    await _sink?.flush();
    state = state.copyWith(phase: 'listening', assistantDraft: '');
  }

  /// Ends the user's utterance now instead of waiting for the server to hear silence: the partial
  /// last frame goes out (padded with silence to a whole 20 ms frame), then `commit` (INT-116).
  void commitUtterance() {
    final socket = _socket;
    if (socket == null) return;
    final last = _framer?.flush();
    if (last != null) socket.sendPcm(last);
    socket.commit();
  }

  Future<void> endSession() async {
    _socket?.stop();
    await _teardown();
    state = state.copyWith(phase: 'idle', micAmplitude: 0, playbackAmplitude: 0);
  }

  void onAppPaused() {
    // v1: end session rather than add a foreground service.
    unawaited(endSession());
  }

  void _onFrame(LiveServerFrame frame) {
    switch (frame) {
      case LiveReadyFrame(:final capture, :final playback):
        unawaited(_sink?.start(sampleRate: playback.sampleRate));
        unawaited(_startCapture(capture.sampleRate));
      case LiveStateFrame(state: final phaseName):
        // Before `ready` the mic is not running yet; stay on "connecting" until it is.
        if (_micSub == null && state.phase == 'connecting') break;
        state = state.copyWith(phase: phaseName.toLowerCase());
      case LiveTranscriptFrame(:final text, :final final_):
        if (final_ && text.isNotEmpty) {
          final lines = [...state.lines, LiveTranscriptLine(role: 'user', text: text)];
          state = state.copyWith(lines: lines);
          final sid = state.sessionId;
          if (sid != null) {
            unawaited(
              ref.read(liveVoiceHistoryProvider).appendTurn(
                    conversationId: sid,
                    role: 'user',
                    content: text,
                  ),
            );
          }
        }
      case LiveTokenFrame(:final text):
        state = state.copyWith(assistantDraft: state.assistantDraft + text);
      case LiveConfirmationFrame(:final tools, :final calls):
        // The server sends the held calls with their arguments and approval ids; an older
        // server sends names only, which still render (without detail).
        ref.read(liveVoicePendingProvider.notifier).set(
              calls.isNotEmpty
                  ? calls.map(PendingToolCall.fromJson).toList()
                  : tools.map((t) => PendingToolCall(tool: t)).toList(),
            );
        state = state.copyWith(
          pendingTools: tools,
          phase: 'awaiting_confirmation',
        );
      case LiveDoneFrame():
        final draft = state.assistantDraft.trim();
        if (draft.isNotEmpty) {
          final lines = [
            ...state.lines,
            LiveTranscriptLine(role: 'assistant', text: draft),
          ];
          state = state.copyWith(lines: lines, assistantDraft: '');
          final sid = state.sessionId;
          if (sid != null) {
            unawaited(
              ref.read(liveVoiceHistoryProvider).appendTurn(
                    conversationId: sid,
                    role: 'assistant',
                    content: draft,
                  ),
            );
          }
        }
        unawaited(_socket?.reauth());
      case LiveAudioEndFrame():
        // The reply's last sentence is queued: let it play out.
        unawaited(_sink?.endOfUtterance());
      case LiveCancelledFrame():
        // The server dropped the reply (its own barge-in detection, or the user's): stop what is
        // still playing, and play what comes after -- that belongs to the next reply.
        _dropReplyAudio = false;
        unawaited(_sink?.flush());
        state = state.copyWith(phase: 'listening', assistantDraft: '');
      case LiveErrorFrame(:final error)
          when error == 'language_unsupported' && !_englishOnlyServer:
        // The server's speech model is English-only and closed the socket: start again in
        // English, keeping the same conversation, and say why -- the user may have chosen
        // Arabic on purpose.
        _englishOnlyServer = true;
        unawaited(_restartInEnglish());
      case LiveErrorFrame(:final error, :final message)
          when LiveVoiceSocket.terminalErrors.contains(error):
        // The socket is gone: stop the mic and playback rather than leave the screen
        // "listening" to nothing. Each close code carries its own message (INT-015).
        unawaited(_fail(message));
      case LiveErrorFrame(:final message):
        state = state.copyWith(error: message);
      case LivePlaybackHintFrame(:final sampleRate):
        unawaited(_sink?.start(sampleRate: sampleRate));
      default:
        break;
    }
  }

  Future<void> _teardown() async {
    await _framesSub?.cancel();
    await _pcmOutSub?.cancel();
    await _micSub?.cancel();
    await _micAmpSub?.cancel();
    await _sinkAmpSub?.cancel();
    _framesSub = null;
    _pcmOutSub = null;
    _micSub = null;
    _micAmpSub = null;
    _sinkAmpSub = null;
    // A partial frame belongs to no utterance once the session is gone.
    _framer?.reset();
    _framer = null;
    _dropReplyAudio = false;
    await _mic?.dispose();
    await _sink?.dispose();
    await _socket?.dispose();
    _mic = null;
    _sink = null;
    _socket = null;
  }
}

final liveVoiceViewModelProvider =
    NotifierProvider<LiveVoiceViewModel, LiveVoiceState>(LiveVoiceViewModel.new);
