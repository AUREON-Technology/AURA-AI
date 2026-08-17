import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/features/chat/presentation/widgets/citation_chip.dart';
import 'package:aura_client/features/chat/presentation/widgets/tool_call_chip.dart';
import 'package:aura_client/features/chat/presentation/widgets/persona_badge.dart';
import 'package:aura_client/features/chat/presentation/widgets/streaming_message_skeleton.dart';
import 'package:aura_client/core/design_system/design_system.dart';

Widget _wrap(Widget child) {
  return MaterialApp(home: Scaffold(body: child));
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
  });

  group('ToolCallChips', () {
    testWidgets('renders nothing for an empty call list', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [])));
      expect(find.byType(AnimatedBuilder), findsNothing);
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
