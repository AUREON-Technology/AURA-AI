import 'package:flutter/material.dart';

/// Spacing scale — always use these instead of ad hoc SizedBox/padding
/// values, so rhythm stays consistent across every screen.
class AuraSpacing {
  const AuraSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
  static const double xxxl = 48;

  /// Largest step on the scale. Reserved for the vertical rhythm of
  /// full-page empty states and onboarding, where 48 reads as cramped
  /// against a whole viewport of nothing.
  static const double xxxxl = 64;
}

class AuraRadius {
  AuraRadius._();
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double pill = 999;

  /// Semantic aliases. A dialog and a bottom sheet should not have to
  /// remember *which* numeric step they agreed on — naming the surface is
  /// what keeps two dialogs written six months apart the same shape.
  static const double dialog = lg;
  static const double sheet = xl;
}

class AuraElevation {
  AuraElevation._();
  static const double none = 0;
  static const double low = 2;
  static const double medium = 6;
  static const double high = 12;

  /// Semantic names for the same four steps, describing what the surface
  /// *is* rather than how much shadow it has: content sitting flat on the
  /// background, a card resting on it, a menu raised above the content it
  /// covers, a dialog floating over everything.
  static const double flat = none;
  static const double resting = low;
  static const double raised = medium;
  static const double floating = high;
}

/// Motion tokens. Every animated transition in the app should reference one
/// of these instead of hand-picking a duration/curve, and every animation
/// site must check [AuraMotion.reduced] before playing non-essential motion.
class AuraMotion {
  AuraMotion._();
  static const Duration fast = Duration(milliseconds: 120);
  static const Duration standard = Duration(milliseconds: 200);
  static const Duration emphasized = Duration(milliseconds: 400);

  static const Curve standardCurve = Curves.easeOutCubic;
  static const Curve emphasizedCurve = Curves.easeInOutCubic;

  /// Returns [Duration.zero] when the platform requests reduced motion,
  /// otherwise [duration]. Call at every animation call site:
  /// `AuraMotion.respecting(context, AuraMotion.standard)`.
  static Duration respecting(BuildContext context, Duration duration) {
    return MediaQuery.of(context).disableAnimations ? Duration.zero : duration;
  }
}
