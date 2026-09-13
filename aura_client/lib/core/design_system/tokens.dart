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

  /// Returns a scaled spacing instance for IDE density.
  /// Uses the same API but multiplies all values by [scale].
  static AuraSpacing _scaled(double scale) => _ScaledAuraSpacing(scale);
}

class _ScaledAuraSpacing extends AuraSpacing {
  final double _scale;

  const _ScaledAuraSpacing(this._scale) : super._();

  double get xs => (AuraSpacing.xs * _scale).roundToDouble();
  double get sm => (AuraSpacing.sm * _scale).roundToDouble();
  double get md => (AuraSpacing.md * _scale).roundToDouble();
  double get lg => (AuraSpacing.lg * _scale).roundToDouble();
  double get xl => (AuraSpacing.xl * _scale).roundToDouble();
  double get xxl => (AuraSpacing.xxl * _scale).roundToDouble();
  double get xxxl => (AuraSpacing.xxxl * _scale).roundToDouble();
}

class AuraRadius {
  AuraRadius._();
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double pill = 999;
}

class AuraElevation {
  AuraElevation._();
  static const double none = 0;
  static const double low = 2;
  static const double medium = 6;
  static const double high = 12;
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
