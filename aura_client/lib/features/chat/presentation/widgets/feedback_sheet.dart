import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/features/chat/data/feedback_repository.dart';

Future<void> showFeedbackSheet({
  required BuildContext context,
  required WidgetRef ref,
  required String turnId,
  String? prompt,
  String? response,
}) {
  final correction = TextEditingController();
  var rating = 5;
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
        child: StatefulBuilder(
          builder: (context, setState) {
            return Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Rate this answer', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: AuraSpacing.md),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    for (var i = 1; i <= 5; i++)
                      IconButton(
                        tooltip: '$i of 5',
                        onPressed: () => setState(() => rating = i),
                        icon: Icon(
                          i <= rating ? Icons.star : Icons.star_border,
                          color: AuraPalette.of(context).warning,
                        ),
                      ),
                  ],
                ),
                AuraTextField(
                  controller: correction,
                  label: 'Optional correction',
                  maxLines: 3,
                ),
                const SizedBox(height: AuraSpacing.lg),
                AuraButton(
                  label: 'Send feedback',
                  expand: true,
                  onPressed: () async {
                    try {
                      await ref.read(feedbackRepositoryProvider).submit(
                            turnId: turnId,
                            rating: rating,
                            correction: correction.text.trim(),
                            prompt: prompt,
                            response: response,
                          );
                      if (context.mounted) {
                        Navigator.of(context).pop();
                        AuraSnackbar.show(
                          context,
                          message: 'Thanks — feedback sent.',
                          kind: AuraSnackbarKind.success,
                        );
                      }
                    } on AuraApiError catch (e) {
                      if (context.mounted) {
                        AuraSnackbar.show(
                          context,
                          message: e.message,
                          kind: AuraSnackbarKind.error,
                        );
                      }
                    }
                  },
                ),
              ],
            );
          },
        ),
      );
    },
  ).whenComplete(correction.dispose);
}
