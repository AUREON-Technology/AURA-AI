/// Says so when AURA planned a step and then answered without finishing it.
///
/// `TurnMeta.plan` is the plan as it stood when execution ended, with a final
/// status per step (`pending`/`ready`/`running`/`succeeded`/`failed`/
/// `skipped`/`blocked`). That is different from `TurnMeta.steps`, which
/// `StepLogPanel` renders: steps are the live chronological progress events,
/// and they say what happened, not what was supposed to.
///
/// So a turn can plan four steps, run two, and answer anyway — and until now
/// nothing in the UI distinguished that from a turn where everything ran. The
/// kernel already treats this as a hazard worth a fix of its own ("a planned
/// step that never ran must not be answered around"); this is the user-facing
/// half of the same concern.
///
/// Deliberately silent on the happy path. A step that `succeeded` needs no
/// comment, and neither does one the planner chose to `skip` — that is a
/// decision, not an omission. Only an unfinished or failed step opens this.
library;

import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';
import '../../data/turn_meta.dart';

/// Statuses that mean the step finished, as planned or as deliberately
/// dropped. Anything else left the plan incomplete when the answer was given.
const _settledStatuses = {'succeeded', 'skipped'};

bool planStepSettled(PlanStep step) {
  final status = step.status?.toLowerCase();
  // An absent status is not evidence of a problem; an older backend, or a
  // shape this client does not know, should not raise an alarm.
  if (status == null || status.isEmpty) return true;
  return _settledStatuses.contains(status);
}

/// The steps worth showing: everything that did not succeed, so a skipped step
/// appears for completeness once the notice is open, but never opens it alone.
List<PlanStep> unfinishedPlanSteps(List<PlanStep> plan) {
  return plan.where((s) {
    final status = s.status?.toLowerCase();
    return status != null && status.isNotEmpty && status != 'succeeded';
  }).toList();
}

bool planHasGaps(List<PlanStep> plan) => plan.any((s) => !planStepSettled(s));

class IncompletePlanNotice extends StatelessWidget {
  const IncompletePlanNotice({super.key, required this.plan});

  final List<PlanStep> plan;

  @override
  Widget build(BuildContext context) {
    if (!planHasGaps(plan)) return const SizedBox.shrink();

    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final shown = unfinishedPlanSteps(plan);
    final headline = l10n.planIncompleteHeadline(plan.length, shown.length);

    return Semantics(
      liveRegion: true,
      label: '$headline ${l10n.planIncompleteAnnouncement}',
      child: Padding(
        padding: const EdgeInsets.only(top: AuraSpacing.sm),
        child: AuraCard(
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.md,
            vertical: AuraSpacing.sm,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Icon(Icons.rule, size: 14, color: palette.warning),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      headline,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
              for (final step in shown)
                Padding(
                  // Directional, not physical. There is no Arabic UI yet —
                  // only Arabic TTS (GAP-020) — but `rtl_layout_test.dart`
                  // scans lib/ for exactly this, so that adding a locale is a
                  // translation file rather than a hunt through every inset.
                  padding: const EdgeInsetsDirectional.only(
                    top: AuraSpacing.xs,
                    start: 20,
                  ),
                  child: Text(
                    // The backend's own status word, not a word of ours: there
                    // is a real difference between blocked, failed and never
                    // started, and flattening it would lose the useful part.
                    l10n.planStepWithStatus(_stepLabel(step, l10n), step.status ?? ''),
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: palette.textSecondary,
                        ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  /// The step's own description, or its kind when the planner gave none.
  static String _stepLabel(PlanStep step, AppLocalizations l10n) {
    final description = step.description?.trim();
    if (description != null && description.isNotEmpty) return description;
    return step.kind ?? l10n.chatStepFallback;
  }
}
