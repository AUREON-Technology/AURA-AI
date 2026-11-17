import 'package:flutter/material.dart';

import '../tokens.dart';

/// Entrance animation: the child fades in while rising [offset] logical
/// pixels, starting [delay] after it is first built.
///
/// The delay lives inside one controller's [Interval] rather than a
/// `Future.delayed`, so there is never a pending timer for a widget test to
/// trip over, and a widget disposed mid-delay simply stops. It plays once:
/// a rebuild does not replay it. With the platform's reduce-motion setting on,
/// the child is shown as-is.
class AuraEntrance extends StatefulWidget {
  final Widget child;
  final Duration delay;
  final Duration duration;
  final double offset;

  const AuraEntrance({
    super.key,
    required this.child,
    this.delay = Duration.zero,
    this.duration = AuraMotion.emphasized,
    this.offset = 12,
  });

  /// The delay for the [index]th item of a list that enters one after another.
  /// Capped, so a long list doesn't keep its last rows waiting.
  static Duration stagger(
    int index, {
    Duration step = const Duration(milliseconds: 50),
  }) {
    final capped = index.clamp(0, 8);
    return step * capped;
  }

  @override
  State<AuraEntrance> createState() => _AuraEntranceState();
}

class _AuraEntranceState extends State<AuraEntrance>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _progress;

  @override
  void initState() {
    super.initState();
    final total = widget.delay + widget.duration;
    _controller = AnimationController(vsync: this, duration: total);
    final start = total.inMicroseconds == 0
        ? 0.0
        : widget.delay.inMicroseconds / total.inMicroseconds;
    _progress = CurvedAnimation(
      parent: _controller,
      curve: Interval(start, 1, curve: AuraMotion.standardCurve),
    );
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_controller.isAnimating || _controller.isCompleted) return;
    if (MediaQuery.maybeDisableAnimationsOf(context) ?? false) {
      _controller.value = 1;
    } else {
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _progress,
      child: widget.child,
      builder: (context, child) {
        final t = _progress.value;
        return Opacity(
          opacity: t,
          child: Transform.translate(
            offset: Offset(0, (1 - t) * widget.offset),
            child: child,
          ),
        );
      },
    );
  }
}

/// Press feedback: the child scales down slightly while a pointer is held on
/// it and springs back on release. Purely visual: it never handles the tap,
/// so the child's own `InkWell`/`onTap` and semantics are untouched.
class AuraPressScale extends StatefulWidget {
  final Widget child;
  final double pressedScale;

  const AuraPressScale({
    super.key,
    required this.child,
    this.pressedScale = 0.97,
  });

  @override
  State<AuraPressScale> createState() => _AuraPressScaleState();
}

class _AuraPressScaleState extends State<AuraPressScale> {
  bool _down = false;

  void _set(bool down) {
    if (_down != down) setState(() => _down = down);
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerDown: (_) => _set(true),
      onPointerUp: (_) => _set(false),
      onPointerCancel: (_) => _set(false),
      child: AnimatedScale(
        scale: _down ? widget.pressedScale : 1,
        duration: AuraMotion.respecting(context, AuraMotion.fast),
        curve: AuraMotion.standardCurve,
        child: widget.child,
      ),
    );
  }
}

/// A gentle "breathing" scale for a hero mark (the brand tile on Copilot's
/// empty state, onboarding): a few slow breaths after it appears, then still.
/// Finite on purpose -- an endless loop would cost battery on a screen people
/// leave open, and would never let a widget test's `pumpAndSettle` settle.
/// Off entirely under reduce-motion.
class AuraBreathe extends StatefulWidget {
  final Widget child;
  final double amplitude;
  final Duration period;

  const AuraBreathe({
    super.key,
    required this.child,
    this.amplitude = 0.04,
    this.period = const Duration(milliseconds: 2800),
  });

  @override
  State<AuraBreathe> createState() => _AuraBreatheState();
}

class _AuraBreatheState extends State<AuraBreathe>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: widget.period,
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduced = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (reduced) {
      _controller.stop();
      _controller.value = 0;
    } else if (!_controller.isAnimating) {
      _controller.repeat(reverse: true, count: 4);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      child: widget.child,
      builder: (context, child) {
        final t = Curves.easeInOut.transform(_controller.value);
        return Transform.scale(scale: 1 + widget.amplitude * t, child: child);
      },
    );
  }
}
