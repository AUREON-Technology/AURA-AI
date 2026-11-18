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
          if (messages.isEmpty) return;
          await ref.read(workspacesClientProvider).share(active.id, conversationId, shareTitle(messages), messages);
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
