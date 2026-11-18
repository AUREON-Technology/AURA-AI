import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../../../core/ai/voice/voice_client.dart';
import '../../../../core/ai/voice/voice_language.dart';
import 'package:aura_client/l10n/l10n.dart';

enum _VoiceState { idle, recording, transcribing }

/// Mic button next to the chat composer's send button (Phase F2). Records a short voice note,
/// sends it to `POST /api/v3/voice/transcribe`, and hands the transcript back to the caller to
/// drop into the composer -- it never sends anything itself, so the user still reviews and hits
/// send like any typed message.
class VoiceRecorderButton extends ConsumerStatefulWidget {
  final bool enabled;
  final ValueChanged<String> onTranscribed;

  const VoiceRecorderButton({
    super.key,
    required this.enabled,
    required this.onTranscribed,
  });

  @override
  ConsumerState<VoiceRecorderButton> createState() => _VoiceRecorderButtonState();
}

class _VoiceRecorderButtonState extends ConsumerState<VoiceRecorderButton> {
  static const _maxRecordingTime = Duration(seconds: 60);

  final _recorder = AudioRecorder();
  _VoiceState _state = _VoiceState.idle;
  String? _path;
  Timer? _autoStopTimer;

  @override
  void dispose() {
    _autoStopTimer?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    try {
      if (!await _recorder.hasPermission()) {
        if (mounted) {
          AuraSnackbar.show(
            context,
            message: context.l10n.voiceMicPermission,
            kind: AuraSnackbarKind.error,
          );
        }
        return;
      }
      final dir = await getTemporaryDirectory();
      final path =
          '${dir.path}/aura_voice_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _recorder.start(const RecordConfig(encoder: AudioEncoder.wav), path: path);
      if (!mounted) return;
      setState(() {
        _state = _VoiceState.recording;
        _path = path;
      });
      _autoStopTimer = Timer(_maxRecordingTime, _stopAndTranscribe);
    } catch (_) {
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.voiceRecordingUnavailable,
          kind: AuraSnackbarKind.error,
        );
      }
    }
  }

  Future<void> _stopAndTranscribe() async {
    _autoStopTimer?.cancel();
    if (_state != _VoiceState.recording) return;
    setState(() => _state = _VoiceState.transcribing);

    try {
      await _recorder.stop();
      final path = _path;
      if (path == null) {
        _reset();
        return;
      }
      final file = File(path);
      final bytes = await file.readAsBytes();
      try {
        await file.delete();
      } catch (_) {
        // Best-effort cleanup of the scratch recording; a leftover temp file is harmless.
      }

      final text = await ref
          .read(voiceClientProvider)
          .transcribe(bytes, language: ref.read(voiceLanguageProvider).code);
      if (!mounted) return;
      if (text == null) {
        AuraSnackbar.show(
          context,
          message: context.l10n.voiceTranscribeFailed,
          kind: AuraSnackbarKind.error,
        );
      } else {
        widget.onTranscribed(text);
      }
    } catch (_) {
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.voiceReadFailed,
          kind: AuraSnackbarKind.error,
        );
      }
    } finally {
      _reset();
    }
  }

  void _reset() {
    if (!mounted) return;
    setState(() {
      _state = _VoiceState.idle;
      _path = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final disabled = !widget.enabled && _state == _VoiceState.idle;

    if (_state == _VoiceState.transcribing) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }

    final recording = _state == _VoiceState.recording;
    return IconButton(
      tooltip: recording ? context.l10n.voiceStopRecording : context.l10n.voiceRecordNote,
      icon: Icon(
        recording ? Icons.stop_circle : Icons.mic_none,
        color: recording ? palette.danger : palette.textSecondary,
      ),
      onPressed: disabled ? null : (recording ? _stopAndTranscribe : _start),
    );
  }
}
