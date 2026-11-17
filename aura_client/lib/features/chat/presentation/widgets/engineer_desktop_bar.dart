/// The Engineer chat's desktop bar (WP8, D-10, UX-06): above the composer on a phone, the web,
/// or any chat surface that is not the Desktop IDE, it says what the user's computer allows --
/// not connected, remote control off there, or a folder to work in -- and lets them pick the
/// folder. The pick is what the turn sends as `workspace_root`
/// ([engineerRemoteWorkspaceRootProvider]).
///
/// Not shown inside the Desktop IDE, whose Engineer panel binds the open workspace instead.
/// Approvals for what the Engineer then does arrive in the chat as the usual confirmation card.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../../data/engineer_remote_folder.dart';

class EngineerDesktopBar extends ConsumerWidget {
  const EngineerDesktopBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final folders = ref.watch(hostFoldersProvider);

    final Widget content = switch (folders) {
      AsyncData(:final value) => switch (value.state) {
          HostFoldersState.notConnected => _Message(l10n.engineerDesktopNotConnected),
          HostFoldersState.remoteControlOff => _Message(l10n.engineerDesktopRemoteOff),
          HostFoldersState.noFolders => _Message(l10n.engineerDesktopNoFolders),
          HostFoldersState.available => _FolderPicker(folders: value),
        },
      AsyncError() => _Message(l10n.engineerDesktopCheckFailed, color: palette.danger),
      _ => const Align(
          alignment: AlignmentDirectional.centerStart,
          child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
        ),
    };

    return Container(
      width: double.infinity,
      padding: const EdgeInsetsDirectional.only(start: AuraSpacing.md, end: AuraSpacing.xs),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        border: Border(top: BorderSide(color: palette.border)),
      ),
      child: Row(
        children: [
          Icon(Icons.computer_outlined, size: 16, color: palette.textSecondary),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(child: content),
          IconButton(
            tooltip: l10n.engineerDesktopRefresh,
            visualDensity: VisualDensity.compact,
            icon: Icon(Icons.refresh, size: 18, color: palette.textSecondary),
            onPressed: () => ref.invalidate(hostFoldersProvider),
          ),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text, {this.color});

  final String text;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AuraSpacing.sm),
      child: Text(text, style: TextStyle(fontSize: 12, color: color ?? palette.textSecondary)),
    );
  }
}

class _FolderPicker extends ConsumerWidget {
  const _FolderPicker({required this.folders});

  final HostFolders folders;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final label = context.l10n.engineerDesktopFolderLabel;
    final selected = ref.watch(engineerRemoteWorkspaceRootProvider);
    return Semantics(
      label: label,
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          isExpanded: true,
          isDense: true,
          value: selected,
          hint: Text(label),
          dropdownColor: palette.surface,
          style: TextStyle(fontSize: 12, color: palette.textPrimary, fontFamily: 'Courier'),
          items: [
            for (final root in folders.roots)
              DropdownMenuItem(
                value: root.path,
                child: Text(root.path, overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: (path) {
            if (path != null) ref.read(engineerRemoteFolderChoiceProvider.notifier).choose(path);
          },
        ),
      ),
    );
  }
}
