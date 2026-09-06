import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/turn_meta.dart';

/// Live progress for a turn's plan/tool steps (`TurnMeta.steps`).
///
/// The data has flowed from the backend's `step` SSE frames since the turn feature shipped
/// (`ChatRepository` already appends every `TurnStep` it sees) -- nothing rendered it. Shown for
/// any turn with steps, not just Engineer-agent ones: the data is the same shape for every
/// plan-executing turn, and there is no client-visible "which agent answered this" signal to
/// gate on that would be worth the extra plumbing.
class StepLogPanel extends StatelessWidget {
  const StepLogPanel({super.key, required this.steps});

  final List<TurnStep> steps;

  @override
  Widget build(BuildContext context) {
    if (steps.isEmpty) return const SizedBox.shrink();
    final palette = AuraPalette.of(context);

    return AuraCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.md,
        vertical: AuraSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < steps.length; i++) ...[
            if (i > 0) const SizedBox(height: AuraSpacing.xs),
            _StepRow(step: steps[i], palette: palette),
          ],
        ],
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({required this.step, required this.palette});

  final TurnStep step;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 16,
          height: 16,
          child: _statusIcon(),
        ),
        const SizedBox(width: AuraSpacing.sm),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                step.label ?? step.kind ?? 'step',
                style: TextStyle(fontSize: 13, color: palette.textPrimary),
              ),
              if (step.status == 'failed' &&
                  step.detail != null &&
                  step.detail!.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    step.detail!,
                    style: TextStyle(fontSize: 12, color: palette.danger),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _statusIcon() {
    switch (step.status) {
      case 'running':
        return CircularProgressIndicator(strokeWidth: 2, color: palette.brand);
      case 'succeeded':
        return Icon(Icons.check_circle, size: 16, color: palette.success);
      case 'failed':
        return Icon(Icons.error, size: 16, color: palette.danger);
      case 'skipped':
        return Icon(Icons.arrow_forward, size: 16, color: palette.textSecondary);
      case 'blocked':
        return Icon(Icons.block, size: 16, color: palette.warning);
      default:
        return Icon(Icons.circle_outlined, size: 16, color: palette.textSecondary);
    }
  }
}
