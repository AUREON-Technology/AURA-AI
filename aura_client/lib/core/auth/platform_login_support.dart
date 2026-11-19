/// Whether `google_sign_in` has a real plugin implementation on this platform (ADR-011 Unit 7).
/// False only for Linux and Windows -- android/ios/macOS/web all have working implementations.
/// Extracted as its own testable function following the precedent
/// `features/chat/presentation/widgets/chat_header_overflow_menu.dart::chatHeaderShowsLiveVoice`
/// already set for this kind of platform allowlist.
library;

import 'package:flutter/foundation.dart';

bool supportsNativeGoogleSignIn() {
  if (kIsWeb) return true;
  return defaultTargetPlatform == TargetPlatform.android ||
      defaultTargetPlatform == TargetPlatform.iOS ||
      defaultTargetPlatform == TargetPlatform.macOS;
}

/// Whether this platform can attest the device in a way the backend verifies: Play Integrity on
/// Android, App Attest on iOS (`aura_backend/services/security.py`, which refuses any other
/// platform). Native sign-in on these goes through the attested `/api/v3/auth/token`; everything
/// else -- the web, macOS -- signs in at `/consumer/token`, which needs no attestation (INT-103).
bool platformHasDeviceAttestation({bool isWeb = kIsWeb, TargetPlatform? platform}) {
  if (isWeb) return false;
  final target = platform ?? defaultTargetPlatform;
  return target == TargetPlatform.android || target == TargetPlatform.iOS;
}
