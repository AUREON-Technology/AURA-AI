import 'package:aura_client/core/locale/text_direction.dart';
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/agents_repository.dart';
import '../../data/chat_connection_error.dart';
import '../../data/chat_repository.dart';
import '../../data/turn_meta.dart';
import 'chat_image_attachments.dart';
import 'citation_chip.dart';
import 'empty_answer_retry_card.dart';
import 'engineer_confirmation_card.dart';
import 'feedback_sheet.dart';
import 'persona_badge.dart';
import 'incomplete_plan_notice.dart';
import 'step_log_panel.dart';
import 'streaming_message_skeleton.dart';
import 'tool_call_chip.dart';
import 'turn_status_row.dart';
import 'user_facing_citations.dart';
import 'voice_playback_button.dart';
import 'package:aura_client/features/workspaces/presentation/plan_limit_card.dart';
import 'package:aura_client/l10n/l10n.dart';

class MessageBubble extends ConsumerWidget {
  final Message message;
  final bool isUser;
  final String? userPrompt;
  const MessageBubble({
    super.key,
    required this.message,
    required this.isUser,
    this.userPrompt,
  });

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

  /// The fixed notices the repository stores in English (persisted and synced as the turn's
  /// text) are shown in the language on screen; anything else is the model's own answer.
  static String _displayContent(BuildContext context, String content) {
    final l10n = context.l10n;
    if (content == kChatWithheldText) return l10n.chatWithheldNotice;
    if (content == kChatBlockedText) return l10n.chatBlockedNotice;
    if (content == kChatApprovalExpiredText) return l10n.chatApprovalExpiredNotice;
    if (content == kChatFeatureUnavailableText) return l10n.chatFeatureUnavailableNotice;
    return localizedEngineerRefusal(content, l10n) ?? content;
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

    if (!isUser && isChatPlanLimitText(message.content)) {
      return Align(
        alignment: AlignmentDirectional.centerStart,
        child: Container(
          margin: const EdgeInsets.only(bottom: AuraSpacing.lg),
          constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * maxWidthFraction),
          child: PlanLimitCard(text: message.content.substring(kChatPlanLimitPrefix.length + 2)),
        ),
      );
    }

