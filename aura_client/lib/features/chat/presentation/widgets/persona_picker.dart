import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/agent_config_repository.dart';
import '../../data/agents_repository.dart';

class PersonaChip extends ConsumerWidget {
  /// When true, the label may ellipsize if a parent [Flexible] is tight.
  /// Do not apply a fixed max width — wrap the chip instead.
  final bool shrink;

  const PersonaChip({super.key, this.shrink = false});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final activeId = ref.watch(activeAgentIdProvider);
    final (label, icon) = personaDisplay(activeId);

    final labelStyle = TextStyle(fontSize: 13, color: palette.textPrimary);
    final labelText = Text(
      label,
      maxLines: 1,
      overflow: shrink ? TextOverflow.ellipsis : TextOverflow.visible,
      style: labelStyle,
    );

    return InkWell(
      borderRadius: BorderRadius.circular(AuraRadius.pill),
      onTap: () => showPersonaPicker(context),
      child: Container(
        padding: EdgeInsets.symmetric(
          horizontal: shrink ? AuraSpacing.sm : AuraSpacing.md,
          vertical: 6,
        ),
        decoration: BoxDecoration(
          color: palette.surfaceVariant,
          borderRadius: BorderRadius.circular(AuraRadius.pill),
          border: Border.all(color: palette.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16, color: palette.brandOnSurfaceText),
            const SizedBox(width: AuraSpacing.xs),
            if (shrink) Flexible(child: labelText) else labelText,
            const SizedBox(width: AuraSpacing.xs),
            Icon(Icons.expand_more, size: 16, color: palette.textSecondary),
          ],
        ),
      ),
    );
  }
}

void showPersonaPicker(BuildContext context) {
  showModalBottomSheet(
    context: context,
    backgroundColor: Colors.transparent,
    isScrollControlled: true,
    builder: (sheetContext) => const PersonaPickerSheet(),
  );
}

/// Scrollable persona list. Default half-height sheets overflowed by ~158px
/// on SM-A366B when all 7 agents with subtitles were laid out in a Column.
class PersonaPickerSheet extends ConsumerWidget {
  const PersonaPickerSheet({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final agentsAsync = ref.watch(agentsProvider);
    final activeId = ref.watch(activeAgentIdProvider);
    final maxHeight = MediaQuery.sizeOf(context).height * 0.75;

    return SafeArea(
      child: Align(
        alignment: Alignment.bottomCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxHeight),
          child: Container(
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(AuraRadius.xl),
              ),
              border: Border.all(color: palette.border),
            ),
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Choose a persona',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: AuraSpacing.sm),
                Flexible(
                  child: agentsAsync.when(
                    data: (agents) => ListView.builder(
                      shrinkWrap: true,
                      itemCount: agents.length,
                      itemBuilder: (context, index) {
                        final agent = agents[index];
                        final (label, icon) = personaDisplay(agent.id);
                        final isActive = agent.id == activeId;
                        return AuraSelectTile(
                          icon: icon,
                          title: label,
                          subtitle: agent.description.isEmpty
                              ? null
                              : agent.description,
                          selected: isActive,
                          trailing: isActive
                              ? Icon(
                                  Icons.check,
                                  color: palette.brandOnSurfaceText,
                                )
                              : null,
                          onTap: () {
                            ref
                                .read(activeAgentIdProvider.notifier)
                                .setAgent(agent.id);
                            Navigator.pop(context);
                          },
                        );
                      },
                    ),
                    loading: () => const Padding(
                      padding: EdgeInsets.all(AuraSpacing.xl),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                    error: (e, _) => Padding(
                      padding: const EdgeInsets.all(AuraSpacing.xl),
                      child: Text(
                        'Could not load personas: $e',
                        style: TextStyle(color: palette.danger),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
