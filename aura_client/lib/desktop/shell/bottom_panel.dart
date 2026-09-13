/// Bottom panel for Desktop IDE.
///
/// Tab strip for Terminal, Output, Problems panels.
library;

import 'package:flutter/material.dart';
import 'package:aura_client/core/design_system/design_system.dart';

class BottomPanel extends StatefulWidget {
  const BottomPanel({super.key});

  @override
  State<BottomPanel> createState() => _BottomPanelState();
}

class _BottomPanelState extends State<BottomPanel> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 3, vsync: this);
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
              tabs: const [
                Tab(text: 'Terminal'),
                Tab(text: 'Output'),
                Tab(text: 'Problems'),
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
                _TerminalPlaceholder(),
                _OutputPlaceholder(),
                _ProblemsPlaceholder(),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TerminalPlaceholder extends StatelessWidget {
  const _TerminalPlaceholder();

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Container(
      color: Colors.black,
      child: Center(
        child: Text(
          'Terminal (D4)',
          style: TextStyle(color: palette.textSecondary, fontFamily: 'Courier', fontSize: 14),
        ),
      ),
    );
  }
}

class _OutputPlaceholder extends StatelessWidget {
  const _OutputPlaceholder();

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Center(
      child: Text(
        'Output (D4)',
        style: TextStyle(color: palette.textSecondary),
      ),
    );
  }
}

class _ProblemsPlaceholder extends StatelessWidget {
  const _ProblemsPlaceholder();

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Center(
      child: Text(
        'Problems (D7)',
        style: TextStyle(color: palette.textSecondary),
      ),
    );
  }
}