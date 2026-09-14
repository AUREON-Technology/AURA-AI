/// Bottom panel for Desktop IDE.
///
/// Tab strip for Terminal, Output, Problems panels.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/debug/debug_console_view.dart';
import 'package:aura_client/desktop/diagnostics/diagnostics_notifier.dart';
import 'package:aura_client/desktop/diagnostics/problems_panel.dart';
import 'package:aura_client/desktop/output/output_panel.dart';
import 'package:aura_client/desktop/terminal/terminal_view.dart';

class BottomPanel extends ConsumerStatefulWidget {
  const BottomPanel({super.key});

  @override
  ConsumerState<BottomPanel> createState() => _BottomPanelState();
}

class _BottomPanelState extends ConsumerState<BottomPanel> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
  }

  @override
  void dispose() {
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
                const Tab(text: 'Terminal'),
                const Tab(text: 'Output'),
                Tab(text: problemsCount > 0 ? 'Problems ($problemsCount)' : 'Problems'),
                const Tab(text: 'Debug Console'),
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
