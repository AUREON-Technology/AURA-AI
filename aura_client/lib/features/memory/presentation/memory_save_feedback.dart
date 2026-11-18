import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/memory_repository.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Tells the user what became of a memory they just saved: nothing for a confirmed save, a note
/// when it is only queued on this device, an error when the server refused it (F22).
void showMemorySaveOutcome(BuildContext context, MemorySaveOutcome outcome, String fact) {
  switch (outcome) {
    case MemorySaveOutcome.saved:
      return;
    case MemorySaveOutcome.queued:
      AuraSnackbar.show(context, message: context.l10n.memorySaveQueued);
    case MemorySaveOutcome.refused:
      AuraSnackbar.show(context,
          message: context.l10n.memorySaveRefused(fact), kind: AuraSnackbarKind.error);
  }
}
