import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';

import '../chat_view_model.dart';
import 'persona_picker.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

class ChatSidebar extends ConsumerWidget {
  final String activeSessionId;
  final AsyncValue<List<String>> sessionsAsync;
  final bool isMobile;

  const ChatSidebar({
    super.key,
    required this.activeSessionId,
    required this.sessionsAsync,
    required this.isMobile,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final chat = ref.watch(chatViewModelProvider);
    final vm = ref.read(chatViewModelProvider.notifier);

    return Container(
      width: 260,
      color: palette.background,
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.lg,
        vertical: AuraSpacing.xl,
      ),
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          AuraButton(
            label: 'New Session',
            icon: Icons.add,
            expand: true,
            onPressed: () {
              vm.startNewSession();
              if (isMobile) Navigator.pop(context);
            },
          ),
          const SizedBox(height: AuraSpacing.xl),
          Text(
            'CHAT SESSIONS',
            style: TextStyle(
              color: palette.textSecondary,
              fontSize: 11,
              fontWeight: FontWeight.bold,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          sessionsAsync.when(
            data: (sessions) {
              final allSessions = sessions.contains('default_session')
                  ? sessions
                  : ['default_session', ...sessions];

              return ListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: allSessions.length,
                itemBuilder: (context, index) {
                  final session = allSessions[index];
                  final isActive = session == activeSessionId;
                  final label = session == 'default_session'
                      ? 'Default Workspace'
                      : 'Session ${session.split('_').last.substring(0, 5)}';

                  return Padding(
                    padding: const EdgeInsets.only(bottom: 6.0),
                    child: AuraSelectTile(
                      icon: Icons.chat_bubble_outline,
                      title: label,
                      selected: isActive,
                      trailing: isActive && session != 'default_session'
                          ? IconButton(
                              icon: Icon(
                                Icons.delete_outline,
                                size: 16,
                                color: palette.textDisabled,
                              ),
                              tooltip: 'Delete conversation',
                              onPressed: () {
                                // Off the session at once (the button only
                                // shows on the active one); the delete itself
                                // waits out the Undo window (F-34).
                                vm.startNewSession();
                                deleteWithUndo(
                                  context,
                                  ref,
                                  key: chatSessionUndoKey(session),
                                  message: 'Conversation deleted',
                                  commit: () => vm.deleteSession(session),
                                  onUndo: () => vm.selectSession(session),
                                );
                              },
                            )
                          : null,
                      onTap: () {
                        vm.selectSession(session);
                        if (isMobile) Navigator.pop(context);
                      },
                    ),
                  );
                },
              );
            },
            loading: () => const Center(
              child: Padding(
                padding: EdgeInsets.all(8.0),
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
            error: (err, _) => Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8.0),
              child: AuraFailureView(failure: AuraFailure.from(err), compact: true),
            ),
          ),
          if (isMobile) ...[
            Divider(color: palette.border, height: 32),
            Text(
              'ACTIVE PERSONA',
              style: TextStyle(
                color: palette.textSecondary,
                fontSize: 11,
                fontWeight: FontWeight.bold,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: AuraSpacing.md),
            const PersonaChip(),
            const SizedBox(height: AuraSpacing.md),
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Use Knowledge (RAG)',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 12,
                      color: palette.textSecondary,
                    ),
                  ),
                ),
                Switch(
                  value: chat.useRag,
                  activeTrackColor: palette.brand,
                  onChanged: vm.setUseRag,
                ),
              ],
            ),
          ],
          Divider(color: palette.border, height: 32),
          Text(
            'AI ROUTING',
            style: TextStyle(
              color: palette.textSecondary,
              fontSize: 11,
              fontWeight: FontWeight.bold,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          Row(
            children: [
              Text(
                'AI engine',
                style: TextStyle(fontSize: 12, color: palette.textSecondary),
              ),
              const SizedBox(width: AuraSpacing.sm),
              Expanded(
                child: Text(
                  ref.watch(aiRouterStatusProvider).isOffline
                      ? 'AURA (unreachable)'
                      : 'AURA server',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.end,
                  style: TextStyle(fontSize: 12, color: palette.textPrimary),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
