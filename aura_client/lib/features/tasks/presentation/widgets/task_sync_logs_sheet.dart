import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

void showTaskSyncLogsSheet(BuildContext context, List<String> logs) {
  showModalBottomSheet(
    context: context,
    backgroundColor: AuraPalette.of(context).background,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
    ),
    builder: (context) {
      final palette = AuraPalette.of(context);
      return SizedBox(
        height: MediaQuery.sizeOf(context).height * 0.5,
        child: Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: palette.surface,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            border: Border.all(color: palette.border),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      context.l10n.tasksSyncLogTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  IconButton(
                    tooltip: context.l10n.close,
                    icon: Icon(Icons.close, color: palette.textPrimary),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              Divider(color: palette.border, height: 24),
              Expanded(
                child: logs.isEmpty
                    ? Center(
                        child: Text(
                          context.l10n.tasksSyncLogEmpty,
                          style: TextStyle(color: palette.textSecondary),
                        ),
                      )
                    : ListView.builder(
                        itemCount: logs.length,
                        itemBuilder: (context, index) {
                          final log = logs[index];
                          IconData logIcon = Icons.info_outline;
                          Color logColor = palette.textSecondary;

                          if (log.toLowerCase().contains('error') ||
                              log.toLowerCase().contains('failed')) {
                            logIcon = Icons.error_outline;
                            logColor = palette.danger;
                          } else if (log.toLowerCase().contains('success')) {
                            logIcon = Icons.check_circle_outline;
                            logColor = palette.success;
                          } else if (log.toLowerCase().contains('import') ||
                              log.toLowerCase().contains('upload')) {
                            logIcon = Icons.cloud_sync_outlined;
                            logColor = palette.brandOnSurfaceText;
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
