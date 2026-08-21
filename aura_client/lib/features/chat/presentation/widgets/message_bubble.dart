import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/agents_repository.dart';
import '../../data/chat_connection_error.dart';
import '../../data/chat_repository.dart';
import 'citation_chip.dart';
import 'persona_badge.dart';
import 'streaming_message_skeleton.dart';
import 'tool_call_chip.dart';

class MessageBubble extends ConsumerWidget {
  final Message message;
  final bool isUser;
  const MessageBubble({super.key, required this.message, required this.isUser});

  List<String> _decodeCitations() {
    final raw = message.citationsJson;
    if (raw == null || raw.isEmpty) return const [];
    try {
      return (jsonDecode(raw) as List<dynamic>)
          .map((c) => c.toString())
          .toList();
    } catch (_) {
      return const [];
    }
  }

  List<Map<String, dynamic>> _decodeToolCalls() {
    final raw = message.toolCallsJson;
    if (raw == null || raw.isEmpty) return const [];
    try {
      return (jsonDecode(raw) as List<dynamic>).cast<Map<String, dynamic>>();
    } catch (_) {
      return const [];
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final formattedTime = DateFormat('jm').format(message.createdAt);
    final maxWidthFraction = MediaQuery.of(context).size.width < 768
        ? 0.85
        : 0.65;

    if (!isUser && isChatConnectionErrorText(message.content)) {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.only(bottom: AuraSpacing.lg),
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * maxWidthFraction,
          ),
          child: AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.cloud_off, color: palette.danger, size: 20),
                    const SizedBox(width: AuraSpacing.sm),
                    Expanded(
                      child: Text(
                        'Cloud AI is currently unavailable',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          color: palette.textPrimary,
                          fontSize: 14,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AuraSpacing.sm),
                Text(
                  'AURA could not connect to the cloud assistant. You can retry or report this issue.',
                  style: TextStyle(
                    color: palette.textSecondary,
                    fontSize: 13,
                    height: 1.4,
                  ),
                ),
                const SizedBox(height: AuraSpacing.md),
                Wrap(
                  spacing: AuraSpacing.sm,
                  runSpacing: AuraSpacing.sm,
                  children: [
                    AuraButton(
                      label: 'Retry',
                      icon: Icons.refresh,
                      onPressed: () {
                        final repo = ref.read(chatRepositoryProvider);
                        unawaited(
                          repo.retryLastFailedTurn(
                            conversationId: message.conversationId,
                            errorMessageId: message.id,
                          ),
                        );
                      },
                    ),
                    AuraButton(
                      label: 'Report Issue',
                      icon: Icons.bug_report_outlined,
                      variant: AuraButtonVariant.secondary,
                      onPressed: () async {
                        final messenger = ScaffoldMessenger.of(context);
                        await ref
                            .read(chatRepositoryProvider)
                            .reportConnectionIssue(
                              conversationId: message.conversationId,
                              messageId: message.id,
                            );
                        messenger.showSnackBar(
                          SnackBar(
                            content: const Text(
                              'Issue recorded in local telemetry.',
                            ),
                            backgroundColor: palette.success,
                          ),
                        );
                      },
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      );
    }

    final isStreaming =
        !isUser && ref.watch(streamingMessageIdProvider) == message.id;
    final showSkeleton = isStreaming && message.content.isEmpty;
    final citations = isUser ? const <String>[] : _decodeCitations();
    final toolCalls = isUser
        ? const <Map<String, dynamic>>[]
        : _decodeToolCalls();
    final agentId = message.agentId;
    final personaInfo = agentId != null ? personaDisplay(agentId) : null;

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: AuraSpacing.lg),
        constraints: BoxConstraints(
          maxWidth:
              MediaQuery.of(context).size.width *
              (isUser ? 0.75 : maxWidthFraction),
        ),
        child: isUser
            ? Container(
                padding: const EdgeInsets.all(AuraSpacing.lg),
                decoration: BoxDecoration(
                  gradient: palette.brandGradient,
                  borderRadius: const BorderRadius.only(
                    topLeft: Radius.circular(AuraRadius.lg),
                    topRight: Radius.circular(AuraRadius.lg),
                    bottomLeft: Radius.circular(AuraRadius.lg),
                    bottomRight: Radius.circular(0),
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      message.content,
                      style: TextStyle(
                        color: palette.onBrand,
                        fontSize: 15,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: AuraSpacing.sm),
                    Align(
                      alignment: Alignment.bottomRight,
                      child: Text(
                        formattedTime,
                        style: TextStyle(
                          fontSize: 10,
                          color: palette.onBrand.withValues(alpha: 0.7),
                        ),
                      ),
                    ),
                  ],
                ),
              )
            : AuraCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (personaInfo != null) ...[
                      PersonaBadge(label: personaInfo.$1, icon: personaInfo.$2),
                      const SizedBox(height: AuraSpacing.sm),
                    ],
                    if (showSkeleton)
                      const StreamingMessageSkeleton()
                    else
                      MarkdownBody(
                        data: message.content,
                        styleSheet: MarkdownStyleSheet(
                          p: TextStyle(
                            color: palette.textPrimary,
                            fontSize: 15,
                            height: 1.4,
                          ),
                          code: TextStyle(
                            backgroundColor: palette.surfaceVariant,
                            color: palette.warning,
                            fontFamily: 'Courier',
                            fontSize: 13,
                          ),
                          codeblockDecoration: BoxDecoration(
                            color: palette.background,
                            borderRadius: BorderRadius.circular(AuraRadius.sm),
                            border: Border.all(color: palette.border),
                          ),
                        ),
                      ),
                    if (toolCalls.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      ToolCallChips(toolCalls: toolCalls),
                    ],
                    if (citations.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      CitationChips(citations: citations),
                    ],
                    const SizedBox(height: AuraSpacing.sm),
                    Align(
                      alignment: Alignment.bottomRight,
                      child: Text(
                        formattedTime,
                        style: TextStyle(
                          fontSize: 10,
                          color: palette.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
      ),
    );
  }
}
