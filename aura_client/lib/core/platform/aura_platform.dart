/// Platform detection for AURA Desktop IDE.
///
/// Provides a clean gate for the desktop IDE features without conflating
/// "wide mobile layout" with "desktop IDE".
library;

import 'package:flutter/foundation.dart';

/// Whether the current platform is a desktop IDE target: native Windows or Linux.
///
/// This is a **platform gate**, not a width gate. It is false on web, mobile and macOS
/// (D-08: Windows and Linux launch; macOS is out). D-08b turned it on for Linux, which gets the
/// same panel, folder binding, terminal and approvals as Windows. Use this to conditionally
/// render the Desktop IDE shell and routes; anything genuinely Windows-specific (installer,
/// PowerShell quoting, Win32 paths) still checks `Platform.isWindows` itself.
bool get isDesktopIde =>
    !kIsWeb &&
    (defaultTargetPlatform == TargetPlatform.windows || defaultTargetPlatform == TargetPlatform.linux);

/// Where AURA lands after sign-in and onboarding. The desktop app (Windows, and Linux since
/// D-08b) is the IDE and nothing else; every other build is the one AURA app, which opens on
/// Copilot (docs/plans/mobile-desktop-split_plan.md).
String landingRouteFor({required bool desktop}) => desktop ? '/ide' : '/chat';

/// The landing route for the platform this build is running on.
String get appLandingRoute => landingRouteFor(desktop: isDesktopIde);

/// Whether the current platform can run the desktop IDE (Windows and Linux).
bool get canRunDesktopIde => isDesktopIde;

/// Whether this is the native desktop app (Windows, Linux or macOS), whose sign-in issues a
/// desktop token. Its Engineer turns count as desktop-origin on the server (WP8), so the
/// phone-and-web "remote control" opt-in does not apply to them.
bool get isDesktopApp =>
    !kIsWeb &&
    (defaultTargetPlatform == TargetPlatform.windows ||
        defaultTargetPlatform == TargetPlatform.linux ||
        defaultTargetPlatform == TargetPlatform.macOS);
