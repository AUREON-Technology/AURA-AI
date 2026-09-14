/// Output panel widget for Desktop IDE's bottom-panel "Output" tab (D7 Part A.1).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/output/output_log_notifier.dart';

class OutputPanel extends ConsumerStatefulWidget {
  const OutputPanel({super.key});

  @override
  ConsumerState<OutputPanel> createState() => _OutputPanelState();
}

class _OutputPanelState extends ConsumerState<OutputPanel> {
  OutputChannel? _filter;
  final _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final entries = ref.watch(outputLogProvider);
    final filtered = _filter == null ? entries : entries.where((e) => e.channel == _filter).toList();

    ref.listen(outputLogProvider, (previous, next) {
      if (next.length != previous?.length) _scrollToBottom();
    });

    return Container(
      color: palette.surface,
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
            decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border))),
            child: Row(
              children: [
                DropdownButton<OutputChannel?>(
                  value: _filter,
                  isDense: true,
                  underline: const SizedBox.shrink(),
                  style: TextStyle(fontSize: 12, color: palette.textPrimary),
                  dropdownColor: palette.surfaceVariant,
                  items: [
                    const DropdownMenuItem(value: null, child: Text('All')),
                    ...OutputChannel.values.map(
                      (c) => DropdownMenuItem(value: c, child: Text(c.label)),
                    ),
                  ],
                  onChanged: (value) => setState(() => _filter = value),
                ),
                const Spacer(),
                IconButton(
                  icon: Icon(Icons.clear_all, size: 16, color: palette.textSecondary),
                  tooltip: 'Clear Output',
                  onPressed: () => ref.read(outputLogProvider.notifier).clear(),
                ),
              ],
            ),
          ),
          Expanded(
            child: filtered.isEmpty
                ? Center(
                    child: Text('No output yet', style: TextStyle(color: palette.textDisabled, fontSize: 12)),
                  )
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(AuraSpacing.sm),
                    itemCount: filtered.length,
                    itemBuilder: (context, index) {
                      final entry = filtered[index];
                      final time = entry.timestamp.toIso8601String().substring(11, 19);
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 1),
                        child: RichText(
                          text: TextSpan(
                            style: const TextStyle(fontFamily: 'Courier', fontSize: 12),
                            children: [
                              TextSpan(text: '$time  ', style: TextStyle(color: palette.textDisabled)),
                              TextSpan(
                                text: '[${entry.channel.label}] ',
                                style: TextStyle(color: palette.brand, fontWeight: FontWeight.w600),
                              ),
                              TextSpan(text: entry.message, style: TextStyle(color: palette.textPrimary)),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
