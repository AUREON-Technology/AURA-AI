// F22: a memory save tells the user when it is only queued on this device, or refused, and says
// nothing extra when the server confirmed it.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/features/chat/data/memory_repository.dart';
import 'package:aura_client/features/memory/presentation/memory_save_feedback.dart';
import 'package:aura_client/l10n/l10n.dart';

Future<BuildContext> _pump(WidgetTester tester) async {
  late BuildContext captured;
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: Scaffold(body: Builder(builder: (context) {
      captured = context;
      return const SizedBox();
    })),
  ));
  return captured;
}

void main() {
  testWidgets('a confirmed save shows nothing extra', (tester) async {
    final context = await _pump(tester);
    showMemorySaveOutcome(context, MemorySaveOutcome.saved, 'likes tea');
    await tester.pump();
    expect(find.byType(SnackBar), findsNothing);
  });

  testWidgets('a queued save says it is kept on this device', (tester) async {
    final context = await _pump(tester);
    showMemorySaveOutcome(context, MemorySaveOutcome.queued, 'likes tea');
    await tester.pump();
    expect(find.text(context.l10n.memorySaveQueued), findsOneWidget);
  });

  testWidgets('a refused save names the fact', (tester) async {
    final context = await _pump(tester);
    showMemorySaveOutcome(context, MemorySaveOutcome.refused, 'likes tea');
    await tester.pump();
    expect(find.text(context.l10n.memorySaveRefused('likes tea')), findsOneWidget);
  });
}
