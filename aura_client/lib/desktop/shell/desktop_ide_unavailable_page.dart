/// What `/ide` shows on a platform without the Desktop IDE (macOS, mobile, web).
///
/// The route used to render `SizedBox.shrink()` there: a blank screen with no way back (WP7).
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/platform/aura_platform.dart';
import 'package:aura_client/l10n/l10n.dart';

class DesktopIdeUnavailablePage extends StatelessWidget {
  const DesktopIdeUnavailablePage({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: palette.background,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: AuraEmptyState(
                icon: Icons.desktop_access_disabled_outlined,
                title: l10n.desktopIdeUnavailableTitle,
                message: l10n.desktopIdeUnavailableBody,
                actionLabel: l10n.desktopIdeUnavailableGoHome,
                onAction: () => context.go(appLandingRoute),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
