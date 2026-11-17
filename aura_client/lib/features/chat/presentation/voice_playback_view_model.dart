import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'package:aura_client/l10n/l10n.dart';

import '../../../core/ai/voice/voice_client.dart';

enum VoicePlaybackStatus { idle, loading, playing }

/// Per-bubble speaker control: asks [VoiceClient] for WAV bytes, then plays them.
/// Owns the [AudioPlayer] so [VoicePlaybackButton] stays a thin view.
class VoicePlaybackViewModel {
  VoicePlaybackViewModel({
    required VoiceClient client,
    required VoidCallback onChanged,
    AudioPlayer? player,
  })  : _client = client,
        _onChanged = onChanged,
        _player = player ?? AudioPlayer() {
    _player.setPlayerMode(PlayerMode.mediaPlayer);
    _player.setReleaseMode(ReleaseMode.stop);
    _completeSub = _player.onPlayerComplete.listen((_) {
      _status = VoicePlaybackStatus.idle;
      _onChanged();
    });
  }

  final VoiceClient _client;
  final AudioPlayer _player;
  final VoidCallback _onChanged;
  StreamSubscription<void>? _completeSub;

  VoicePlaybackStatus _status = VoicePlaybackStatus.idle;
  String? _errorMessage;

  VoicePlaybackStatus get status => _status;
  String? get errorMessage => _errorMessage;

  Future<void> toggle(String text) async {
    _errorMessage = null;
    if (_status == VoicePlaybackStatus.playing) {
      await _player.stop();
      _status = VoicePlaybackStatus.idle;
      _onChanged();
      return;
    }
    if (_status == VoicePlaybackStatus.loading) return;

    _status = VoicePlaybackStatus.loading;
    _onChanged();

    final result = await _client.speak(text);
    if (!result.ok) {
      _status = VoicePlaybackStatus.idle;
      _errorMessage = result.errorMessage ?? currentL10n.chatSpeechGenerateFailed;
      _onChanged();
      return;
    }

    try {
      await _playWav(result.audio!);
      _status = VoicePlaybackStatus.playing;
      _onChanged();
    } catch (e) {
      debugPrint("[VoicePlayback] play failed: $e");
      _status = VoicePlaybackStatus.idle;
      _errorMessage = currentL10n.chatSpeechPlayFailed;
      _onChanged();
    }
  }

  Future<void> _playWav(Uint8List audio) async {
    try {
      await _player.play(BytesSource(audio, mimeType: 'audio/wav'));
    } catch (_) {
      if (kIsWeb) rethrow;
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/aura_tts_${identityHashCode(this)}.wav');
      await file.writeAsBytes(audio, flush: true);
      await _player.play(DeviceFileSource(file.path));
    }
  }

  void dispose() {
    _completeSub?.cancel();
    _player.dispose();
  }
}
