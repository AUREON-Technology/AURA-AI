// aura_hostd sends a stable `code` next to every English refusal/error (aura_hostd's
// error_codes.dart). The app shows the translation for a code it knows and falls back to the
// daemon's English for an older daemon (no code) or a code it does not know yet.
//
// Imports hostd_control_client_io.dart directly (VM-only test), as hostd_control_client_io_test
// does.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/hostd/hostd_control_client_io.dart';
import 'package:aura_client/core/hostd/hostd_error_codes.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

final AppLocalizations _ar = lookupAppLocalizations(const Locale('ar'));
final AppLocalizations _en = lookupAppLocalizations(const Locale('en'));

/// Every code aura_hostd can send for an error a person sees, copied from
/// aura_hostd/lib/src/error_codes.dart (minus the two "allowed" verdict codes, never shown as an
/// error) -- a code missing from the mapping would silently fall back to English.
const _daemonErrorCodes = [
  'command_hard_denied',
  'destructive_path_outside_workspace',
  'destructive_after_leaving_workspace',
  'untrusted_shell_syntax',
  'command_needs_approval',
  'command_not_allowlisted',
  'write_needs_approval',
  'protected_git_hooks',
  'protected_git_config',
  'root_not_a_directory',
  'root_is_filesystem_root',
  'root_denylisted',
  'outside_workspace',
  'unauthorized',
  'no_such_endpoint',
  'internal_error',
  'missing_parameter',
  'unknown_action',
  'root_not_granted',
  'path_not_found',
  'file_not_utf8',
  'approval_not_found',
  'not_a_git_repository',
  'git_output_not_utf8',
  'git_command_failed',
  'unknown_diagnostics_tool',
  'task_not_found',
  'pairing_failed',
  'no_pairing_session',
  'pairing_ended',
  'workspace_not_trusted',
  'terminal_start_failed',
  'debug_start_failed',
  'debug_unreachable',
];

void main() {
  group('hostdCodeMessage', () {
    test('a known code shows the Arabic translation, not the daemon English', () {
      const english = '/home/me/proj is not a granted root';
      final shown = hostdCodeMessage('root_not_granted', english, l10n: _ar);
      expect(shown, _ar.hostdCodeRootNotGranted);
      expect(shown, isNot(english));
      expect(shown, contains('مساحات الشغل'));
    });

    test('a code that wraps an underlying error quotes it untranslated', () {
      expect(
        hostdCodeMessage('git_command_failed', 'fatal: bad revision', l10n: _ar),
        'Git فشل: fatal: bad revision',
      );
      // A terminal/debug frame sends the underlying error alone as `detail`.
      expect(
        hostdCodeMessage(
          'terminal_start_failed',
          'failed to start terminal: ProcessException: no pty',
          detail: 'ProcessException: no pty',
          l10n: _en,
        ),
        "Couldn't start the terminal: ProcessException: no pty",
      );
    });

    test('an unknown code falls back to the daemon text', () {
      expect(hostdCodeMessage('some_future_code', 'raw daemon text', l10n: _ar), 'raw daemon text');
    });

    test('no code (a daemon that predates codes) falls back to the daemon text', () {
      expect(hostdCodeMessage(null, 'raw daemon text', l10n: _ar), 'raw daemon text');
    });

    test('every daemon error code has a translation in Arabic and English', () {
      const fallback = 'FALLBACK';
      for (final code in _daemonErrorCodes) {
        final ar = hostdCodeMessage(code, fallback, l10n: _ar);
        final en = hostdCodeMessage(code, fallback, l10n: _en);
        expect(ar, isNot(startsWith(fallback)), reason: code);
        expect(en, isNot(startsWith(fallback)), reason: code);
        expect(ar, isNot(en), reason: code);
      }
    });
  });

  group('HostdControlClient', () {
    late HttpServer httpServer;
    late Map<String, Object?> errorBody;

    setUp(() async {
      httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      unawaited(() async {
        await for (final request in httpServer) {
          await utf8.decoder.bind(request).join();
          request.response.statusCode = 404;
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode(errorBody));
          await request.response.close();
        }
      }());
      setCurrentL10n(_ar);
    });

    tearDown(() async {
      setCurrentL10n(null);
      await httpServer.close(force: true);
    });

    HostdControlClient client() => HostdControlClient(
          discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 'tok'),
        );

    test('an error with a known code reaches the UI in Arabic, code kept', () async {
      errorBody = {'error': '/w is not a granted root', 'code': 'root_not_granted'};
      await expectLater(
        client().gitStatus(root: '/w'),
        throwsA(isA<HostdControlError>()
            .having((e) => e.message, 'message', _ar.hostdCodeRootNotGranted)
            .having((e) => e.code, 'code', 'root_not_granted')
            .having((e) => e.statusCode, 'statusCode', 404)),
      );
    });

    test('an error from an older daemon (no code) keeps its English text', () async {
      errorBody = {'error': '/w is not a granted root'};
      await expectLater(
        client().gitStatus(root: '/w'),
        throwsA(isA<HostdControlError>()
            .having((e) => e.message, 'message', '/w is not a granted root')
            .having((e) => e.code, 'code', isNull)),
      );
    });

    test('an error with an unknown code keeps its English text', () async {
      errorBody = {'error': 'something new', 'code': 'brand_new_code'};
      await expectLater(
        client().gitStatus(root: '/w'),
        throwsA(isA<HostdControlError>().having((e) => e.message, 'message', 'something new')),
      );
    });
  });
}
