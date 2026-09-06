import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import 'package:aura_client/core/ai/voice/live/live_audio_sink.dart';
import 'package:aura_client/core/ai/voice/live/live_mic_source.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_protocol.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_socket.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/voice_live/data/live_voice_history.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_state.dart';

class LiveVoiceViewModel extends Notifier<LiveVoiceState> {
  LiveVoiceSocket? _socket;
  LiveMicSource? _mic;
  LiveAudioSink? _sink;
  StreamSubscription<LiveServerFrame>? _framesSub;
  StreamSubscription<List<int>>? _pcmOutSub;
  StreamSubscription<List<int>>? _micSub;
  StreamSubscription<double>? _micAmpSub;
  final _uuid = const Uuid();

  @override
  LiveVoiceState build() {
    ref.onDispose(_teardown);
    final mic = LiveMicSource();
    return LiveVoiceState(supported: mic.isSupported);
  }

  Future<void> startSession({String? conversationId}) async {
    if (!state.supported) {
      state = state.copyWith(error: 'Live voice is available on Android and iOS only');
      return;
    }
    await _teardown();
    state = state.copyWith(phase: 'connecting', clearError: true);

    final activeId = ref.read(activeConversationIdProvider);
    final String sessionId = conversationId?.isNotEmpty == true
        ? conversationId!
        : (activeId.isEmpty ? 'voice_${_uuid.v4()}' : activeId);
    ref.read(activeConversationIdProvider.notifier).setSession(sessionId);

    _socket = LiveVoiceSocket(ref);
    _mic = LiveMicSource();
    _sink = await createLiveAudioSink();

    try {
      await _socket!.connectAndAuth();
      _framesSub = _socket!.frames.listen(_onFrame);
      _pcmOutSub = _socket!.pcm.listen((bytes) async {
        await _sink?.pushPcm(bytes);
      });
      _socket!.start(sessionId: sessionId, language: 'en');
      state = state.copyWith(sessionId: sessionId, phase: 'listening');

      await _mic!.start(sampleRate: 16000);
      _micSub = _mic!.pcm.listen((chunk) => _socket?.sendPcm(chunk));
      _micAmpSub = _mic!.amplitude.listen((a) {
        state = state.copyWith(micAmplitude: a);
      });
    } catch (e) {
      state = state.copyWith(phase: 'idle', error: e.toString());
      await _teardown();
    }
  }

  void toggleTranscript() {
    state = state.copyWith(transcriptVisible: !state.transcriptVisible);
  }

  Future<void> confirmPending() async {
    final tools = state.pendingTools;
    if (tools.isEmpty) return;
    _socket?.confirm(tools);
    ref.read(pendingConfirmationProvider.notifier).clear();
    state = state.copyWith(pendingTools: const [], phase: 'thinking');
  }

  Future<void> rejectPending() async {
    _socket?.reject();
    ref.read(pendingConfirmationProvider.notifier).clear();
    state = state.copyWith(pendingTools: const [], phase: 'listening');
  }

  Future<void> bargeIn() async {
    _socket?.bargeIn();
    await _sink?.stop();
    state = state.copyWith(phase: 'listening', assistantDraft: '');
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
      case LiveReadyFrame(:final playback):
        unawaited(_sink?.start(sampleRate: playback.sampleRate));
      case LiveStateFrame(state: final phaseName):
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
      case LiveConfirmationFrame(:final tools):
        // The live-voice WS protocol only ever sends tool names, no arguments -- unlike the
        // SSE chat path's `pending_tool_calls`, this frame predates that and is out of scope
        // here. Render with empty arguments so the shared confirmation UI still works, just
        // without per-tool detail for this path.
        ref.read(pendingConfirmationProvider.notifier).setPending(
              tools.map((t) => PendingToolCall(tool: t)).toList(),
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
      case LiveCancelledFrame():
        state = state.copyWith(phase: 'listening', assistantDraft: '');
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
    _framesSub = null;
    _pcmOutSub = null;
    _micSub = null;
    _micAmpSub = null;
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
