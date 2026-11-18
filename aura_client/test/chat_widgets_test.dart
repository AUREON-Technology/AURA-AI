import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/citation_chip.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:aura_client/features/chat/presentation/widgets/tool_call_chip.dart';
import 'package:aura_client/features/chat/presentation/widgets/persona_badge.dart';
import 'package:aura_client/features/chat/presentation/widgets/streaming_message_skeleton.dart';
import 'package:aura_client/features/chat/presentation/widgets/turn_status_row.dart';
import 'package:aura_client/features/chat/presentation/widgets/user_facing_citations.dart';
import 'package:aura_client/core/design_system/design_system.dart';

/// The bubble's "is this the latest turn" lookup, answered without a database (F2): unknown, so
/// a failed turn keeps its Retry.
Override _latestTurnUnknown() =>
    latestAssistantMessageIdProvider.overrideWith((ref, _) => Stream<String?>.value(null));

Widget _wrap(Widget child) {
  return MaterialApp(
    theme: AuraThemeV2.light,
    home: Scaffold(body: child),
  );
}

void main() {
  group('CitationChips', () {
    testWidgets('renders nothing for an empty citation list', (tester) async {
      await tester.pumpWidget(_wrap(const CitationChips(citations: [])));
      expect(find.byType(Tooltip), findsNothing);
    });

    testWidgets('renders one chip per citation label', (tester) async {
      await tester.pumpWidget(_wrap(const CitationChips(citations: ['doc-a (page 1)', 'doc-b (page 4 > Heading)'])));
      expect(find.text('doc-a (page 1)'), findsOneWidget);
      expect(find.text('doc-b (page 4 > Heading)'), findsOneWidget);
      expect(find.byIcon(Icons.description_outlined), findsNWidgets(2));
    });

    testWidgets('hides internal provenances and keeps document citations', (tester) async {
      await tester.pumpWidget(_wrap(const CitationChips(citations: [
        'episodic_summary',
        'working_memory',
        'cag',
        'memory:session',
        'goal:hydrate',
        'tool:search_knowledge',
        'handbook@v1',
      ])));
      expect(find.text('handbook@v1'), findsOneWidget);
      expect(find.text('episodic_summary'), findsNothing);
      expect(find.text('working_memory'), findsNothing);
      expect(find.text('cag'), findsNothing);
      expect(find.text('memory:session'), findsNothing);
      expect(find.text('goal:hydrate'), findsNothing);
      expect(find.text('tool:search_knowledge'), findsNothing);
      expect(find.byIcon(Icons.description_outlined), findsOneWidget);
    });
  });

  group('userFacingCitations', () {
    test('drops bookkeeping labels and prefix provenances', () {
      expect(
        userFacingCitations([
          'episodic_summary',
          'working_memory',
          'cag',
          'memory:x',
          'goal:y',
          'tool:z',
          'handbook@v1',
        ]),
        ['handbook@v1'],
      );
    });
  });

  group('TurnStatusRow', () {
    testWidgets('does not list raw step kind/status lines', (tester) async {
      await tester.pumpWidget(_wrap(TurnStatusRow(
        meta: const TurnMeta(
          steps: [
            TurnStep(kind: 'generate', status: 'failed', label: 'generate'),
            TurnStep(kind: 'generate', status: 'running', label: 'generate'),
          ],
        ),
        hasCitations: false,
      )));
      expect(find.textContaining('generate'), findsNothing);
      expect(find.textContaining('failed'), findsNothing);
      expect(find.textContaining('running'), findsNothing);
    });

    testWidgets('degraded and empty search uses library-empty copy', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, searchedButEmpty: true),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.libraryEmptyCopy), findsOneWidget);
      expect(find.text(TurnStatusRow.reducedModeCopy), findsNothing);
    });

    testWidgets('degraded only shows a short reduced-mode line', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.reducedModeCopy), findsOneWidget);
      expect(find.text(TurnStatusRow.libraryEmptyCopy), findsNothing);
    });
  });

  group('MessageBubble', () {
    testWidgets('empty finished assistant turn shows a retry card', (tester) async {
      final message = Message(
        id: 'asst-empty',
        conversationId: 'conv-1',
        role: 'assistant',
        content: '   ',
        createdAt: DateTime.utc(2026, 1, 1),
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [_latestTurnUnknown()],
          child: _wrap(MessageBubble(message: message, isUser: false)),
        ),
      );
      expect(find.text('No reply this turn'), findsOneWidget);
      expect(find.text('Retry'), findsOneWidget);
      expect(find.byType(MarkdownBody), findsNothing);
    });

    testWidgets(
      'empty turn awaiting confirmation shows a hint, not the plain retry card',
      (tester) async {
        final message = Message(
          id: 'asst-confirm',
          conversationId: 'conv-1',
          role: 'assistant',
          content: '',
          createdAt: DateTime.utc(2026, 1, 1),
          toolCallsJson: '[{"name":"propose_health_log","arguments":{},"settled":true}]',
          citationsJson: '["handbook@v1"]',
          turnMetaJson: const TurnMeta(needsConfirmation: true, guardsComplete: true).encode(),
        );
        await tester.pumpWidget(
          ProviderScope(
            overrides: [
              // The held call is on offer for this turn (the bubble's id stands in for its turn id).
              pendingConfirmationProvider.overrideWith(() => _SeededPending(const [
                    PendingToolCall(tool: 'propose_health_log', turnId: 'asst-confirm', approvalId: 'apr.1'),
                  ])),
            ],
            child: _wrap(MessageBubble(message: message, isUser: false)),
          ),
        );
        expect(find.text('No reply this turn'), findsNothing);
        expect(find.text('Retry'), findsNothing);
        // The card is below the hint, so the hint says "below" (G-14a).
        expect(find.text('Confirm the action below to continue.'), findsOneWidget);
        expect(find.byType(ToolCallChips), findsOneWidget);
        expect(find.byType(CitationChips), findsOneWidget);
      },
    );

    testWidgets(
      'empty turn without a pending confirmation still shows the plain retry card',
      (tester) async {
        final message = Message(
          id: 'asst-empty-2',
          conversationId: 'conv-1',
          role: 'assistant',
          content: '',
          createdAt: DateTime.utc(2026, 1, 1),
          turnMetaJson: const TurnMeta(needsConfirmation: false).encode(),
        );
        await tester.pumpWidget(
          ProviderScope(
            overrides: [_latestTurnUnknown()],
            child: _wrap(MessageBubble(message: message, isUser: false)),
          ),
        );
        expect(find.text('No reply this turn'), findsOneWidget);
        expect(find.text('Retry'), findsOneWidget);
      },
    );
  });

  group('ToolCallChips', () {
    testWidgets('renders nothing for an empty call list', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [])));
      // Scoped to descendants of ToolCallChips: MaterialApp/Scaffold have their
      // own unrelated AnimatedBuilder internally, so an unscoped find.byType
      // search matches those too and is not what this test means to assert.
      expect(
        find.descendant(of: find.byType(ToolCallChips), matching: find.byType(AnimatedBuilder)),
        findsNothing,
      );
    });

    testWidgets('renders a title-cased label for a known tool', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [
        {'name': 'search_knowledge', 'arguments': {}, 'settled': true},
      ])));
      expect(find.text('Search Knowledge'), findsOneWidget);
      expect(find.byIcon(Icons.search), findsOneWidget);
    });

    testWidgets('falls back to a generic icon for an unrecognised tool name', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [
        {'name': 'some_future_tool', 'arguments': {}, 'settled': true},
      ])));
      expect(find.text('Some Future Tool'), findsOneWidget);
      expect(find.byIcon(Icons.build_outlined), findsOneWidget);
    });

    testWidgets('an unsettled call does not throw and disposes its animation cleanly', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [
        {'name': 'calculator', 'arguments': {}, 'settled': false},
      ])));
      await tester.pump(const Duration(milliseconds: 100));
      // Swap it out for an empty list to force disposal of the pulsing AnimationController.
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [])));
      expect(tester.takeException(), isNull);
    });
  });

  group('PersonaBadge', () {
    testWidgets('renders the given label and icon', (tester) async {
      await tester.pumpWidget(_wrap(const PersonaBadge(label: 'Orchestrator', icon: Icons.hub_outlined)));
      expect(find.text('Orchestrator'), findsOneWidget);
      expect(find.byIcon(Icons.hub_outlined), findsOneWidget);
    });
  });

  group('StreamingMessageSkeleton', () {
    testWidgets('renders two shimmer lines', (tester) async {
      await tester.pumpWidget(_wrap(const StreamingMessageSkeleton()));
      expect(find.byType(AuraSkeleton), findsNWidgets(2));
    });
  });
}

class _SeededPending extends PendingConfirmation {
  _SeededPending(this._seed);
  final List<PendingToolCall> _seed;

  @override
  List<PendingToolCall> build() => _seed;
}
