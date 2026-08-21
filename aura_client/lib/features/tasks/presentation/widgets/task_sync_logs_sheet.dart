import 'package:flutter/material.dart';

import 'package:aura_client/core/theme/theme.dart';

void showTaskSyncLogsSheet(BuildContext context, List<String> logs) {
  showModalBottomSheet(
    context: context,
    backgroundColor: const Color(0xFF0F0C1B),
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
    ),
    builder: (context) {
      return SizedBox(
        height: MediaQuery.sizeOf(context).height * 0.5,
        child: Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: AuraTheme.cardBg,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            border: Border.all(color: const Color(0x1F94A3B8)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Tasks Sync Activity Log',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              const Divider(color: Color(0x1F94A3B8), height: 24),
              Expanded(
                child: logs.isEmpty
                    ? const Center(
                        child: Text(
                          'No logs recorded yet. Press sync to begin.',
                          style: TextStyle(color: AuraTheme.textSecondary),
                        ),
                      )
                    : ListView.builder(
                        itemCount: logs.length,
                        itemBuilder: (context, index) {
                          final log = logs[index];
                          IconData logIcon = Icons.info_outline;
                          Color logColor = AuraTheme.textSecondary;

                          if (log.toLowerCase().contains('error') ||
                              log.toLowerCase().contains('failed')) {
                            logIcon = Icons.error_outline;
                            logColor = Colors.redAccent;
                          } else if (log.toLowerCase().contains('success')) {
                            logIcon = Icons.check_circle_outline;
                            logColor = AuraTheme.accentTeal;
                          } else if (log.toLowerCase().contains('import') ||
                              log.toLowerCase().contains('upload')) {
                            logIcon = Icons.cloud_sync_outlined;
                            logColor = AuraTheme.accentIndigo;
                          }

                          return Padding(
                            padding: const EdgeInsets.symmetric(vertical: 6.0),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(logIcon, color: logColor, size: 18),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Text(
                                    log,
                                    style: TextStyle(
                                      color: logColor,
                                      fontSize: 13,
                                      fontFamily: 'Courier',
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
              ),
            ],
          ),
        ),
      );
    },
  );
}
