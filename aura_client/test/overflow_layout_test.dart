import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/persona_picker.dart';

const _phoneSize = Size(360, 640);

List<AgentInfo> _sevenPersonas() {
  return const [
    AgentInfo(
      id: 'orchestrator',
      description: 'Routes work across every domain of your life.',
      version: '1',
    ),
    AgentInfo(
      id: 'task_planner',
      description: 'Breaks goals into dated, prioritized tasks.',
      version: '1',
    ),
    AgentInfo(
      id: 'finance_advisor',
      description: 'Tracks spend, income, and budget tradeoffs.',
      version: '1',
    ),
    AgentInfo(
      id: 'health_companion',
      description: 'Logs water, sleep, exercise, and recovery.',
      version: '1',
    ),
    AgentInfo(
      id: 'research_agent',
      description: 'Searches knowledge and cites sources.',
      version: '1',
    ),
    AgentInfo(
      id: 'coding_agent',
      description: 'Explains code and drafts implementations.',
      version: '1',
    ),
    AgentInfo(
      id: 'decision_agent',
      description: 'Weighs options and recommends a next step.',
      version: '1',
    ),
  ];
}

void main() {
  testWidgets('persona picker sheet does not overflow on a 360x640 phone', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(_phoneSize);
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          agentsProvider.overrideWith((ref) async => _sevenPersonas()),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          home: const Scaffold(body: PersonaPickerSheet()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Choose a persona'), findsOneWidget);
    expect(find.text('Orchestrator'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'section header with a long title ellipsizes instead of overflowing',
    (tester) async {
      await tester.binding.setSurfaceSize(_phoneSize);
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(
        MaterialApp(
          theme: AuraThemeV2.dark,
          home: const Scaffold(
            body: Padding(
              padding: EdgeInsets.all(16),
              child: SizedBox(
                width: 200,
                child: AuraSectionHeader(
                  title:
                      'A very long section title that would overflow a narrow card',
                  actionLabel: 'View all',
                  onAction: _noop,
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );
}

void _noop() {}
