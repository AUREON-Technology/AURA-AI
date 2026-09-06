import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/persona_picker.dart';

const _phoneSize = Size(360, 640);

class _OrchestratorAgent extends ActiveAgent {
  @override
  String build() => 'orchestrator';
}

void main() {
  testWidgets('a persona the caller cannot use renders locked and its tap is a no-op', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(_phoneSize);
    addTearDown(() => tester.binding.setSurfaceSize(null));

    final container = ProviderContainer(
      overrides: [
        agentsProvider.overrideWith((ref) async => const [
              AgentInfo(
                id: 'orchestrator',
                description: 'Routes work across every domain of your life.',
                version: '1',
              ),
              AgentInfo(
                id: 'coding_agent',
                description: 'Explains code and drafts implementations.',
                version: '1',
                usable: false,
                requiredCapabilities: {'engineer_agent'},
              ),
            ]),
        activeAgentIdProvider.overrideWith(_OrchestratorAgent.new),
      ],
    );
    addTearDown(container.dispose);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          home: const Scaffold(body: PersonaPickerSheet()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // The locked persona shows a lock icon and an explanatory subtitle naming the gate.
    expect(find.byIcon(Icons.lock_outline), findsOneWidget);
    expect(find.textContaining('Requires'), findsOneWidget);
    expect(find.textContaining('Engineer capability'), findsOneWidget);

    // Tapping it must not select it -- `IgnorePointer` around the locked tile means this tap
    // doesn't even hit-test the widget, which is itself proof the tile is truly disabled, not
    // just visually greyed out (hence `warnIfMissed: false`: the miss is the expected behavior).
    await tester.tap(find.text('Coding Agent'), warnIfMissed: false);
    await tester.pumpAndSettle();
    expect(container.read(activeAgentIdProvider), 'orchestrator');
    expect(tester.takeException(), isNull);
  });
}
