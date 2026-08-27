import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// 64-bar ring waveform around the orb (Phase H).
class AuraVoiceWaveform extends StatelessWidget {
  const AuraVoiceWaveform({
    super.key,
    required this.amplitude,
    required this.phase,
  });

  final double amplitude;
  final String phase;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final reduce = MediaQuery.disableAnimationsOf(context);
    return CustomPaint(
      size: const Size(280, 280),
      painter: _WavePainter(
        amplitude: reduce ? 0 : amplitude,
        phase: phase,
        color: palette.brand,
        staticMode: reduce,
      ),
    );
  }
}

class _WavePainter extends CustomPainter {
  _WavePainter({
    required this.amplitude,
    required this.phase,
    required this.color,
    required this.staticMode,
  });

  final double amplitude;
  final String phase;
  final Color color;
  final bool staticMode;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.shortestSide * 0.42;
    const bars = 64;
    final paint = Paint()
      ..color = color.withValues(alpha: 0.55)
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;

    for (var i = 0; i < bars; i++) {
      final a = (i / bars) * math.pi * 2;
      final band = staticMode
          ? 0.15
          : (0.12 +
              amplitude *
                  (0.55 +
                      0.45 *
                          math.sin(i * 0.35 + amplitude * 8) *
                          (phase == 'speaking' ? 1.2 : 1.0)));
      final inner = radius;
      final outer = radius + 18 * band;
      final p0 = center + Offset(math.cos(a) * inner, math.sin(a) * inner);
      final p1 = center + Offset(math.cos(a) * outer, math.sin(a) * outer);
      canvas.drawLine(p0, p1, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _WavePainter oldDelegate) {
    return oldDelegate.amplitude != amplitude ||
        oldDelegate.phase != phase ||
        oldDelegate.staticMode != staticMode;
  }
}
