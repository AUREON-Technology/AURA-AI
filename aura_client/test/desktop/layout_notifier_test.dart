/// Regression coverage for the Phase 0 desktop audit's layout findings
/// (2026-09-25): `LayoutNotifier` used to hold in-memory-only defaults even
/// though `desktopLayoutState` was migrated into the schema for exactly this,
/// and its width/height setters had no clamping at all. These tests exercise
/// the fix directly against a real in-memory Drift database, the same way
/// `chat_outbox_test.dart`/`memory_repository_test.dart` do for their tables.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus eventBus;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    eventBus = IdeEventBus();
  });

  tearDown(() async {
    await eventBus.close();
    await db.close();
  });

  /// Waits long enough for a published event's async listener chain (stream
  /// dispatch + an in-memory sqlite round trip) to finish.
  Future<void> pumpEvents() => Future<void>.delayed(const Duration(milliseconds: 50));

  /// Waits past LayoutNotifier's 400ms debounce window for drag-resize writes.
  Future<void> pumpDebounce() => Future<void>.delayed(const Duration(milliseconds: 500));

  test('starts with LayoutState defaults before any workspace is opened', () {
    final notifier = LayoutNotifier(db, eventBus);
    addTearDown(notifier.dispose);

    const defaults = LayoutState();
    expect(notifier.state.primarySidebarWidth, defaults.primarySidebarWidth);
    expect(notifier.state.secondarySidebarWidth, defaults.secondarySidebarWidth);
    expect(notifier.state.bottomPanelHeight, defaults.bottomPanelHeight);
    expect(notifier.state.showPrimarySidebar, isTrue);
    expect(notifier.state.showBottomPanel, isFalse);
  });

  test('adjustPrimarySidebarWidth and adjustBottomPanelHeight clamp to configured bounds', () async {
    final notifier = LayoutNotifier(db, eventBus);
    addTearDown(notifier.dispose);
    eventBus.publish(const WorkspaceOpened('ws-clamp'));
    await pumpEvents();

    notifier.setPrimarySidebarWidth(5000);
    expect(notifier.state.primarySidebarWidth, 640.0);

    notifier.setPrimarySidebarWidth(1);
    expect(notifier.state.primarySidebarWidth, 180.0);

    notifier.setBottomPanelHeight(5000);
    expect(notifier.state.bottomPanelHeight, 640.0);

    notifier.setBottomPanelHeight(1);
    expect(notifier.state.bottomPanelHeight, 120.0);
  });

  test('adjustPrimarySidebarWidth accumulates against live state, not a stale snapshot', () async {
    // Regression for the exact race a drag handle hits: several onDelta
    // callbacks can fire before the widget tree rebuilds, so each one must
    // add to the notifier's own current state rather than one captured by
    // the caller at drag-start.
    final notifier = LayoutNotifier(db, eventBus);
    addTearDown(notifier.dispose);
    eventBus.publish(const WorkspaceOpened('ws-accum'));
    await pumpEvents();

    final start = notifier.state.primarySidebarWidth;
    notifier.adjustPrimarySidebarWidth(10);
    notifier.adjustPrimarySidebarWidth(10);
    notifier.adjustPrimarySidebarWidth(10);

    expect(notifier.state.primarySidebarWidth, start + 30);
  });

  test('persists width changes and restores them for the same workspace after a restart', () async {
    final first = LayoutNotifier(db, eventBus);
    eventBus.publish(const WorkspaceOpened('ws-restart'));
    await pumpEvents();

    first.setPrimarySidebarWidth(444);
    first.setSecondarySidebarWidth(333);
    first.setBottomPanelHeight(222);
    await pumpDebounce();
    first.dispose();

    // A fresh notifier simulates the app restarting -- it should load the
    // persisted row instead of falling back to LayoutState's defaults.
    final second = LayoutNotifier(db, eventBus);
    addTearDown(second.dispose);
    eventBus.publish(const WorkspaceOpened('ws-restart'));
    await pumpEvents();

    expect(second.state.primarySidebarWidth, 444.0);
    expect(second.state.secondarySidebarWidth, 333.0);
    expect(second.state.bottomPanelHeight, 222.0);
  });

  test('persists panel visibility and the active sidebar panel immediately (no debounce wait needed)', () async {
    final first = LayoutNotifier(db, eventBus);
    eventBus.publish(const WorkspaceOpened('ws-visibility'));
    await pumpEvents();

    first.toggleBottomPanel();
    first.showSourceControlPanel();
    await pumpEvents();
    first.dispose();

    final second = LayoutNotifier(db, eventBus);
    addTearDown(second.dispose);
    eventBus.publish(const WorkspaceOpened('ws-visibility'));
    await pumpEvents();

    expect(second.state.showBottomPanel, isTrue);
    expect(second.state.primarySidebarPanel, PrimarySidebarPanel.sourceControl);
    expect(second.state.showPrimarySidebar, isTrue);
  });

  test('a workspace that has never been saved falls back to defaults, not another workspace\'s layout', () async {
    final first = LayoutNotifier(db, eventBus);
    eventBus.publish(const WorkspaceOpened('ws-a'));
    await pumpEvents();
    first.setPrimarySidebarWidth(500);
    await pumpDebounce();
    first.dispose();

    final second = LayoutNotifier(db, eventBus);
    addTearDown(second.dispose);
    eventBus.publish(const WorkspaceOpened('ws-b'));
    await pumpEvents();

    const defaults = LayoutState();
    expect(second.state.primarySidebarWidth, defaults.primarySidebarWidth);
  });
}
