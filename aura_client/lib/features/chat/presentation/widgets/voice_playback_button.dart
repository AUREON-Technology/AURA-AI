import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../../../core/ai/voice/voice_client.dart';
import '../voice_playback_view_model.dart';

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
  late final VoicePlaybackViewModel _viewModel;

  @override
  void initState() {
    super.initState();
    _viewModel = VoicePlaybackViewModel(
      client: ref.read(voiceClientProvider),
      onChanged: () {
        if (mounted) setState(() {});
      },
    );
  }

  @override
  void dispose() {
    _viewModel.dispose();
    super.dispose();
  }

  Future<void> _onPressed() async {
    await _viewModel.toggle(widget.text);
    if (!mounted) return;
    final error = _viewModel.errorMessage;
    if (error != null) {
      AuraSnackbar.show(context, message: error, kind: AuraSnackbarKind.error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final status = _viewModel.status;

    if (status == VoicePlaybackStatus.loading) {
      return const Padding(
        padding: EdgeInsets.all(8),
        child: SizedBox(
          width: 16,
          height: 16,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }

    final playing = status == VoicePlaybackStatus.playing;
    return IconButton(
      tooltip: playing ? 'Stop' : 'Listen to this reply',
      visualDensity: VisualDensity.compact,
      icon: Icon(
        playing ? Icons.stop_circle_outlined : Icons.volume_up_outlined,
        size: 18,
        color: palette.textSecondary,
      ),
      onPressed: _onPressed,
    );
  }
}
