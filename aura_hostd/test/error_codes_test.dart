// Every refusal and error this daemon can show a person carries a stable machine `code` next to
// its English text (Arabic plan stage 3, decision 12): the desktop app translates by code and
// falls back to the English for a code it does not know. These tests pin both halves -- each
// path produces one of `HostdErrorCode`'s codes, and no error response or error frame can be
// written without one.
import 'dart:io';

import 'package:aura_hostd/src/command_policy.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/error_codes.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  group('codes', () {
    test('are snake_case and unique', () {
      final pattern = RegExp(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$');
      for (final code in HostdErrorCode.all) {
        expect(pattern.hasMatch(code), isTrue, reason: code);
      }
      // A Set literal would silently collapse two constants sharing one value.
      final declared = RegExp(r"static const \w+ = '([a-z0-9_]+)';")
          .allMatches(File('lib/src/error_codes.dart').readAsStringSync())
          .map((m) => m.group(1))
          .toList();
      expect(declared.toSet().length, declared.length, reason: 'two constants share a code');
      expect(declared.toSet(), HostdErrorCode.all);
    });
  });

  group('command policy verdicts', () {
    // One command per verdict branch of evaluateCommand, with the code it must carry.
    final cases = <(String, bool, bool, CommandDecision, String)>[
      ('rm -rf /', true, true, CommandDecision.deny, HostdErrorCode.commandHardDenied),
      ('rm -rf /', false, true, CommandDecision.deny, HostdErrorCode.commandHardDenied),
      ('rm -r ../sibling', true, true, CommandDecision.deny,
          HostdErrorCode.destructivePathOutsideWorkspace),
      ('cd .. && rm -r build', true, true, CommandDecision.deny,
          HostdErrorCode.destructiveAfterLeavingWorkspace),
      ('npm install', true, true, CommandDecision.allow, HostdErrorCode.workspaceTrusted),
      ('git status && ls', false, true, CommandDecision.deny, HostdErrorCode.untrustedShellSyntax),
      ('git status', false, true, CommandDecision.allow, HostdErrorCode.readOnlyAllowlisted),
      ('npm install', false, true, CommandDecision.needsApproval,
          HostdErrorCode.commandNeedsApproval),
      ('npm install', false, false, CommandDecision.deny, HostdErrorCode.commandNotAllowlisted),
    ];
    for (final (command, trusted, queue, decision, code) in cases) {
      test('"$command" (trusted: $trusted, queue: $queue) -> $code', () {
        final verdict = evaluateCommand(command, workspaceTrusted: trusted, allowApprovalQueue: queue);
        expect(verdict.decision, decision, reason: verdict.reason);
        expect(verdict.code, code);
        expect(verdict.reason, isNotEmpty, reason: 'the English text stays for older apps');
      });
    }

    test('write verdicts carry codes', () {
      expect(evaluateWrite(workspaceTrusted: true).code, HostdErrorCode.workspaceTrusted);
      expect(evaluateWrite(workspaceTrusted: false).code, HostdErrorCode.writeNeedsApproval);
    });

    test('protected git writes carry codes, and the English-only helper is unchanged', () {
      const hook = '/work/repo/.git/hooks/pre-commit';
      const config = '/work/repo/.git/config';
      expect(protectedGitWriteRefusal(hook)?.code, HostdErrorCode.protectedGitHooks);
      expect(protectedGitWriteRefusal(config)?.code, HostdErrorCode.protectedGitConfig);
      expect(protectedGitWriteReason(hook), protectedGitWriteRefusal(hook)?.reason);
      expect(protectedGitWriteRefusal('/work/repo/src/main.dart'), isNull);
    });

    test('destructiveEscapeReason still returns the English text alone', () {
      expect(destructiveEscapeReason('rm -r ../x'), contains('outside the workspace'));
      expect(destructiveEscapeReason('rm -r build'), isNull);
    });
  });

  group('InvalidRootError', () {
    late Directory tempDir;
    setUp(() => tempDir = Directory.systemTemp.createTempSync('aura_hostd_codes_test_'));
    tearDown(() => tempDir.deleteSync(recursive: true));

    String codeOf(String path) {
      try {
        WorkspaceRoots(const []).addRoot(path);
      } on InvalidRootError catch (error) {
        expect(error.message, isNotEmpty);
        return error.code;
      }
      fail('$path was granted');
    }

    test('a missing path', () {
      expect(codeOf(p.join(tempDir.path, 'missing')), HostdErrorCode.rootNotADirectory);
    });

    test('a file, not a directory', () {
      final file = File(p.join(tempDir.path, 'f.txt'))..writeAsStringSync('x');
      expect(codeOf(file.path), HostdErrorCode.rootNotADirectory);
    });

    test('a filesystem root', () {
      expect(codeOf(p.rootPrefix(tempDir.absolute.path)), HostdErrorCode.rootIsFilesystemRoot);
    });

    test('a denylisted folder', () {
      if (!Directory('/etc').existsSync()) return; // no /etc on this platform
      expect(codeOf('/etc'), HostdErrorCode.rootDenylisted);
    });

    test('a containment violation', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(tempDir.path);
      expect(
        () => roots.requireContained(root, '../elsewhere'),
        throwsA(isA<ContainmentViolation>()
            .having((e) => e.code, 'code', HostdErrorCode.outsideWorkspace)),
      );
    });
  });

  group('no error leaves the daemon without a code', () {
    test('control_server.dart writes every error body through _fail', () {
      final source = File('lib/src/control_server.dart').readAsStringSync();
      // The only `'error':` literal left is _fail's own body; every other error goes through it,
      // and _respond asserts the pair at runtime as well.
      expect(RegExp(r"'error':").allMatches(source).length, 1);
      expect(source, contains("{'error': message, 'code': code}"));
      // The pairing status's `reason` is the one non-error field shown to a person.
      expect(source, contains("'reason': reason,\n          'code': HostdErrorCode.pairingEnded"));
    });

    test('terminal and debug error frames take a code', () {
      for (final path in ['lib/src/terminal_session_manager.dart', 'lib/src/debug_session_manager.dart']) {
        final source = File(path).readAsStringSync();
        final calls = RegExp(r'_sendError\(\s*socket,\s*([^,]+),').allMatches(source).toList();
        expect(calls, isNotEmpty, reason: path);
        for (final call in calls) {
          expect(call.group(1), anyOf(startsWith('HostdErrorCode.'), 'error.code'), reason: path);
        }
        expect(source, contains("'code': code,"), reason: path);
      }
    });
  });
}
