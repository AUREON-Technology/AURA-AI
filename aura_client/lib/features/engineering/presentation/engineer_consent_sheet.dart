/// The Engineer's Anthropic consent notice (D-03, UX-01): shown before the first Engineer send,
/// and again when the server refuses a turn with `consent_required`.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Shows the notice and returns whether the user agreed -- and the server recorded it.
///
/// With [updateRequired] (the server requires a notice version this app does not carry), it
/// says "update the app" instead, and always returns false: agreeing to words the user was not
/// shown is not consent.
Future<bool> showEngineerConsentSheet(
  BuildContext context, {
  bool updateRequired = false,
}) async {
  final agreed = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    useSafeArea: true,
    builder: (_) => EngineerConsentSheet(updateRequired: updateRequired),
  );
  return agreed == true;
}

/// The gate in front of an Engineer send. Returns true when the send may go ahead: consent is
/// current, or the user just agreed. Returns false after "Not now" or a dismissal, and the
/// caller must not send.
///
/// If the consent status cannot be fetched, the send goes ahead: the server checks consent on
/// every Engineer turn, refuses without it before any provider call, and that refusal brings
/// this sheet up through [EngineerConsentPromptListener].
Future<bool> ensureEngineerConsent(BuildContext context, WidgetRef ref) async {
  final EngineerConsent consent;
  try {
    consent = await ref.read(engineerConsentProvider.future);
  } catch (_) {
    // Not retried automatically: fetch again on the next send rather than trusting this error.
    ref.invalidate(engineerConsentProvider);
    return true;
  }
  if (consent.isCurrent) return true;
  if (!context.mounted) return false;
  return showEngineerConsentSheet(context, updateRequired: !consent.appNoticeIsCurrent);
}

class EngineerConsentSheet extends ConsumerStatefulWidget {
  const EngineerConsentSheet({super.key, this.updateRequired = false});

  final bool updateRequired;

  @override
  ConsumerState<EngineerConsentSheet> createState() => _EngineerConsentSheetState();
}

class _EngineerConsentSheetState extends ConsumerState<EngineerConsentSheet> {
  late bool _updateRequired = widget.updateRequired;
  bool _saving = false;
  String? _error;

  Future<void> _agree() async {
    final l10n = context.l10n;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref.read(engineerConsentProvider.notifier).accept();
      if (mounted) Navigator.of(context).pop(true);
    } on EngineerConsentVersionMismatch {
      if (mounted) setState(() => _updateRequired = true);
    } catch (e) {
      if (mounted) {
        setState(() => _error = l10n.engineerConsentSaveFailed(AuraFailure.from(e).message));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    final theme = Theme.of(context);
    final body = theme.textTheme.bodyMedium?.copyWith(color: palette.textSecondary, height: 1.45);

    final List<Widget> content;
    final List<Widget> actions;
    if (_updateRequired) {
      content = [
        Text(l10n.engineerConsentUpdateTitle, style: theme.textTheme.titleLarge),
        const SizedBox(height: AuraSpacing.md),
        Text(l10n.engineerConsentUpdateBody, style: body),
      ];
      actions = [
        Expanded(
          child: AuraButton(
            label: l10n.engineerConsentUpdateOk,
            expand: true,
            onPressed: () => Navigator.of(context).pop(false),
          ),
        ),
      ];
    } else {
      content = [
        Text(l10n.engineerConsentTitle, style: theme.textTheme.titleLarge),
        const SizedBox(height: AuraSpacing.md),
        Text(l10n.engineerConsentBodyWhat, style: body),
        const SizedBox(height: AuraSpacing.sm),
        Text(l10n.engineerConsentBodyIncludes, style: body),
        const SizedBox(height: AuraSpacing.sm),
        Text(l10n.engineerConsentBodyChoice, style: body),
        if (_error != null) ...[
          const SizedBox(height: AuraSpacing.md),
          Text(_error!, style: body?.copyWith(color: palette.danger)),
        ],
      ];
      actions = [
        Expanded(
          child: AuraButton(
            label: l10n.engineerConsentNotNow,
            variant: AuraButtonVariant.secondary,
            expand: true,
            onPressed: _saving ? null : () => Navigator.of(context).pop(false),
          ),
        ),
        const SizedBox(width: AuraSpacing.md),
        Expanded(
          child: AuraButton(
            label: l10n.engineerConsentAgree,
            expand: true,
            loading: _saving,
            onPressed: _agree,
          ),
        ),
      ];
    }

    return Center(
      heightFactor: 1,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            AuraSpacing.xl,
            0,
            AuraSpacing.xl,
            AuraSpacing.xl,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ...content,
              const SizedBox(height: AuraSpacing.xl),
              Row(children: actions),
            ],
          ),
        ),
      ),
    );
  }
}

/// Wraps a chat surface so a `consent_required` refusal ([engineerConsentPromptProvider]) shows
/// the consent sheet there, and agreeing resends the refused turn. When two surfaces are
/// mounted, the first to take the prompt handles it.
class EngineerConsentPromptListener extends ConsumerWidget {
  const EngineerConsentPromptListener({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.listen<EngineerConsentPrompt?>(engineerConsentPromptProvider, (_, next) {
      if (next == null) return;
      // Out of the notification: taking the prompt changes the provider being notified.
      scheduleMicrotask(() {
        if (context.mounted) unawaited(_handlePrompt(context, ref));
      });
    });
    return child;
  }

  Future<void> _handlePrompt(BuildContext context, WidgetRef ref) async {
    final prompt = ref.read(engineerConsentPromptProvider.notifier).take();
    if (prompt == null) return;
    final current = prompt.currentVersion;
    final agreed = await showEngineerConsentSheet(
      context,
      updateRequired: current != null && current != kEngineerConsentNoticeVersion,
    );
    if (!agreed || !context.mounted) return;
    await ref.read(chatRepositoryProvider).retryLastFailedTurn(
          conversationId: prompt.conversationId,
          errorMessageId: prompt.messageId,
        );
  }
}
