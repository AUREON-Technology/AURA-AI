import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/chat_repository.dart';
import '../chat_view_model.dart';

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

    return Container(
      padding: const EdgeInsets.all(AuraSpacing.xl),
      decoration: BoxDecoration(border: Border(top: BorderSide(color: palette.border, width: 1))),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              enabled: !locked,
              decoration: InputDecoration(
                hintText: locked
                    ? 'Waiting for Copilot...'
                    : (isMobile ? 'Ask Copilot...' : 'Ask Copilot anything...'),
              ),
              style: TextStyle(color: palette.textPrimary),
              onSubmitted: locked ? null : (_) => onSend(),
            ),
          ),
          const SizedBox(width: AuraSpacing.lg),
          Container(
            decoration: BoxDecoration(gradient: palette.brandGradient, borderRadius: BorderRadius.circular(AuraRadius.md)),
            child: IconButton(
              icon: Icon(Icons.send, color: palette.onBrand),
              onPressed: locked ? null : onSend,
            ),
          ),
        ],
      ),
    );
  }
}
