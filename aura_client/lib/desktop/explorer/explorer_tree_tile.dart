/// A single row in the Explorer file tree.
library;

import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';

class ExplorerTreeTile extends StatelessWidget {
  const ExplorerTreeTile({
    super.key,
    required this.entry,
    required this.depth,
    required this.isExpanded,
    required this.isActive,
    required this.onTap,
  });

  final ExplorerEntry entry;
  final int depth;
  final bool isExpanded;
  final bool isActive;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;

    return InkWell(
      onTap: onTap,
      child: Container(
        height: ideDensity.treeRowHeight,
        color: isActive ? palette.brand.withValues(alpha: 0.08) : null,
        padding: EdgeInsets.only(left: 8.0 + depth * 14.0, right: 8),
        child: Row(
          children: [
            if (entry.isDirectory)
              Icon(
                isExpanded ? Icons.keyboard_arrow_down : Icons.keyboard_arrow_right,
                size: 16,
                color: palette.textSecondary,
              )
            else
              const SizedBox(width: 16),
            const SizedBox(width: 2),
            Icon(_iconFor(entry), size: 15, color: palette.textSecondary),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                entry.name,
                style: TextStyle(
                  fontSize: 12.5,
                  color: isActive ? palette.textPrimary : palette.textSecondary,
                  fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }

  IconData _iconFor(ExplorerEntry entry) {
    if (entry.isDirectory) {
      return isExpanded ? Icons.folder_open : Icons.folder;
    }
    final ext = entry.name.contains('.') ? entry.name.split('.').last.toLowerCase() : '';
    switch (ext) {
      case 'dart':
      case 'py':
      case 'js':
      case 'ts':
        return Icons.code;
      case 'json':
        return Icons.data_object;
      case 'yaml':
      case 'yml':
        return Icons.settings;
      case 'md':
        return Icons.description;
      case 'sh':
      case 'ps1':
        return Icons.terminal;
      default:
        return Icons.insert_drive_file;
    }
  }
}
