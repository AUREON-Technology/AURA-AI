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
