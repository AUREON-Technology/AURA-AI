/// Actions the IDE shell can perform, independent of what triggered them.
///
/// Extracted so a keybinding and a command-palette entry run the *same* code:
/// saving used to live inside DesktopShortcuts, which meant the palette could
/// only have offered "Save File" by reimplementing it — two copies of a
/// write-to-disk path, which is exactly the kind of thing that drifts.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Writes the active editor tab to disk, clears its dirty flag and announces
/// the save. A no-op when there is no active tab, nothing unsaved, no
/// workspace, or no buffer content — each of which is a normal state, not an
/// error.
///
/// Pass [context] so a failed save, or a file that changed on disk since it was opened, can be
/// shown to the user. Without one nothing is overwritten that the user has not been asked about.
Future<void> saveActiveFile(WidgetRef ref, {BuildContext? context}) async {
  final activeTab = ref.read(editorNotifierProvider).activeTab;
  if (activeTab == null || !activeTab.isDirty) return;
  await saveEditorTab(ref, activeTab, context: context);
}

enum _DiskChangedChoice { overwrite, reload }

/// Saves [tab]. Returns whether its buffer is now what is on disk.
///
/// Three things the old save did not do:
///  * it checks the file has not changed on disk since it was read -- a formatter, `git checkout`
///    or the Engineer agent may have rewritten it -- and asks before overwriting that change;
///  * it reports a failure (read-only file, locked file, a folder that no longer exists) instead
///    of leaving the tab dirty with no word said;
///  * it marks the tab clean only if the buffer still is the text that was written, so what was
///    typed while the write was in flight is not declared saved along with it.
Future<bool> saveEditorTab(WidgetRef ref, EditorTab tab, {BuildContext? context}) async {
  final workspace = ref.read(workspaceNotifierProvider).activeWorkspace;
  if (workspace == null) return false;

  final notifier = ref.read(editorNotifierProvider.notifier);
  final content = notifier.contentFor(tab.id);
  if (content == null) return false;

  final service = WorkspaceFileService(workspace.rootPath);
  try {
    final onDisk = await _readForCheck(service, tab.path);
    if (onDisk != null && notifier.hasDiskChanged(tab.id, onDisk)) {
      final choice = context != null && context.mounted ? await _askDiskChanged(context, tab) : null;
      if (choice == _DiskChangedChoice.reload) {
        notifier.reloadFromDisk(tab.id, onDisk);
        return false;
      }
      if (choice != _DiskChangedChoice.overwrite) return false;
    }
    await service.writeFile(tab.path, content);
  } catch (error) {
    debugPrint('[saveEditorTab] could not save ${tab.path}: $error');
    if (context != null && context.mounted) {
      AuraSnackbar.show(
        context,
        message: context.l10n.editorSaveFailed(tab.title, '$error'),
        kind: AuraSnackbarKind.error,
      );
    }
    return false;
  }

  await notifier.markSaved(tab.id, content);
  ref.read(ideEventBusProvider).publish(FileSaved(tab.path, workspace.id));
  return true;
}

/// The file's text on disk, or `null` when there is nothing to compare with (the file is gone,
/// or cannot be read as text -- the write that follows reports what is wrong, if anything).
Future<String?> _readForCheck(WorkspaceFileService service, String path) async {
  try {
    if (!service.fileExists(path)) return null;
    return await service.readFile(path);
  } catch (_) {
    return null;
  }
}

Future<_DiskChangedChoice?> _askDiskChanged(BuildContext context, EditorTab tab) {
  return showDialog<_DiskChangedChoice>(
    context: context,
    builder: (dialogContext) => AlertDialog(
      title: Text(dialogContext.l10n.editorDiskChangedTitle),
      content: Text(dialogContext.l10n.editorDiskChangedBody(tab.title)),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(dialogContext).pop(),
          child: Text(dialogContext.l10n.cancel),
        ),
        TextButton(
          onPressed: () => Navigator.of(dialogContext).pop(_DiskChangedChoice.reload),
          child: Text(dialogContext.l10n.editorReloadFromDisk),
        ),
        FilledButton(
          onPressed: () => Navigator.of(dialogContext).pop(_DiskChangedChoice.overwrite),
          child: Text(dialogContext.l10n.editorOverwrite),
        ),
      ],
    ),
  );
}
