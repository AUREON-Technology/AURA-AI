import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/config/legal_links.dart';
import '../../../core/design_system/design_system.dart';
import '../../../l10n/l10n.dart';

final legalLinksProvider = Provider<LegalLinks>((ref) => LegalLinks.fromEnvironment());

/// "By continuing you agree to the Terms of Service · Privacy Policy" at sign-up, linking only
/// the pages this build was given.
class LegalLinksText extends ConsumerWidget {
  const LegalLinksText({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final links = ref.watch(legalLinksProvider);
    if (links.isEmpty) return const SizedBox.shrink();
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    final style = TextStyle(color: palette.textSecondary, fontSize: 12);
    Widget link(String label, Uri uri) => TextButton(
          key: Key('legal-$label'),
          style: TextButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 4), minimumSize: Size.zero),
          onPressed: () => launchUrl(uri, mode: LaunchMode.externalApplication),
          child: Text(label, style: style.copyWith(decoration: TextDecoration.underline)),
        );
    return Wrap(
      key: const Key('legal-links'),
      alignment: WrapAlignment.center,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Text(l10n.legalAgreePrefix, style: style),
        if (links.terms != null) link(l10n.legalTerms, links.terms!),
        if (links.privacy != null) link(l10n.legalPrivacy, links.privacy!),
        if (links.refund != null) link(l10n.legalRefund, links.refund!),
      ],
    );
  }
}
