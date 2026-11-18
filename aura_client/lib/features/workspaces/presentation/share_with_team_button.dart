import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_api_error.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../../chat/data/chat_connection_error.dart';
import '../../chat/data/chat_repository.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';
import 'workspace_providers.dart';

/// The messages of one local chat worth sharing: user and assistant text, without the rows the
/// app writes for failures.
List<SharedChatMessage> shareableMessages(Iterable<({String role, String text})> rows) => [
      for (final r in rows)
        if ((r.role == 'user' || r.role == 'assistant') &&
            r.text.trim().isNotEmpty &&
            !isChatConnectionErrorText(r.text) &&
            !isChatPlanLimitText(r.text))
          SharedChatMessage(role: r.role, text: r.text),
    ];

/// The server's limits on one shared chat (`aura_core/domain/sharing.py`: `MAX_SHARED_MESSAGES`,
/// `MAX_SHARED_CHARS`); it refuses a longer one as a whole (422).
const kMaxSharedMessages = 200;
const kMaxSharedChars = 200000;

/// The most recent of [messages] that fit both server limits, in their original order, and how
/// many older ones were left out (INT-113). Characters are counted as the server counts them, in
/// code points. A latest message longer than the whole limit is cut to it, so a long chat is
/// never shared as nothing.
({List<SharedChatMessage> messages, int omitted}) fitShareLimits(
  List<SharedChatMessage> messages, {
  int maxMessages = kMaxSharedMessages,
  int maxChars = kMaxSharedChars,
}) {
  var start = messages.length;
  var chars = 0;
  while (start > 0 && messages.length - start < maxMessages) {
    final length = messages[start - 1].text.runes.length;
    if (chars + length > maxChars) break;
    chars += length;
    start--;
  }
  if (start == messages.length && messages.isNotEmpty) {
    final latest = messages.last;
    final cut = String.fromCharCodes(latest.text.runes.take(maxChars));
    return (messages: [SharedChatMessage(role: latest.role, text: cut)], omitted: messages.length - 1);
  }
  return (messages: messages.sublist(start), omitted: start);
}

String shareTitle(List<SharedChatMessage> messages) {
  final first = messages.where((m) => m.role == 'user').firstOrNull?.text ?? '';
  final line = first.split('\n').first.trim();
  return line.length > 60 ? '${line.substring(0, 60)}…' : line;
}

/// "Share with team" in the chat header: only while a team is active. Sends a copy of this
/// chat's messages from the local database; the person's own chat stays private.
class ShareWithTeamButton extends ConsumerWidget {
  const ShareWithTeamButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeWorkspaceProvider);
    if (active == null || !active.isTeam) return const SizedBox.shrink();
    final l10n = context.l10n;
    return IconButton(
      key: const Key('share-with-team'),
      icon: const Icon(Icons.ios_share),
      tooltip: l10n.shareWithTeam,
      onPressed: () async {
        final conversationId = ref.read(activeConversationIdProvider);
        try {
          final rows = await ref.read(chatRepositoryProvider).watchMessages(conversationId).first;
          final messages = shareableMessages(rows.map((m) => (role: m.role, text: m.content)));
          if (!context.mounted) return;
          if (messages.isEmpty) {
            AuraSnackbar.show(context, message: l10n.shareNothing);
            return;
          }
          // INT-113: the latest messages that fit the server's limits; the confirmation says
          // how many older ones stay out.
          final fit = fitShareLimits(messages);
          final body = fit.omitted == 0
              ? l10n.shareConfirm(active.name)
              : '${l10n.shareConfirm(active.name)}\n\n'
                  '${l10n.shareTruncatedOlder(fit.messages.length, fit.omitted)}';
          // A copy goes to everyone in the team: say which team, and ask (F2 review #9).
          if (!await confirm(context, title: l10n.shareWithTeam, body: body, action: l10n.shareWithTeam)) {
            return;
          }
          await ref
              .read(workspacesClientProvider)
              .share(active.id, conversationId, shareTitle(messages), fit.messages);
          if (context.mounted) {
            AuraSnackbar.show(context, message: l10n.sharedWithTeam(active.name), kind: AuraSnackbarKind.success);
          }
        } on AuraApiError catch (e) {
          if (!context.mounted) return;
          if (e.statusCode == 409) {
            AuraSnackbar.show(context, message: l10n.teamChatsTooMany, kind: AuraSnackbarKind.error);
          } else {
            showFailure(context, e);
          }
        } catch (e) {
          if (context.mounted) showFailure(context, e);
        }
      },
    );
  }
}
