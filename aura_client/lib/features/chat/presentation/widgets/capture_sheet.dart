import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

Future<void> showCaptureSheet(BuildContext context) {
  final controller = TextEditingController();
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (context) {
      final bottom = MediaQuery.viewInsetsOf(context).bottom;
      return Padding(
        padding: EdgeInsets.fromLTRB(
          AuraSpacing.xl,
          AuraSpacing.md,
          AuraSpacing.xl,
          AuraSpacing.xl + bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(context.l10n.captureTitle, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: AuraSpacing.md),
            AuraTextField(
              controller: controller,
              hintText: context.l10n.captureHint,
              maxLines: 3,
              textInputAction: TextInputAction.send,
              onSubmitted: (value) => _go(context, value),
            ),
            const SizedBox(height: AuraSpacing.lg),
            AuraButton(
              label: context.l10n.captureSend,
              icon: Icons.auto_awesome,
              expand: true,
              onPressed: () => _go(context, controller.text),
            ),
          ],
        ),
      );
    },
  ).whenComplete(controller.dispose);
}

void _go(BuildContext context, String raw) {
  final query = raw.trim();
  Navigator.of(context).pop();
  if (query.isEmpty) {
    context.go('/chat');
    return;
  }
  context.go('/chat', extra: query);
}
