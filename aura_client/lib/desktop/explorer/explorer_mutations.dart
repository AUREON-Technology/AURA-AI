/// File-management actions for the Explorer's context menu: rename, delete, new file, new
/// folder, and open in terminal.
///
/// Phase 0 desktop audit (2026-09-25) found the context menu had only its read-only actions
/// (copy path, refresh, reveal) -- these five were entirely absent, with no backing
/// `HostdControlClient` method for them either. They land here as `dart:io`-direct operations via
/// [WorkspaceFileService] instead: per this repo's own CLAUDE.md, "human IDE reads via hostd
/// (granted roots), writes via dart:io (workspace root)" -- the human's own file management,
/// like the existing editor save path, does not need a hostd grant at all.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:collection/collection.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

/// Prompts for a name via a simple text-field dialog. Returns the trimmed text, or `null` if
/// cancelled or left blank.
Future<String?> promptForName(
  BuildContext context, {
  required String title,
  required String label,
  required String confirmLabel,
  String initialValue = '',
}) async {
  final controller = TextEditingController(text: initialValue);
  try {
    final result = await AuraDialog.show<String>(
      context: context,
      title: title,
      confirmLabel: confirmLabel,
      cancelLabel: 'Cancel',
      onConfirm: () => Navigator.of(context).pop(controller.text.trim()),
      child: AuraTextField(
        controller: controller,
        label: label,
        textInputAction: TextInputAction.done,
        onSubmitted: (value) => Navigator.of(context).pop(value.trim()),
      ),
    );
    return (result == null || result.isEmpty) ? null : result;
  } finally {
    controller.dispose();
  }
}

/// Confirms a destructive delete with the human first -- deleting a directory is recursive and
/// neither can be undone.
Future<bool> _confirmDelete(BuildContext context, {required String name, required bool isDirectory}) async {
  final confirmed = await AuraDialog.show<bool>(
    context: context,
    title: 'Delete ${isDirectory ? 'folder' : 'file'}?',
    confirmLabel: 'Delete',
    cancelLabel: 'Cancel',
    confirmDestructive: true,
    onConfirm: () => Navigator.of(context).pop(true),
    child: Text(
      isDirectory
          ? '"$name" and everything inside it will be permanently deleted.'
          : '"$name" will be permanently deleted.',
    ),
  );
  return confirmed == true;
}

/// Renames [absolutePath] (a file or directory), refreshing via [onDone] on success. [onDone] is
/// the caller's own `reload()`/`_refresh()` -- the mutating tile has no reference to whichever
/// ancestor (`_ExplorerTree` or a parent `_DirectoryTile`) actually owns the listing it's part of.
Future<void> renameEntry(
  BuildContext context,
  WidgetRef ref, {
  required String workspaceRoot,
  required String workspaceId,
  required String absolutePath,
  required String currentName,
  required bool isDirectory,
  required VoidCallback onDone,
}) async {
  final newName = await promptForName(
    context,
    title: 'Rename ${isDirectory ? 'folder' : 'file'}',
    label: 'New name',
    confirmLabel: 'Rename',
    initialValue: currentName,
  );
  if (newName == null || newName == currentName || !context.mounted) return;
  final newPath = p.join(p.dirname(absolutePath), newName);
  try {
    await WorkspaceFileService(workspaceRoot).rename(absolutePath, newPath, isDirectory: isDirectory);
    ref.read(ideEventBusProvider).publish(FileRenamed(absolutePath, newPath, workspaceId));
    onDone();
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: 'Could not rename: $e', kind: AuraSnackbarKind.error);
    }
  }
}

/// Deletes [absolutePath] after human confirmation, refreshing via [onDone] on success.
Future<void> deleteEntry(
  BuildContext context,
  WidgetRef ref, {
  required String workspaceRoot,
  required String workspaceId,
  required String absolutePath,
  required String name,
  required bool isDirectory,
  required VoidCallback onDone,
}) async {
  final confirmed = await _confirmDelete(context, name: name, isDirectory: isDirectory);
  if (!confirmed || !context.mounted) return;
  try {
    await WorkspaceFileService(workspaceRoot).delete(absolutePath, isDirectory: isDirectory);
    ref.read(ideEventBusProvider).publish(FileDeleted(absolutePath, workspaceId));
    onDone();
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: 'Could not delete: $e', kind: AuraSnackbarKind.error);
    }
  }
}

/// Creates a new, empty file inside [parentAbsolutePath], refreshing via [onDone] on success.
Future<void> createFileIn(
  BuildContext context,
  WidgetRef ref, {
  required String workspaceRoot,
  required String workspaceId,
  required String parentAbsolutePath,
  required VoidCallback onDone,
}) async {
  final name = await promptForName(context, title: 'New file', label: 'File name', confirmLabel: 'Create');
  if (name == null || !context.mounted) return;
  final path = p.join(parentAbsolutePath, name);
  try {
    await WorkspaceFileService(workspaceRoot).createFile(path);
    ref.read(ideEventBusProvider).publish(FileCreated(path, workspaceId));
    onDone();
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: 'Could not create file: $e', kind: AuraSnackbarKind.error);
    }
  }
}

/// Creates a new, empty folder inside [parentAbsolutePath], refreshing via [onDone] on success.
Future<void> createFolderIn(
  BuildContext context,
  WidgetRef ref, {
  required String workspaceRoot,
  required String workspaceId,
  required String parentAbsolutePath,
  required VoidCallback onDone,
}) async {
  final name = await promptForName(context, title: 'New folder', label: 'Folder name', confirmLabel: 'Create');
  if (name == null || !context.mounted) return;
  final path = p.join(parentAbsolutePath, name);
  try {
    await WorkspaceFileService(workspaceRoot).createDirectory(path);
    ref.read(ideEventBusProvider).publish(FileCreated(path, workspaceId));
    onDone();
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: 'Could not create folder: $e', kind: AuraSnackbarKind.error);
    }
  }
}

/// Opens (or redirects) the workspace's terminal at [absoluteDirPath].
///
/// `TerminalView` only ever shows the *first* session for a workspace -- a session tab strip for
/// browsing multiple concurrent sessions is a tracked D4 follow-up, not built yet -- so spawning
/// a second session here would be invisible: nothing in the UI could switch to it. Instead, this
/// starts the first session directly at [absoluteDirPath] if none exists yet (so the one session
/// that becomes visible is already rooted where the human asked), or sends a `cd` into the
/// existing one otherwise.
Future<void> openInTerminal(
  BuildContext context,
  WidgetRef ref, {
  required String workspaceId,
  required String absoluteDirPath,
}) async {
  final notifier = ref.read(terminalNotifierProvider.notifier);
  final existing =
      ref.read(terminalNotifierProvider).sessions.where((s) => s.workspaceId == workspaceId).firstOrNull;
  if (existing == null) {
    await notifier.start(workspaceId: workspaceId, rootPath: absoluteDirPath);
  } else {
    // The desktop terminal is PowerShell-only today (ADR-015, Windows-first; aura_hostd's
    // PtySession hardcodes powershell.exe) -- its single-quoted strings escape an embedded quote
    // by doubling it, unlike POSIX shells' '\''.
    final quoted = "'${absoluteDirPath.replaceAll("'", "''")}'";
    notifier.sendInput(existing.id, Uint8List.fromList(utf8.encode('cd $quoted\r')));
  }
  final layout = ref.read(layoutNotifierProvider.notifier);
  if (!ref.read(layoutNotifierProvider).showBottomPanel) layout.toggleBottomPanel();
  ref.read(ideEventBusProvider).publish(const FocusTerminalRequested());
}
