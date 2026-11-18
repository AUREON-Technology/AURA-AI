// Two-step verification settings (SaaS P1-I, spec §5.1): an optional authenticator code on every
// sign-in, through Firebase multi-factor. Offered to everyone signed in: each person owns their
// personal workspace, so "Owners" is everyone. Nothing enforces it.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/auth/identity_service.dart';
import '../../../core/design_system/design_system.dart';
import '../../../l10n/l10n.dart';
import '../../workspaces/presentation/workspace_providers.dart' show confirm;
import 'login_page.dart' show identityErrorText;

class TwoStepPage extends ConsumerStatefulWidget {
  const TwoStepPage({super.key});

  @override
  ConsumerState<TwoStepPage> createState() => _TwoStepPageState();
}

class _TwoStepPageState extends ConsumerState<TwoStepPage> {
  List<SecondFactor>? _factors;

  /// The secret being set up. Held only while this page is open; never stored or logged.
  TotpEnrollment? _enrolling;
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  IdentityService get _identity => ref.read(identityServiceProvider);

  Future<void> _load() async {
    try {
      final factors = await _identity.secondFactors();
      if (mounted) setState(() => _factors = factors);
    } on IdentityError catch (e) {
      if (mounted) setState(() => _error = identityErrorText(e.kind, context.l10n));
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
    } on IdentityError catch (e) {
      if (!mounted) return;
      setState(() => _error = e.kind == IdentityErrorKind.wrongCode
          ? context.l10n.twoStepWrongCode
          : identityErrorText(e.kind, context.l10n));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _start() => _run(() async {
        final enrollment = await _identity.startTotpEnrollment();
        if (mounted) setState(() => _enrolling = enrollment);
      });

  Future<void> _finish() => _run(() async {
        final l10n = context.l10n;
        await _identity.finishTotpEnrollment(_enrolling!, _code.text.trim());
        _code.clear();
        if (!mounted) return;
        setState(() => _enrolling = null);
        AuraSnackbar.show(context, message: l10n.twoStepEnabled, kind: AuraSnackbarKind.success);
        await _load();
      });

  Future<void> _turnOff(SecondFactor factor) async {
    final l10n = context.l10n;
    if (!await confirm(context, title: l10n.twoStepTitle, body: l10n.twoStepTurnOffConfirm,
        action: l10n.twoStepTurnOff, destructive: true)) {
      return;
    }
    await _run(() async {
      await _identity.removeSecondFactor(factor.uid);
      await _load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final verified = ref.watch(identityServiceProvider).current?.emailVerified ?? false;
    final factors = _factors;
    final children = <Widget>[Text(l10n.twoStepIntro), const SizedBox(height: AuraSpacing.lg)];
    if (!verified) {
      children.add(Text(l10n.twoStepVerifyEmailFirst, key: const Key('two-step-verify-email')));
    } else if (factors == null) {
      children.add(const Center(child: CircularProgressIndicator()));
    } else if (factors.isNotEmpty) {
      children.add(Text(l10n.twoStepOn, key: const Key('two-step-on')));
      for (final factor in factors) {
        children.add(AuraListTile(
          icon: Icons.verified_user_outlined,
          title: factor.displayName.isEmpty ? l10n.twoStepTitle : factor.displayName,
          trailing: AuraButton(
            key: const Key('two-step-off'),
            label: l10n.twoStepTurnOff,
            variant: AuraButtonVariant.ghost,
            loading: _busy,
            onPressed: _busy ? null : () => _turnOff(factor),
          ),
        ));
      }
    } else if (_enrolling case final enrolling?) {
      children.addAll([
        Text(l10n.twoStepScanOrEnter),
        const SizedBox(height: AuraSpacing.sm),
        AuraCard(
          child: Row(children: [
            Expanded(child: SelectableText(enrolling.secretKey, key: const Key('two-step-secret'))),
            IconButton(
              icon: const Icon(Icons.copy),
              tooltip: l10n.twoStepCopyKey,
              onPressed: () => Clipboard.setData(ClipboardData(text: enrolling.secretKey)),
            ),
          ]),
        ),
        const SizedBox(height: AuraSpacing.sm),
        AuraButton(
          label: l10n.twoStepOpenApp,
          variant: AuraButtonVariant.secondary,
          onPressed: () => launchUrl(Uri.parse(enrolling.otpauthUrl), mode: LaunchMode.externalApplication),
        ),
        const SizedBox(height: AuraSpacing.md),
        AuraTextField(
          key: const Key('two-step-enroll-code'),
          controller: _code,
          label: l10n.twoStepCodeLabel,
          keyboardType: TextInputType.number,
          autofillHints: const [AutofillHints.oneTimeCode],
        ),
        const SizedBox(height: AuraSpacing.sm),
        AuraButton(key: const Key('two-step-enroll-verify'), label: l10n.twoStepVerify, loading: _busy, onPressed: _finish),
      ]);
    } else {
      children.add(AuraButton(key: const Key('two-step-setup'), label: l10n.twoStepSetUp, loading: _busy, onPressed: _start));
    }
    if (_error != null) {
      children.addAll([
        const SizedBox(height: AuraSpacing.md),
        Text(_error!, key: const Key('two-step-page-error'), style: TextStyle(color: Theme.of(context).colorScheme.error)),
      ]);
    }
    return Scaffold(
      appBar: AppBar(title: Text(l10n.twoStepTitle)),
      body: ListView(padding: const EdgeInsets.all(AuraSpacing.lg), children: children),
    );
  }
}
