import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:aura_client/core/config/app_links.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// What the Engineer daemon page shows in a browser, which cannot run `aura_hostd`. It says where
/// the daemon is paired instead (the desktop app) and links to the installer and to the list of
/// paired devices, which works from every platform.
class WebDaemonNotice extends StatelessWidget {
  const WebDaemonNotice({super.key});

  /// The installer page is a sibling of the app on the same host: `/desktop-ide/install` next to
  /// `/app/`. Empty when this build does not know where it is served from.
  static String installerUrl() {
    final base = appBaseUrl();
    final uri = Uri.tryParse(base);
    if (uri == null || !uri.hasAuthority) return '';
    return '${uri.scheme}://${uri.authority}/desktop-ide/install';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final installer = installerUrl();
    return Scaffold(
      appBar: AppBar(title: Text(l10n.engineeringDaemonTitle)),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: AuraEmptyState(
            icon: Icons.computer_outlined,
            title: l10n.webDaemonUnavailableTitle,
            message: l10n.webDaemonUnavailableMessage,
            actionLabel: installer.isEmpty ? l10n.webManageDevices : l10n.webGetDesktopApp,
            onAction: installer.isEmpty
                ? () => context.push('/desktop-devices')
                : () => launchUrl(Uri.parse(installer), webOnlyWindowName: '_self'),
            secondaryActionLabel: installer.isEmpty ? null : l10n.webManageDevices,
            onSecondaryAction: installer.isEmpty ? null : () => context.push('/desktop-devices'),
          ),
        ),
      ),
    );
  }
}
