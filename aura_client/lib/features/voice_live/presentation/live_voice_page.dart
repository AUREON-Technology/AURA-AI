import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_confirmation_card.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_view_model.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/aura_voice_orb.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/aura_voice_waveform.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/live_transcript_panel.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/live_voice_controls.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/voice_language_toggle.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Full-screen live voice surface (outside the shell nav bar).
class LiveVoicePage extends ConsumerStatefulWidget {
  const LiveVoicePage({super.key});

  @override
  ConsumerState<LiveVoicePage> createState() => _LiveVoicePageState();
}

class _LiveVoicePageState extends ConsumerState<LiveVoicePage>
    with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(liveVoiceViewModelProvider.notifier).startSession();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) {
      ref.read(liveVoiceViewModelProvider.notifier).onAppPaused();
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final voice = ref.watch(liveVoiceViewModelProvider);
    final amp = voice.phase == 'speaking'
        ? voice.playbackAmplitude
        : voice.micAmplitude;

    return Scaffold(
      backgroundColor: palette.background,
      body: SafeArea(
        child: Column(
          children: [
            Row(
              children: [
                IconButton(
                  tooltip: context.l10n.liveVoiceEnd,
                  icon: Icon(Icons.close, color: palette.textPrimary),
                  onPressed: () async {
                    await ref.read(liveVoiceViewModelProvider.notifier).endSession();
                    if (context.mounted) context.pop();
                  },
                ),
                const Spacer(),
                Padding(
                  padding: const EdgeInsetsDirectional.only(end: AuraSpacing.md),
                  child: VoiceLanguageToggle(
                    value: ref.watch(voiceLanguageProvider),
                    onChanged: (language) => ref
                        .read(liveVoiceViewModelProvider.notifier)
                        .setLanguage(language),
                  ),
                ),
              ],
            ),
            Expanded(
              child: Stack(
                alignment: Alignment.center,
                children: [
                  AuraVoiceWaveform(amplitude: amp, phase: voice.phase),
                  AuraVoiceOrb(phase: voice.phase, amplitude: amp),
                ],
              ),
            ),
            if (voice.error != null)
              Padding(
                padding: const EdgeInsets.all(AuraSpacing.md),
                child: Text(
                  voice.error!,
                  style: TextStyle(color: palette.danger),
                  textAlign: TextAlign.center,
                ),
              ),
            if (voice.transcriptVisible)
              SizedBox(
                height: 160,
                child: LiveTranscriptPanel(
                  lines: voice.lines,
                  draft: voice.assistantDraft,
                ),
              ),
            EngineerConfirmationCard(
              calls: ref.watch(liveVoicePendingProvider),
              onConfirm: () =>
                  ref.read(liveVoiceViewModelProvider.notifier).confirmPending(),
              onCancel: () =>
                  ref.read(liveVoiceViewModelProvider.notifier).rejectPending(),
            ),
            LiveVoiceControls(
              phase: voice.phase,
              transcriptVisible: voice.transcriptVisible,
              hasPendingConfirmation: false,
              onToggleTranscript: () =>
                  ref.read(liveVoiceViewModelProvider.notifier).toggleTranscript(),
              onEnd: () async {
                await ref.read(liveVoiceViewModelProvider.notifier).endSession();
                if (context.mounted) context.pop();
              },
              onBargeIn: () =>
                  ref.read(liveVoiceViewModelProvider.notifier).bargeIn(),
            ),
            const SizedBox(height: AuraSpacing.lg),
          ],
        ),
      ),
    );
  }
}
