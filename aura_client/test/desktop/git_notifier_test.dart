/// Coverage for the Git panel's state notifier (D5): fetch, pull, push and "Publish branch"
/// (decision D-01), which had no tests. Every daemon outcome the panel can receive -- success, a
/// structured `ok: false` reason, a thrown daemon error -- is driven through a fake control client
/// and checked as the message the person would read.
library;

import 'dart:async';

import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/git_notifier.dart';
import 'package:aura_client/l10n/generated/app_localizations_ar.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter_test/flutter_test.dart';

/// Answers only what the notifier calls, with canned results, and records every call in order.
class _FakeHostd extends HostdControlClient {
  Map<String, dynamic> statusBody = {
    'branch': 'main',
    'upstream': 'origin/main',
    'ahead': 0,
    'behind': 0,
    'files': <Map<String, dynamic>>[],
  };
  Map<String, dynamic> fetchResult = {'ok': true};
  Map<String, dynamic> pullResult = {'ok': true};
  Map<String, dynamic> pushResult = {'ok': true};
  Map<String, dynamic> publishResult = {'ok': true};
  Map<String, dynamic> checkoutResult = {'ok': true};
  Map<String, dynamic> remotesResult = {'remotes': <String>['origin']};
  Object? remotesError;
  Object? pushError;

  /// When set, [gitPush] waits for it -- lets a test hold an operation open.
  Completer<Map<String, dynamic>>? pushGate;

  final calls = <String>[];

  @override
  Future<Map<String, dynamic>> gitStatus({required String root}) async {
    calls.add('status');
    return statusBody;
  }

  @override
  Future<Map<String, dynamic>> gitBranches({required String root}) async => {'branches': <dynamic>[]};

  @override
  Future<Map<String, dynamic>> gitFetch({required String root}) async {
    calls.add('fetch');
    return fetchResult;
  }

  @override
  Future<Map<String, dynamic>> gitPull({required String root}) async {
    calls.add('pull');
    return pullResult;
  }

  @override
  Future<Map<String, dynamic>> gitPush({required String root}) async {
    calls.add('push');
    if (pushError != null) throw pushError!;
    return pushGate != null ? pushGate!.future : pushResult;
  }

  @override
  Future<Map<String, dynamic>> gitCheckout({required String root, required String branch, bool create = false}) async {
    calls.add('checkout:$branch:${create ? 'create' : 'switch'}');
    return checkoutResult;
  }

  @override
  Future<Map<String, dynamic>> gitRemotes({required String root}) async {
    calls.add('remotes');
    if (remotesError != null) throw remotesError!;
    return remotesResult;
  }

  @override
  Future<Map<String, dynamic>> gitPublish({required String root, required String remote}) async {
    calls.add('publish:$remote');
    return publishResult;
  }
}

