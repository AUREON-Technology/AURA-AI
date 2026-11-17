import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'auth_provider.dart';

/// Reminds an email/password account to verify its address (SaaS P1-B). Creating or joining a
/// team needs a verified address, which the backend checks; this is the user's way to get there.
/// Google and Apple sign-ins arrive verified and never see it.
///
/// "I've verified" matters: the backend session only learns of the verification from a fresh
/// identity token ([AuthNotifier.confirmEmailVerified]).
class VerifyEmailBanner extends ConsumerStatefulWidget {
  const VerifyEmailBanner({super.key});

  @override
  ConsumerState<VerifyEmailBanner> createState() => _VerifyEmailBannerState();
}

class _VerifyEmailBannerState extends ConsumerState<VerifyEmailBanner> {
  bool _checking = false;

  Future<void> _confirm() async {
    setState(() => _checking = true);
    try {
      await ref.read(authProvider.notifier).confirmEmailVerified();
    } catch (_) {
      // Offline or the provider refused: the banner stays and the user can try again.
    }
    if (mounted) setState(() => _checking = false);
  }

  @override
  Widget build(BuildContext context) {
    final identity = ref.watch(identityServiceProvider);
    final user = identity.current;
    if (user == null || user.emailVerified) return const SizedBox.shrink();
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.only(bottom: AuraSpacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AuraBanner(
            icon: Icons.mark_email_unread_outlined,
            message: l10n.verifyEmailBanner,
            actionLabel: l10n.verifyEmailResend,
            onAction: () async {
              try {
                await identity.resendVerification();
              } catch (_) {
                // Rate-limited or offline: the banner stays, and the user can try again.
              }
            },
          ),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: TextButton(
              onPressed: _checking ? null : _confirm,
              child: Text(l10n.verifyEmailDone),
            ),
          ),
        ],
      ),
    );
  }
}
