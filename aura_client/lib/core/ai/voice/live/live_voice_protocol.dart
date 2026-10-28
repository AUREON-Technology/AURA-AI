/// Sealed-style frame types for `/api/v3/voice/live` (Phase H).
///
/// Typed encode/decode — never string-split on a raw stream.
library;

import 'dart:convert';

sealed class LiveClientFrame {
  const LiveClientFrame();

  Map<String, dynamic> toJson();

  String encode() => jsonEncode(toJson());
}

final class LiveAuthFrame extends LiveClientFrame {
  const LiveAuthFrame({required this.token, this.attestation = const {}});

  final String token;
  final Map<String, String> attestation;

  @override
  Map<String, dynamic> toJson() => {
        'type': 'auth',
        'token': token,
        if (attestation.isNotEmpty) 'attestation': attestation,
      };
}

final class LiveStartFrame extends LiveClientFrame {
  const LiveStartFrame({
    required this.sessionId,
    this.language = 'en',
    this.voice,
  });

  final String sessionId;
  final String language;
  final String? voice;

  @override
  Map<String, dynamic> toJson() => {
        'type': 'start',
        'session_id': sessionId,
        'language': language,
        if (voice != null) 'voice': voice,
      };
}

final class LiveBargeInFrame extends LiveClientFrame {
  const LiveBargeInFrame();

  @override
  Map<String, dynamic> toJson() => {'type': 'barge_in'};
}

final class LiveCommitFrame extends LiveClientFrame {
  const LiveCommitFrame();

  @override
  Map<String, dynamic> toJson() => {'type': 'commit'};
}

/// Approves the calls the session is holding, by the approval ids the server issued (F-04).
/// Tool names are never sent: the server resumes exactly the turn it is holding.
final class LiveConfirmFrame extends LiveClientFrame {
  const LiveConfirmFrame({this.approvalIds = const []});

  final List<String> approvalIds;

  @override
  Map<String, dynamic> toJson() => {'type': 'confirm', 'approval_ids': approvalIds};
}

final class LiveRejectFrame extends LiveClientFrame {
  const LiveRejectFrame();

  @override
  Map<String, dynamic> toJson() => {'type': 'reject'};
}

final class LiveReauthFrame extends LiveClientFrame {
  const LiveReauthFrame({required this.token, this.attestation = const {}});

  final String token;
  final Map<String, String> attestation;

  @override
  Map<String, dynamic> toJson() => {
        'type': 'reauth',
        'token': token,
        if (attestation.isNotEmpty) 'attestation': attestation,
      };
}

final class LiveStopFrame extends LiveClientFrame {
  const LiveStopFrame();

  @override
  Map<String, dynamic> toJson() => {'type': 'stop'};
}

sealed class LiveServerFrame {
  const LiveServerFrame();

  static LiveServerFrame? tryParse(dynamic raw) {
    if (raw is! Map) return null;
    final map = Map<String, dynamic>.from(raw);
    final type = map['type']?.toString();
    switch (type) {
      case 'ready':
        return LiveReadyFrame(
          capture: LiveAudioClock.fromJson(
            Map<String, dynamic>.from(map['capture'] as Map? ?? const {}),
          ),
          playback: LiveAudioClock.fromJson(
            Map<String, dynamic>.from(map['playback'] as Map? ?? const {}),
          ),
        );
      case 'state':
        return LiveStateFrame(state: map['state']?.toString() ?? '');
      case 'speech_started':
        return const LiveSpeechStartedFrame();
      case 'speech_ended':
        return const LiveSpeechEndedFrame();
      case 'transcript':
        return LiveTranscriptFrame(
          text: map['text']?.toString() ?? '',
          final_: map['final'] == true,
        );
      case 'token':
        return LiveTokenFrame(text: map['text']?.toString() ?? '');
      case 'confirmation':
        return LiveConfirmationFrame(
          tools: (map['tools'] as List? ?? const [])
              .map((e) => e.toString())
              .toList(),
          calls: (map['calls'] as List? ?? const [])
              .whereType<Map>()
              .map((c) => Map<String, dynamic>.from(c))
              .toList(),
          text: map['text']?.toString() ?? '',
        );
      case 'done':
        return LiveDoneFrame(
          needsConfirmation: (map['needs_confirmation'] as List? ?? const [])
              .map((e) => e.toString())
              .toList(),
        );
      case 'cancelled':
        return const LiveCancelledFrame();
      case 'audio_end':
        return const LiveAudioEndFrame();
      case 'error':
        return LiveErrorFrame(
          error: map['error']?.toString() ?? 'error',
          message: map['message']?.toString() ?? '',
        );
      case 'playback':
        return LivePlaybackHintFrame(
          sampleRate: (map['sample_rate'] as num?)?.toInt() ?? 22050,
        );
      default:
        return LivePassthroughFrame(type: type ?? 'unknown', raw: map);
    }
  }
}

class LiveAudioClock {
  const LiveAudioClock({
    required this.sampleRate,
    required this.channels,
    required this.format,
  });

  final int sampleRate;
  final int channels;
  final String format;

  factory LiveAudioClock.fromJson(Map<String, dynamic> json) => LiveAudioClock(
        sampleRate: (json['sample_rate'] as num?)?.toInt() ?? 16000,
        channels: (json['channels'] as num?)?.toInt() ?? 1,
        format: json['format']?.toString() ?? 'pcm_s16le',
      );
}

final class LiveReadyFrame extends LiveServerFrame {
  const LiveReadyFrame({required this.capture, required this.playback});
  final LiveAudioClock capture;
  final LiveAudioClock playback;
}

final class LiveStateFrame extends LiveServerFrame {
  const LiveStateFrame({required this.state});
  final String state;
}

final class LiveSpeechStartedFrame extends LiveServerFrame {
  const LiveSpeechStartedFrame();
}

final class LiveSpeechEndedFrame extends LiveServerFrame {
  const LiveSpeechEndedFrame();
}

final class LiveTranscriptFrame extends LiveServerFrame {
  const LiveTranscriptFrame({required this.text, required this.final_});
  final String text;
  final bool final_;
}

final class LiveTokenFrame extends LiveServerFrame {
  const LiveTokenFrame({required this.text});
  final String text;
}

final class LiveConfirmationFrame extends LiveServerFrame {
  const LiveConfirmationFrame({required this.tools, this.calls = const [], required this.text});
  final List<String> tools;

  /// The held calls as the server sent them: `approval_id`, `call_id`, `tool`, `arguments`.
  final List<Map<String, dynamic>> calls;
  final String text;
}

final class LiveDoneFrame extends LiveServerFrame {
  const LiveDoneFrame({this.needsConfirmation = const []});
  final List<String> needsConfirmation;
}

final class LiveCancelledFrame extends LiveServerFrame {
  const LiveCancelledFrame();
}

final class LiveAudioEndFrame extends LiveServerFrame {
  const LiveAudioEndFrame();
}

final class LiveErrorFrame extends LiveServerFrame {
  const LiveErrorFrame({required this.error, required this.message});
  final String error;
  final String message;
}

final class LivePlaybackHintFrame extends LiveServerFrame {
  const LivePlaybackHintFrame({required this.sampleRate});
  final int sampleRate;
}

final class LivePassthroughFrame extends LiveServerFrame {
  const LivePassthroughFrame({required this.type, required this.raw});
  final String type;
  final Map<String, dynamic> raw;
}
