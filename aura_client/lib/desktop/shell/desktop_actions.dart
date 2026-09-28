/// Actions the IDE shell can perform, independent of what triggered them.
///
/// Extracted so a keybinding and a command-palette entry run the *same* code:
/// saving used to live inside DesktopShortcuts, which meant the palette could
/// only have offered "Save File" by reimplementing it — two copies of a
/// write-to-disk path, which is exactly the kind of thing that drifts.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

/// Writes the active editor tab to disk, clears its dirty flag and announces
/// the save. A no-op when there is no active tab, nothing unsaved, no
/// workspace, or no buffer content — each of which is a normal state, not an
/// error.
Future<void> saveActiveFile(WidgetRef ref) async {
  final activeTab = ref.read(editorNotifierProvider).activeTab;
  if (activeTab == null || !activeTab.isDirty) return;

  final workspace = ref.read(workspaceNotifierProvider).activeWorkspace;
  if (workspace == null) return;

  final content = ref.read(editorNotifierProvider.notifier).contentFor(activeTab.id);
  if (content == null) return;

  await WorkspaceFileService(workspace.rootPath).writeFile(activeTab.path, content);
  ref.read(editorNotifierProvider.notifier).markDirty(activeTab.id, false);
  ref.read(ideEventBusProvider).publish(FileSaved(activeTab.path, workspace.id));
}
