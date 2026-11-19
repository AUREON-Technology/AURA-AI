// The second sign-in step (SaaS P1-I): an authenticator code.
//
// Firebase issues no ID token until this step is done, so no backend session exists while the
// dialog is open. A wrong code keeps the dialog and its challenge; Cancel returns null.

import 'package:flutter/material.dart';

import '../../../core/auth/identity_service.dart';
import '../../../core/design_system/design_system.dart';
import '../../../l10n/l10n.dart';

Future<IdentityUser?> showSecondFactorDialog(BuildContext context, SecondFactorRequired challenge) =>
    showDialog<IdentityUser>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _SecondFactorDialog(challenge: challenge),
    );

class _SecondFactorDialog extends StatefulWidget {
  const _SecondFactorDialog({required this.challenge});

  final SecondFactorRequired challenge;

  @override
  State<_SecondFactorDialog> createState() => _SecondFactorDialogState();
}

class _SecondFactorDialogState extends State<_SecondFactorDialog> {
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _verify() async {
    final l10n = context.l10n;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final user = await widget.challenge.resolve(_code.text.trim());
      if (mounted) Navigator.of(context).pop(user);
    } on IdentityError catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.kind == IdentityErrorKind.wrongCode ? l10n.twoStepWrongCode : l10n.identityUnavailable;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final cancel = AuraButton(
      key: const Key('two-step-cancel'),
      label: l10n.cancel,
      variant: AuraButtonVariant.ghost,
      onPressed: _busy ? null : () => Navigator.of(context).pop(),
    );
    // No system back while a code is being checked: popping then would sign the person out while
    // Firebase finishes signing them in (review I#4).
    return PopScope(canPop: !_busy, child: _content(context, l10n, cancel));
  }

  Widget _content(BuildContext context, AppLocalizations l10n, Widget cancel) {
    if (!widget.challenge.totp) {
      return AuraDialog(
        title: l10n.twoStepTitle,
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l10n.twoStepUnsupported, key: const Key('two-step-unsupported')),
          const SizedBox(height: AuraSpacing.md),
          cancel,
        ]),
      );
    }
    return AuraDialog(
      title: l10n.twoStepTitle,
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(l10n.twoStepSignInBody),
        const SizedBox(height: AuraSpacing.sm),
        AuraTextField(
          key: const Key('two-step-code'),
          controller: _code,
          label: l10n.twoStepCodeLabel,
          keyboardType: TextInputType.number,
          autofillHints: const [AutofillHints.oneTimeCode],
          onSubmitted: (_) => _busy ? null : _verify(),
        ),
        if (_error != null) ...[
          const SizedBox(height: AuraSpacing.sm),
          Text(_error!, key: const Key('two-step-error'), style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
        const SizedBox(height: AuraSpacing.md),
        AuraButton(key: const Key('two-step-verify'), label: l10n.twoStepVerify, loading: _busy, onPressed: _verify),
        const SizedBox(height: AuraSpacing.xs),
        cancel,
      ]),
    );
  }
}
