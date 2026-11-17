import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Reminds an email/password account to verify its address (SaaS P1-B). Creating or joining a
/// team needs a verified address, which the backend checks; this is the user's way to get there.
/// Google and Apple sign-ins arrive verified and never see it.
class VerifyEmailBanner extends ConsumerWidget {
  const VerifyEmailBanner({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final identity = ref.watch(identityServiceProvider);
    final user = identity.current;
    if (user == null || user.emailVerified) return const SizedBox.shrink();
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.only(bottom: AuraSpacing.xl),
      child: AuraBanner(
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
    );
  }
}
