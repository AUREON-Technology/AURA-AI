import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/network/aura_failure_view.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../../auth/presentation/auth_provider.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';
import 'workspace_providers.dart';

final sharedChatsProvider = FutureProvider.autoDispose.family<List<SharedChatSummary>, String>(
  (ref, id) => ref.read(workspacesClientProvider).shared(id),
);

final sharedChatProvider = FutureProvider.autoDispose.family<SharedChat, (String, String)>(
  (ref, key) => ref.read(workspacesClientProvider).sharedChat(key.$1, key.$2),
);

/// Chats members shared with the active team (SaaS P1-F2, spec §5.2).
class SharedChatsPage extends ConsumerWidget {
  const SharedChatsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final active = ref.watch(activeWorkspaceProvider);
    return Scaffold(
      appBar: AppBar(title: Text(l10n.teamChatsTitle)),
      body: active == null || !active.isTeam
          ? Center(child: Text(l10n.teamChatsEmpty))
          : ref.watch(sharedChatsProvider(active.id)).when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (error, _) => AuraFailureView(failure: AuraFailure.from(error),
                    onRetry: () => ref.invalidate(sharedChatsProvider(active.id))),
                data: (chats) => chats.isEmpty
                    ? Center(child: Text(l10n.teamChatsEmpty, key: const Key('team-chats-empty')))
                    : ListView(padding: const EdgeInsets.all(AuraSpacing.lg), children: [
                        for (final c in chats)
                          AuraListTile(
                            key: Key('shared-${c.id}'),
                            icon: Icons.forum_outlined,
                            title: c.title,
                            subtitle: l10n.teamChatsSharedBy(c.sharedBy),
                            onTap: () => context.push('/team-chats/${c.id}'),
                          ),
                      ]),
              ),
    );
  }
}

class SharedChatView extends ConsumerWidget {
  const SharedChatView({super.key, required this.sharedId});

  final String sharedId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final active = ref.watch(activeWorkspaceProvider);
    if (active == null) return const Scaffold(body: SizedBox.shrink());
    final me = ref.watch(authProvider).value?.id;
    final chat = ref.watch(sharedChatProvider((active.id, sharedId)));
    final palette = AuraPalette.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(chat.value?.summary.title ?? l10n.teamChatsTitle),
        actions: [
          if (chat.value != null && (chat.value!.summary.sharedBy == me || active.canManage))
            TextButton(
              key: const Key('unshare'),
              onPressed: () async {
                try {
                  await ref.read(workspacesClientProvider).unshare(active.id, sharedId);
                  ref.invalidate(sharedChatsProvider(active.id));
                  if (context.mounted) context.pop();
                } catch (error) {
                  if (context.mounted) showFailure(context, error);
                }
              },
              child: Text(l10n.teamChatsUnshare),
            ),
        ],
      ),
      body: chat.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => AuraFailureView(failure: AuraFailure.from(error)),
        data: (c) => ListView(padding: const EdgeInsets.all(AuraSpacing.lg), children: [
          for (final m in c.messages)
            Align(
              alignment: m.role == 'user' ? AlignmentDirectional.centerEnd : AlignmentDirectional.centerStart,
              child: Container(
                margin: const EdgeInsets.only(bottom: AuraSpacing.sm),
                padding: const EdgeInsets.all(AuraSpacing.md),
                constraints: const BoxConstraints(maxWidth: 560),
                decoration: BoxDecoration(
                  color: m.role == 'user' ? palette.brand.withValues(alpha: 0.12) : palette.surface,
                  borderRadius: BorderRadius.circular(AuraRadius.md),
                  border: Border.all(color: palette.border),
                ),
                child: SelectableText(m.text),
              ),
            ),
        ]),
      ),
    );
  }
}
