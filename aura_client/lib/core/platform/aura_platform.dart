/// Platform detection for AURA Desktop IDE.
///
/// Provides a clean gate for Windows-specific IDE features without
/// conflating "wide mobile layout" with "desktop IDE".
library;

import 'package:flutter/foundation.dart';

/// Whether the current platform is a Windows desktop IDE target.
///
/// This is a **platform gate**, not a width gate. It returns true only for
/// native Windows builds (not web, not mobile, not macOS, not Linux).
/// Use this to conditionally render the Desktop IDE shell and routes.
bool get isDesktopIde => !kIsWeb && defaultTargetPlatform == TargetPlatform.windows;

/// Where AURA lands after sign-in and onboarding. The Windows desktop app is
/// the IDE and nothing else; every other build is the one AURA app, which
/// opens on Copilot.
String landingRouteFor({required bool desktop}) => desktop ? '/ide' : '/chat';

/// The landing route for the platform this build is running on.
String get appLandingRoute => landingRouteFor(desktop: isDesktopIde);

/// Whether the current platform can run the desktop IDE (Windows only for D1–D3).
///
/// Future phases may widen this to include macOS/Linux when parity is ready.
bool get canRunDesktopIde => isDesktopIde;