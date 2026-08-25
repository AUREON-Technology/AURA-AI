import 'dart:async';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../../../core/ai/voice/voice_client.dart';

enum _PlaybackState { idle, loading, playing }

/// Speaker icon on an assistant message (Phase F2): fetches `POST /api/v3/voice/speak` on
/// first tap and plays the WAV response back; a second tap stops playback. Self-contained --
/// [message_bubble.dart] only needs to hand it the reply text.
class VoicePlaybackButton extends ConsumerStatefulWidget {
  final String text;

  const VoicePlaybackButton({super.key, required this.text});

  @override
  ConsumerState<VoicePlaybackButton> createState() => _VoicePlaybackButtonState();
}

class _VoicePlaybackButtonState extends ConsumerState<VoicePlaybackButton> {
  final _player = AudioPlayer();
  _PlaybackState _state = _PlaybackState.idle;
  StreamSubscription<void>? _completeSub;

  @override
  void initState() {
    super.initState();
    _completeSub = _player.onPlayerComplete.listen((_) {
      if (mounted) setState(() => _state = _PlaybackState.idle);
    });
  }

  @override
  void dispose() {
    _completeSub?.cancel();
    _player.dispose();
    super.dispose();
  }

  Future<void> _toggle() async {
    if (_state == _PlaybackState.playing) {
      await _player.stop();
      if (mounted) setState(() => _state = _PlaybackState.idle);
      return;
    }
    if (_state == _PlaybackState.loading) return;

    setState(() => _state = _PlaybackState.loading);
    final audio = await ref.read(voiceClientProvider).speak(widget.text);
    if (!mounted) return;
    if (audio == null) {
      setState(() => _state = _PlaybackState.idle);
      AuraSnackbar.show(
        context,
        message: 'Could not generate speech for this reply.',
        kind: AuraSnackbarKind.error,
      );
      return;
    }
    setState(() => _state = _PlaybackState.playing);
    await _player.play(BytesSource(audio));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    if (_state == _PlaybackState.loading) {
      return const Padding(
        padding: EdgeInsets.all(8),
        child: SizedBox(
          width: 16,
          height: 16,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }

    final playing = _state == _PlaybackState.playing;
    return IconButton(
      tooltip: playing ? 'Stop' : 'Listen to this reply',
      visualDensity: VisualDensity.compact,
      icon: Icon(
        playing ? Icons.stop_circle_outlined : Icons.volume_up_outlined,
        size: 18,
        color: palette.textSecondary,
      ),
      onPressed: _toggle,
    );
  }
}