void main() {
  late _FakeHostd hostd;
  late GitNotifier notifier;

  setUp(() async {
    setCurrentL10n(null);
    hostd = _FakeHostd();
    notifier = GitNotifier(IdeEventBus(), hostd);
    await notifier.refresh('w1', '/repo');
    hostd.calls.clear();
  });

  group('refresh', () {
    test('reads the branch, its upstream and how far it is ahead or behind', () async {
      hostd.statusBody = {...hostd.statusBody, 'branch': 'feature', 'upstream': null, 'ahead': 2, 'behind': 1};

      await notifier.refresh('w1', '/repo');

      expect(notifier.state.branch, 'feature');
      expect(notifier.state.upstream, isNull);
      expect(notifier.state.ahead, 2);
      expect(notifier.state.behind, 1);
      expect(notifier.state.error, isNull);
    });
  });

  group('fetch, pull and push', () {
    test('a successful one refreshes the status and leaves no error', () async {
      await notifier.fetch();
      await notifier.pull();
      await notifier.push();

      expect(hostd.calls, ['fetch', 'status', 'pull', 'status', 'push', 'status']);
      expect(notifier.state.error, isNull);
      expect(notifier.state.busy, isFalse);
    });

    test('creating a branch whose name is taken says so, and stays on screen (GT-02)', () async {
      hostd.checkoutResult = {'ok': false, 'reason': 'branch_exists'};

      await notifier.checkout('develop', create: true);

      expect(notifier.state.error, currentL10n.gitBranchExists('develop'));
      expect(notifier.state.error, isNot(currentL10n.gitCheckoutOverwrite),
          reason: 'it is not a local-changes problem and must not be reported as one');
      expect(hostd.calls, contains('checkout:develop:create'));
    });

    test('each structured failure reaches the person as its own message, and stays on screen', () async {
      // Regression: the refresh that follows every operation replaces the whole state, which used to
      // wipe the message before the panel showed it.
      final l10n = currentL10n;
      final cases = <(String, Map<String, dynamic>, String)>[
        ('pull', {'ok': false, 'reason': 'no_upstream'}, l10n.gitNoUpstream),
        ('pull', {'ok': false, 'reason': 'diverged'}, l10n.gitDiverged),
        ('pull', {'ok': false, 'reason': 'auth_failed'}, l10n.gitAuthFailed),
        (
          'pull',
          {
            'ok': false,
            'reason': 'local_changes_would_be_overwritten',
            'affected_files': ['a.txt', 'b.txt'],
          },
          l10n.gitLocalChangesOverwrittenIn('a.txt, b.txt'),
        ),
        (
          'pull',
          {'ok': false, 'reason': 'local_changes_would_be_overwritten'},
          l10n.gitLocalChangesOverwritten,
        ),
        ('push', {'ok': false, 'reason': 'no_upstream'}, l10n.gitNoUpstream),
        ('push', {'ok': false, 'reason': 'diverged'}, l10n.gitDiverged),
        ('push', {'ok': false, 'reason': 'auth_failed'}, l10n.gitAuthFailed),
        ('push', {'ok': false, 'reason': 'something_new'}, l10n.gitRemoteSyncFailed),
        ('fetch', {'ok': false, 'reason': 'auth_failed'}, l10n.gitAuthFailed),
      ];
      for (final (op, result, expected) in cases) {
        switch (op) {
          case 'pull':
            hostd.pullResult = result;
            await notifier.pull();
          case 'push':
            hostd.pushResult = result;
            await notifier.push();
          default:
            hostd.fetchResult = result;
            await notifier.fetch();
        }
        expect(notifier.state.error, expected, reason: '$op $result');
        expect(notifier.state.busy, isFalse, reason: '$op $result');
      }
    });

    test('the no-upstream message points at Publish branch', () {
      expect(currentL10n.gitNoUpstream, contains(currentL10n.gitPublishBranch));
    });

    test('a daemon error (including a timeout) becomes the message and releases the busy state', () async {
      hostd.pushError = HostdControlError(500, 'git push timed out after 300s');

      await notifier.push();

      expect(notifier.state.error, 'git push timed out after 300s');
      expect(notifier.state.busy, isFalse);
    });

    test('a daemon that is not running becomes the message', () async {
      hostd.pushError = HostdControlUnavailable('aura_hostd is not running');

      await notifier.push();

      expect(notifier.state.error, 'aura_hostd is not running');
      expect(notifier.state.busy, isFalse);
    });

    test('a second click while one is running does nothing', () async {
      hostd.pushGate = Completer<Map<String, dynamic>>();

      final first = notifier.push();
      await Future<void>.delayed(Duration.zero);
      expect(notifier.state.busy, isTrue);
      await notifier.push();
      await notifier.pull();
      expect(hostd.calls.where((c) => c == 'push'), hasLength(1));
      expect(hostd.calls, isNot(contains('pull')));

      hostd.pushGate!.complete({'ok': true});
      await first;
      expect(notifier.state.busy, isFalse);
    });
  });

  group('Publish branch (D-01)', () {
    test('loadRemotes returns the configured remotes without an error', () async {
      hostd.remotesResult = {'remotes': ['origin', 'upstream']};

      expect(await notifier.loadRemotes(), ['origin', 'upstream']);
      expect(notifier.state.error, isNull);
    });

    test('a repository with no remote says so instead of offering an empty choice', () async {
      hostd.remotesResult = {'remotes': <String>[]};

      expect(await notifier.loadRemotes(), isEmpty);
      expect(notifier.state.error, currentL10n.gitNoRemote);
    });

    test('remotes that cannot be read give null and the daemon\'s message', () async {
      hostd.remotesError = HostdControlError(409, 'not a git repository');

      expect(await notifier.loadRemotes(), isNull);
      expect(notifier.state.error, 'not a git repository');
    });

    test('publishing sends the chosen remote and refreshes the status', () async {
      await notifier.publish('upstream');

      expect(hostd.calls, ['publish:upstream', 'status']);
      expect(notifier.state.error, isNull);
      expect(notifier.state.busy, isFalse);
    });

    test('each way it can be refused reaches the person as its own message', () async {
      final l10n = currentL10n;
      final cases = <(String, String)>[
        ('unknown_remote', l10n.gitPublishUnknownRemote),
        ('detached_head', l10n.gitPublishDetachedHead),
        ('no_commits', l10n.gitPublishNoCommits),
        ('auth_failed', l10n.gitAuthFailed),
        // The remote already has that branch name with other commits: not the pull/push wording.
        ('diverged', l10n.gitPublishDiverged),
      ];
      for (final (reason, expected) in cases) {
        hostd.publishResult = {'ok': false, 'reason': reason};
        await notifier.publish('origin');
        expect(notifier.state.error, expected, reason: reason);
      }
      expect(l10n.gitPublishDiverged, isNot(l10n.gitDiverged));
    });

    test('publishing cannot start while another operation is running', () async {
      hostd.pushGate = Completer<Map<String, dynamic>>();
      final push = notifier.push();
      await Future<void>.delayed(Duration.zero);

      await notifier.publish('origin');

      expect(hostd.calls, isNot(contains('publish:origin')));
      hostd.pushGate!.complete({'ok': true});
      await push;
    });
  });

  group('the new messages exist in Arabic as well as English', () {
    test('each is translated text, and the placeholders arrive in both', () {
      final en = AppLocalizationsEn();
      final ar = AppLocalizationsAr();
      final pairs = <(String, String)>[
        (en.gitAuthFailed, ar.gitAuthFailed),
        (en.gitPublishTooltip, ar.gitPublishTooltip),
        (en.gitPublishAction, ar.gitPublishAction),
        (en.gitNoRemote, ar.gitNoRemote),
        (en.gitPublishUnknownRemote, ar.gitPublishUnknownRemote),
        (en.gitPublishDetachedHead, ar.gitPublishDetachedHead),
        (en.gitPublishNoCommits, ar.gitPublishNoCommits),
        (en.gitPublishDiverged, ar.gitPublishDiverged),
        (en.gitPushTooltip, ar.gitPushTooltip),
        (en.gitNoUpstream, ar.gitNoUpstream),
      ];
      for (final (english, arabic) in pairs) {
        expect(arabic, isNotEmpty);
        expect(arabic, isNot(english));
      }
      for (final l10n in [en, ar]) {
        expect(l10n.gitPublishConfirm('feature/x', 'origin'), allOf(contains('feature/x'), contains('origin')));
        expect(l10n.gitPublishChooseRemote('feature/x'), contains('feature/x'));
      }
    });

    test('Push no longer promises "current branch only" (D-03)', () {
      expect(AppLocalizationsEn().gitPushTooltip.toLowerCase(), isNot(contains('current branch')));
      expect(AppLocalizationsEn().gitPushTooltip.toLowerCase(), contains('git configuration'));
    });
  });
}