    if (!isUser && isChatConnectionErrorText(message.content)) {
      return Align(
        alignment: AlignmentDirectional.centerStart,
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
                        context.l10n.chatCloudUnavailableTitle,
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
                  context.l10n.chatCloudUnavailableBody,
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
                      label: context.l10n.retry,
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
                      label: context.l10n.chatReportIssue,
                      icon: Icons.bug_report_outlined,
                      variant: AuraButtonVariant.secondary,
                      onPressed: () async {
                        final messenger = ScaffoldMessenger.of(context);
                        final recorded = context.l10n.chatIssueRecorded;
                        await ref
                            .read(chatRepositoryProvider)
                            .reportConnectionIssue(
                              conversationId: message.conversationId,
                              messageId: message.id,
                            );
                        messenger.showSnackBar(
                          SnackBar(
                            content: Text(recorded),
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
    final citations = isUser
        ? const <String>[]
        : userFacingCitations(_decodeCitations());
    final toolCalls = isUser
        ? const <Map<String, dynamic>>[]
        : _decodeToolCalls();
    final agentId = message.agentId;
    final personaInfo = agentId != null ? personaDisplay(agentId) : null;
    final turnMeta = TurnMeta.decode(message.turnMetaJson);
    // G-14a: whether this turn's held calls are on offer right now (live, or brought back after a
    // restart). A held turn with none left was answered, or outlived the server's TTL.
    final turnKey = turnMeta.turnId ?? message.id;
    final awaitingHere = !isUser &&
        turnMeta.needsConfirmation &&
        ref.watch(pendingConfirmationProvider.select((all) => all.any((c) => c.turnId == turnKey)));
    // ADR-031: the stored text ends in the "[N images attached]" marker; the bubble shows the
    // images (or a count) instead of the marker itself.
    final userImages = isUser
        ? splitImagesNote(message.content)
        : (text: message.content, imageCount: 0);

    return Align(
      alignment: isUser ? AlignmentDirectional.centerEnd : AlignmentDirectional.centerStart,
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
                  // The square "tail" corner sits on the side the bubble is aligned to (end).
                  borderRadius: const BorderRadiusDirectional.only(
                    topStart: Radius.circular(AuraRadius.lg),
                    topEnd: Radius.circular(AuraRadius.lg),
                    bottomStart: Radius.circular(AuraRadius.lg),
                    bottomEnd: Radius.circular(0),
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (userImages.imageCount > 0) ...[
                      SentImagesView(
                        messageId: message.id,
                        imageCount: userImages.imageCount,
                        foreground: palette.onBrand,
                      ),
                      const SizedBox(height: AuraSpacing.sm),
                    ],
                    Text(
                      userImages.text,
                      style: TextStyle(
                        color: palette.onBrand,
                        fontSize: 15,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: AuraSpacing.sm),
                    Align(
                      alignment: AlignmentDirectional.bottomEnd,
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
                    if (!isStreaming &&
                        turnMeta.resolvedAgent != null &&
                        agentId != null &&
                        turnMeta.resolvedAgent != agentId) ...[
                      _ResolvedAgentMismatchBanner(
                        requested: agentId,
                        resolved: turnMeta.resolvedAgent!,
                      ),
                      const SizedBox(height: AuraSpacing.sm),
                    ],
                    if (showSkeleton)
                      const StreamingMessageSkeleton()
                    else if (!isStreaming &&
                        message.content.trim().isEmpty &&
                        turnMeta.needsConfirmation)
                      // The card is below; if it is gone without an answer, the approval expired
                      // (no Retry: it would resend the message as a new turn).
                      awaitingHere
                          ? _AwaitingConfirmationHint(text: context.l10n.chatAwaitingConfirmation)
                          : turnMeta.confirmationResolved
                              ? const SizedBox.shrink()
                              : _AwaitingConfirmationHint(text: context.l10n.chatApprovalExpiredNotice)
                    else if (!isStreaming && message.content.trim().isEmpty)
                      EmptyAnswerRetryCard(
                        onRetry: () {
                          final repo = ref.read(chatRepositoryProvider);
                          unawaited(
                            repo.retryLastFailedTurn(
                              conversationId: message.conversationId,
                              errorMessageId: message.id,
                            ),
                          );
                        },
                      )
                    else
                      // The message reads in its own direction (first strong letter outside
                      // code); its fenced code blocks stay left-to-right (text_direction.dart).
                      Directionality(
                        textDirection: directionOfText(message.content) ?? Directionality.of(context),
                        child: _MarkdownWithLtrCode(
                          data: _displayContent(context, message.content),
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
                      ),
                    if (toolCalls.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      ToolCallChips(toolCalls: toolCalls),
                    ],
                    if (turnMeta.steps.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      StepLogPanel(steps: turnMeta.steps, live: isStreaming),
                    ],
                    // Only once the turn has finished: mid-stream, a step that
                    // has not completed yet is simply a step in progress.
                    if (!isStreaming) IncompletePlanNotice(plan: turnMeta.plan),
                    if (!isStreaming) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      EngineerConfirmationCard(
                        turnId: turnMeta.turnId ?? message.id,
                      ),
                    ],
                    if (citations.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      CitationChips(citations: citations),
                    ],
                    if (!isStreaming)
                      TurnStatusRow(meta: turnMeta, hasCitations: citations.isNotEmpty),
                    const SizedBox(height: AuraSpacing.sm),
                    Row(
                      children: [
                        if (turnMeta.turnId != null && !isStreaming)
                          IconButton(
                            tooltip: context.l10n.chatRateAnswer,
                            visualDensity: VisualDensity.compact,
                            icon: Icon(Icons.thumb_up_outlined, size: 18, color: palette.textSecondary),
                            onPressed: () => showFeedbackSheet(
                              context: context,
                              ref: ref,
                              turnId: turnMeta.turnId!,
                              prompt: userPrompt,
                              response: message.content,
                            ),
                          ),
                        if (!isStreaming && message.content.trim().isNotEmpty)
                          VoicePlaybackButton(text: message.content),
                        const Spacer(),
                        Text(
                          formattedTime,
                          style: TextStyle(
                            fontSize: 10,
                            color: palette.textSecondary,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
      ),
    );
  }
}

/// Shown when the backend answered with a different persona than the one requested --
/// `AgentRegistry.resolve()` silently falls back to the orchestrator when the caller lacks a
/// persona's required capability, with no error anywhere else in the response. The picker
/// (`persona_picker.dart`) already tries to prevent selecting an unusable persona in the first
/// place; this is the belt-and-suspenders signal for when that guard is somehow bypassed (a
/// stale cached agent list, for instance).
class _ResolvedAgentMismatchBanner extends StatelessWidget {
  const _ResolvedAgentMismatchBanner({required this.requested, required this.resolved});

  final String requested;
  final String resolved;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (requestedLabel, _) = personaDisplay(requested);
    final (resolvedLabel, _) = personaDisplay(resolved);
    return Container(
      padding: const EdgeInsets.all(AuraSpacing.sm),
      decoration: BoxDecoration(
        color: palette.warning.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AuraRadius.sm),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.info_outline, size: 16, color: palette.warning),
          const SizedBox(width: AuraSpacing.xs),
          Expanded(
            child: Text(
              context.l10n.chatPersonaFallback(resolvedLabel, requestedLabel),
              style: TextStyle(color: palette.textSecondary, fontSize: 12, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

/// Shown instead of [EmptyAnswerRetryCard] when a turn ended with no text because it is
/// waiting on the user to confirm a proposed tool action, not because it failed outright --
/// retrying would just resend the same unconfirmed request.
class _AwaitingConfirmationHint extends StatelessWidget {
  const _AwaitingConfirmationHint({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.pending_actions_outlined, size: 16, color: palette.textSecondary),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            text,
            style: TextStyle(color: palette.textSecondary, fontSize: 13, height: 1.4),
          ),
        ),
      ],
    );
  }
}


/// Markdown whose closed fenced code blocks are laid out left-to-right whatever the message's
/// direction: in a right-to-left paragraph a line like `foo();` would show its trailing
/// punctuation on the wrong side (docs/plans/arabic-language_plan.md, stage 2). Prose between
/// the blocks renders through [MarkdownBody] as before; an unclosed fence (mid-stream) stays in
/// the prose until it closes.
class _MarkdownWithLtrCode extends StatelessWidget {
  const _MarkdownWithLtrCode({required this.data, required this.styleSheet});

  final String data;
  final MarkdownStyleSheet styleSheet;

  static final _fence = RegExp(r'```[^\n]*\n([\s\S]*?)```');

  @override
  Widget build(BuildContext context) {
    final matches = _fence.allMatches(data).toList();
    if (matches.isEmpty) return MarkdownBody(data: data, styleSheet: styleSheet);

    final children = <Widget>[];
    var cursor = 0;
    void prose(String text) {
      if (text.trim().isEmpty) return;
      children.add(MarkdownBody(data: text, styleSheet: styleSheet));
    }

    for (final match in matches) {
      prose(data.substring(cursor, match.start));
      children.add(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: AuraSpacing.xs),
          child: LtrContent(
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(AuraSpacing.sm),
              clipBehavior: Clip.hardEdge,
              decoration: styleSheet.codeblockDecoration,
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Text(match.group(1)!.trimRight(), style: styleSheet.code),
              ),
            ),
          ),
        ),
      );
      cursor = match.end;
    }
    prose(data.substring(cursor));
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children);
  }
}
