import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// Dedicated view for a `github_create_branch` confirmation -- previously fell through to the
/// generic key/value dump.
class GithubBranchSummary extends StatelessWidget {
  const GithubBranchSummary({super.key, required this.arguments});

  final Map<String, dynamic> arguments;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final branch = arguments['branch']?.toString() ?? '?';
    final fromRef = arguments['from_ref']?.toString() ?? '?';
    final repo = arguments['repo']?.toString();

    return Container(
      padding: const EdgeInsets.all(AuraSpacing.sm),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Icons.call_split, size: 16, color: palette.brand),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  'Create branch${repo != null ? ' on $repo' : ''}',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        fontWeight: FontWeight.w600,
                        color: palette.textSecondary,
                      ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            '$fromRef → $branch',
            style: TextStyle(fontFamily: 'Courier', fontSize: 13, color: palette.textPrimary),
          ),
        ],
      ),
    );
  }
}
