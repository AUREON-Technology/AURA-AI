import 'package:flutter/foundation.dart';

@immutable
class LiveTranscriptLine {
  const LiveTranscriptLine({required this.role, required this.text});

  final String role; // user | assistant
  final String text;
}

@immutable
class LiveVoiceState {
  /// connecting | listening | thinking | speaking | awaiting_confirmation | idle | error.
  /// `error` means the session ended on a failure (see [error]); the mic is stopped.
  final String phase;
  final double micAmplitude;
  final double playbackAmplitude;
  final bool transcriptVisible;
  final List<LiveTranscriptLine> lines;
  final String assistantDraft;
  final List<String> pendingTools;
  final String? error;
  final String? sessionId;
  final bool supported;

  /// Native audio could not start (INT-102): replies arrive as text only, and the screen says so.
  final bool audioUnavailable;

  const LiveVoiceState({
    this.phase = 'idle',
    this.micAmplitude = 0,
    this.playbackAmplitude = 0,
    this.transcriptVisible = true,
    this.lines = const [],
    this.assistantDraft = '',
    this.pendingTools = const [],
    this.error,
    this.sessionId,
    this.supported = true,
    this.audioUnavailable = false,
  });

  bool get isBusy =>
      phase == 'thinking' || phase == 'speaking' || phase == 'connecting';

  LiveVoiceState copyWith({
    String? phase,
    double? micAmplitude,
    double? playbackAmplitude,
    bool? transcriptVisible,
    List<LiveTranscriptLine>? lines,
    String? assistantDraft,
    List<String>? pendingTools,
    String? error,
    bool clearError = false,
    String? sessionId,
    bool? supported,
    bool? audioUnavailable,
  }) {
    return LiveVoiceState(
      phase: phase ?? this.phase,
      micAmplitude: micAmplitude ?? this.micAmplitude,
      playbackAmplitude: playbackAmplitude ?? this.playbackAmplitude,
      transcriptVisible: transcriptVisible ?? this.transcriptVisible,
      lines: lines ?? this.lines,
      assistantDraft: assistantDraft ?? this.assistantDraft,
      pendingTools: pendingTools ?? this.pendingTools,
      error: clearError ? null : (error ?? this.error),
      sessionId: sessionId ?? this.sessionId,
      supported: supported ?? this.supported,
      audioUnavailable: audioUnavailable ?? this.audioUnavailable,
    );
  }
}
