/// Bottom panel for Desktop IDE.
///
/// Tab strip for Terminal, Output, Problems panels.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/debug/debug_console_view.dart';
import 'package:aura_client/desktop/diagnostics/diagnostics_notifier.dart';
import 'package:aura_client/desktop/diagnostics/problems_panel.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/output/output_panel.dart';
import 'package:aura_client/desktop/terminal/terminal_view.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Index of the Terminal tab in [_BottomPanelState._tabController] -- must match the order the
/// tabs are declared in below.
const _terminalTabIndex = 0;

class BottomPanel extends ConsumerStatefulWidget {
  const BottomPanel({super.key});

  @override
  ConsumerState<BottomPanel> createState() => _BottomPanelState();
}

class _BottomPanelState extends ConsumerState<BottomPanel> with SingleTickerProviderStateMixin {
  late TabController _tabController;
  StreamSubscription<IdeEvent>? _eventSub;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    // `FocusTerminalRequested` was previously published (the Ctrl+`/Cmd+` shortcut,
    // "Open in Terminal" from the Explorer's context menu) with nothing ever subscribed to it --
    // this is that missing subscriber.
    _eventSub = ref.read(ideEventBusProvider).stream.listen((event) {
      if (event is FocusTerminalRequested) _tabController.animateTo(_terminalTabIndex);
    });
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    final problemsCount = ref.watch(diagnosticsNotifierProvider.select((s) => s.totalCount));

    return Container(
      color: palette.surface,
      child: Column(
        children: [
          // Tab bar
          Container(
            height: ideDensity.tabHeight,
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: palette.border, width: 1)),
            ),
            child: TabBar(
              controller: _tabController,
              tabs: [
                Tab(text: context.l10n.ideBottomTerminal),
                Tab(text: context.l10n.ideBottomOutput),
                Tab(
                  text: problemsCount > 0
                      ? context.l10n.ideBottomProblemsCount(problemsCount)
                      : context.l10n.ideBottomProblems,
                ),
                Tab(text: context.l10n.ideBottomDebugConsole),
              ],
              labelColor: palette.textPrimary,
              unselectedLabelColor: palette.textSecondary,
              indicatorColor: palette.brand,
              indicatorWeight: 2,
              tabAlignment: TabAlignment.start,
              isScrollable: true,
              dividerColor: Colors.transparent,
            ),
          ),
          // Tab content
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: const [
                TerminalView(),
                OutputPanel(),
                ProblemsPanel(),
                DebugConsoleView(),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
