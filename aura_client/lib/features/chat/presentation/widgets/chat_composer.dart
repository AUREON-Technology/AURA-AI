import 'package:aura_client/core/locale/text_direction.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/agent_config_repository.dart';
import '../../data/agents_repository.dart';
import '../../data/chat_repository.dart';
import '../chat_view_model.dart';
import 'chat_image_attachments.dart';
import 'voice_recorder_button.dart';
import 'package:aura_client/l10n/l10n.dart';

/// English tooltip and screen-reader label of the composer's Stop button.
///
/// The widget reads `chatStopGenerating` from `app_en.arb`; this constant mirrors that
/// value so tests can find the button by tooltip.
const kStopGeneratingLabel = 'Stop generating';

class ChatComposer extends ConsumerWidget {
  final TextEditingController controller;
  final bool isMobile;
  final VoidCallback onSend;

  const ChatComposer({
    super.key,
    required this.controller,
    required this.isMobile,
    required this.onSend,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final chat = ref.watch(chatViewModelProvider);
    final isStreaming = ref.watch(streamingMessageIdProvider) != null;
    final locked = chat.composerLocked(isStreaming: isStreaming);
    final (personaLabel, _) = personaDisplay(ref.watch(activeAgentIdProvider));
    final l10n = context.l10n;

    return Container(
      padding: const EdgeInsets.all(AuraSpacing.xl),
      decoration: BoxDecoration(border: Border(top: BorderSide(color: palette.border, width: 1))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: AuraSpacing.sm,
            children: [
              FilterChip(
                label: Text(l10n.chatToggleKnowledge),
                selected: chat.useRag,
                onSelected: locked
                    ? null
                    : (v) => ref.read(chatViewModelProvider.notifier).setUseRag(v),
              ),
              FilterChip(
                label: Text(l10n.chatToggleMemory),
                selected: chat.useMemory,
                onSelected: locked
                    ? null
                    : (v) => ref.read(chatViewModelProvider.notifier).setUseMemory(v),
              ),
              FilterChip(
                label: Text(l10n.chatToggleTools),
                selected: chat.useTools,
                onSelected: locked
                    ? null
                    : (v) => ref.read(chatViewModelProvider.notifier).setUseTools(v),
              ),
            ],
          ),
          PendingImagesStrip(enabled: !locked),
          const SizedBox(height: AuraSpacing.md),
          Row(
            children: [
              AttachImageButton(enabled: !locked),
              Expanded(
                // Arabic typed into an English app reads right-to-left, and English or code typed
                // into an Arabic app left-to-right: the field follows its own first strong letter.
                child: ValueListenableBuilder<TextEditingValue>(
                  valueListenable: controller,
                  builder: (context, value, _) => TextField(
                  controller: controller,
                  textDirection: directionOfText(value.text) ?? Directionality.of(context),
                  enabled: !locked,
                  decoration: InputDecoration(
                    hintText: locked
                        ? l10n.chatComposerWaiting(personaLabel)
                        : (isMobile
                            ? l10n.chatComposerHintShort(personaLabel)
                            : l10n.chatComposerHint(personaLabel)),
                  ),
                  style: TextStyle(color: palette.textPrimary),
                  onSubmitted: locked ? null : (_) => onSend(),
                ),
                ),
              ),
              VoiceRecorderButton(
                enabled: !locked,
                onTranscribed: (text) {
                  final existing = controller.text;
                  controller.text = existing.isEmpty ? text : '$existing $text';
                  controller.selection = TextSelection.collapsed(offset: controller.text.length);
                },
              ),
              const SizedBox(width: AuraSpacing.lg),
              Container(
                decoration: BoxDecoration(gradient: palette.brandGradient, borderRadius: BorderRadius.circular(AuraRadius.md)),
                // While a response streams, Send becomes Stop: it unlocks the chat at once and
                // keeps whatever text already arrived (F-31).
                child: isStreaming
                    ? IconButton(
                        key: const ValueKey('chat-stop-button'),
                        tooltip: l10n.chatStopGenerating,
                        icon: Icon(Icons.stop_rounded, color: palette.onBrand),
                        onPressed: () => ref.read(chatRepositoryProvider).stopStreaming(),
                      )
                    : IconButton(
                        tooltip: l10n.chatSend,
                        icon: Icon(Icons.send, color: palette.onBrand),
                        onPressed: locked ? null : onSend,
                      ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
