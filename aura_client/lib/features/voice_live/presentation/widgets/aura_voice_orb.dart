import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// Amplitude-reactive orb — first CustomPainter in the app (Phase H).
class AuraVoiceOrb extends StatefulWidget {
  const AuraVoiceOrb({
    super.key,
    required this.phase,
    required this.amplitude,
  });

  final String phase;
  final double amplitude;

  @override
  State<AuraVoiceOrb> createState() => _AuraVoiceOrbState();
}

class _AuraVoiceOrbState extends State<AuraVoiceOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _tick;

  @override
  void initState() {
    super.initState();
    _tick = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
  }

  @override
  void dispose() {
    _tick.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final reduce = MediaQuery.disableAnimationsOf(context);
    if (reduce) {
      return CustomPaint(
        size: const Size(220, 220),
        painter: _OrbPainter(
          phase: widget.phase,
          amplitude: 0,
          t: 0,
          palette: palette,
          staticMode: true,
        ),
      );
    }
    return AnimatedBuilder(
      animation: _tick,
      builder: (context, _) {
        return CustomPaint(
          size: const Size(220, 220),
          painter: _OrbPainter(
            phase: widget.phase,
            amplitude: widget.amplitude,
            t: _tick.value,
            palette: palette,
            staticMode: false,
          ),
        );
      },
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter({
    required this.phase,
    required this.amplitude,
    required this.t,
    required this.palette,
    required this.staticMode,
  });

  final String phase;
  final double amplitude;
  final double t;
  final AuraPalette palette;
  final bool staticMode;

  Shader? _cached;
  Size? _cachedSize;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final base = size.shortestSide * 0.36;
    final breath = staticMode
        ? 1.0
        : (phase == 'connecting'
            ? 0.98 + 0.02 * math.sin(t * math.pi * 2 * (8 / 4))
            : 1.0);
    final amp = amplitude.clamp(0.0, 1.0);
    final deform = phase == 'listening' || phase == 'speaking' ? 0.08 * amp : 0.04;

    final path = Path();
    const steps = 64;
    for (var i = 0; i <= steps; i++) {
      final a = (i / steps) * math.pi * 2;
      final wobble = staticMode
          ? 0.0
          : deform *
              (math.sin(a * 3 + t * math.pi * 2 * 1.1) +
                  0.6 * math.sin(a * 5 - t * math.pi * 2 * 0.7) +
                  0.35 * math.sin(a * 2 + t * math.pi * 2 * 1.7));
      final r = base * breath * (1 + wobble) * (1 + 0.12 * amp);
      final p = center + Offset(math.cos(a) * r, math.sin(a) * r);
      if (i == 0) {
        path.moveTo(p.dx, p.dy);
      } else {
        path.lineTo(p.dx, p.dy);
      }
    }
    path.close();

    if (_cached == null || _cachedSize != size) {
      _cached = palette.brandGradient.createShader(Offset.zero & size);
      _cachedSize = size;
    }
    final fill = Paint()..shader = _cached;
    if (phase == 'connecting') {
      fill.colorFilter = const ColorFilter.matrix(<double>[
        0.6, 0.3, 0.1, 0, 0,
        0.3, 0.6, 0.1, 0, 0,
        0.2, 0.2, 0.5, 0, 0,
        0, 0, 0, 1, 0,
      ]);
    }
    canvas.drawPath(path, fill);

    // Specular highlight
    final highlight = Paint()
      ..shader = RadialGradient(
        colors: [
          Colors.white.withValues(alpha: 0.35),
          Colors.white.withValues(alpha: 0.0),
        ],
      ).createShader(
        Rect.fromCircle(
          center: center.translate(-base * 0.25, -base * 0.3),
          radius: base * 0.55,
        ),
      );
    canvas.drawCircle(
      center.translate(-base * 0.2, -base * 0.25),
      base * 0.45,
      highlight,
    );

    if (!staticMode && phase == 'thinking') {
      final sweep = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3
        ..shader = SweepGradient(
          startAngle: t * math.pi * 2 * 1.2,
          colors: [
            palette.brand.withValues(alpha: 0.0),
            palette.brand.withValues(alpha: 0.8),
            palette.brand.withValues(alpha: 0.0),
          ],
        ).createShader(Rect.fromCircle(center: center, radius: base * 1.15));
      canvas.drawCircle(center, base * 1.15, sweep);
    }

    if (!staticMode && phase == 'speaking' && amp > 0.15) {
      final ring = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = palette.brand.withValues(alpha: 0.35 * amp);
      canvas.drawCircle(center, base * (1.2 + 0.25 * amp), ring);
    }
  }

  @override
  bool shouldRepaint(covariant _OrbPainter oldDelegate) {
    return oldDelegate.phase != phase ||
        oldDelegate.amplitude != amplitude ||
        oldDelegate.t != t ||
        oldDelegate.staticMode != staticMode;
  }
}
