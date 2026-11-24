import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_confirmation_card.dart';
import 'package:aura_client/features/chat/presentation/widgets/github_branch_summary.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_file_view.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_terminal_view.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';

class _SeededPending extends PendingConfirmation {
  _SeededPending(this._seed);
  final List<PendingToolCall> _seed;

  @override
  List<PendingToolCall> build() => _seed;
}

Future<void> _pump(WidgetTester tester, List<PendingToolCall> pending, {String? turnId}) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        pendingConfirmationProvider.overrideWith(() => _SeededPending(pending)),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        home: Scaffold(body: EngineerConfirmationCard(turnId: turnId)),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('renders nothing when there are no pending calls', (tester) async {
    await _pump(tester, const []);
    expect(find.byType(EngineerConfirmationCard), findsOneWidget);
    expect(find.text('Confirm action'), findsNothing);
  });

  testWidgets('write_file dispatches to SandboxFileView', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'write_file',
        arguments: {'path': 'main.py', 'content': 'print(1)'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.byType(SandboxFileView), findsOneWidget);
    expect(find.text('main.py'), findsOneWidget);
  });

  testWidgets('run_command dispatches to SandboxTerminalView', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'run_command',
        arguments: {'command': 'pytest -q'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.byType(SandboxTerminalView), findsOneWidget);
  });

  testWidgets('run_command is titled "Run a command": it may run on your computer, not only in a '
      'sandbox (WP8)', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'run_command',
        arguments: {'command': 'pytest -q'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.text(AppLocalizationsEn().chatConfirmRunCommand), findsOneWidget);
    expect(find.text('Run command in sandbox'), findsNothing);
  });

  testWidgets('edit_file dispatches to SandboxDiffView', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'edit_file',
        arguments: {'path': 'a.py', 'find': 'old', 'replace': 'new'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.byType(SandboxDiffView), findsOneWidget);
  });

  testWidgets('git_commit with paths lists exactly the files it will commit', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'c1',
        tool: 'git_commit',
        arguments: {'message': 'fix parser', 'paths': ['src/parser.py', 'tests/test_parser.py']},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.text('fix parser'), findsOneWidget);
    expect(find.text('src/parser.py'), findsOneWidget);
    expect(find.text('tests/test_parser.py'), findsOneWidget);
  });

  testWidgets('github_create_branch dispatches to GithubBranchSummary', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'github_create_branch',
        arguments: {'branch': 'feature/x', 'from_ref': 'main'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.byType(GithubBranchSummary), findsOneWidget);
    expect(find.textContaining('main → feature/x'), findsOneWidget);
  });

  testWidgets('a commit with a real diffPreview renders SandboxDiffView, not CodePreview', (
    tester,
  ) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'github_commit_files',
        arguments: {
          'branch': 'main',
          'message': 'fix bug',
          'files': {'a.py': 'print(1)'},
        },
        diffPreview: '-print(0)\n+print(1)',
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.byType(SandboxDiffView), findsOneWidget);
  });

  testWidgets('only pending calls for the given turnId are shown', (tester) async {
    await _pump(tester, [
      const PendingToolCall(callId: 'step_a', tool: 'run_command', turnId: 't1'),
      const PendingToolCall(callId: 'step_b', tool: 'run_command', turnId: 't2'),
    ], turnId: 't1');
    // Only turn t1's call renders -- t2's must not leak into this message's card.
    expect(find.byType(SandboxTerminalView), findsOneWidget);
  });

  testWidgets('turnId null shows every pending call regardless of turn (live-voice mode)', (
    tester,
  ) async {
    await _pump(tester, [
      const PendingToolCall(callId: 'step_a', tool: 'run_command', turnId: 't1'),
      const PendingToolCall(callId: 'step_b', tool: 'run_command', turnId: 't2'),
    ]);
    expect(find.byType(SandboxTerminalView), findsNWidgets(2));
  });

  testWidgets('an unrecognized tool falls back to the generic summary', (tester) async {
    await _pump(tester, [
      const PendingToolCall(
        callId: 'step_s1',
        tool: 'some_future_tool',
        arguments: {'foo': 'bar'},
        turnId: 't1',
      ),
    ], turnId: 't1');
    expect(find.text('some_future_tool'), findsOneWidget);
    expect(find.textContaining('foo: bar'), findsOneWidget);
  });
}
