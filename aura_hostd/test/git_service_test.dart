// Exercises GitService against a real local git repository (no mocking of Process.run) --
// covering the status/diff/log parsers across the git states D5's plan verification section
// calls out (clean, modified, staged, untracked, renamed, detached HEAD, empty repo, non-repo)
// plus the write operations and checkout's structured failure mode.
import 'dart:io';

import 'package:aura_hostd/src/git_service.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

Future<ProcessResult> _git(String cwd, List<String> args) =>
    Process.run('git', args, workingDirectory: cwd);

void main() {
  late Directory repoDir;
  late GitService git;

  setUp(() async {
    repoDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_');
    await _git(repoDir.path, ['init', '-q', '-b', 'main']);
    await _git(repoDir.path, ['config', 'user.name', 'Test User']);
    await _git(repoDir.path, ['config', 'user.email', 'test@example.com']);
    git = GitService();
  });

  tearDown(() {
    repoDir.deleteSync(recursive: true);
  });

  Future<void> commitFile(String name, String content, {String message = 'commit'}) async {
    File(p.join(repoDir.path, name)).writeAsStringSync(content);
    await _git(repoDir.path, ['add', '--', name]);
    await _git(repoDir.path, ['commit', '-q', '-m', message]);
  }

  /// Sets up a real bare "remote" repository with one commit, and a real clone of it -- for tests
  /// that need fetch/pull/push to talk to a second, genuinely separate repository rather than
  /// exercising only the no-remote/no-upstream failure paths the rest of this group covers.
  Future<(Directory remote, Directory clone)> setUpRemoteWithClone() async {
    final remoteDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_remote_');
    addTearDown(() => remoteDir.deleteSync(recursive: true));
    await _git(remoteDir.path, ['init', '-q', '--bare', '-b', 'main']);

    final seedDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_seed_');
    addTearDown(() => seedDir.deleteSync(recursive: true));
    await _git(seedDir.path, ['init', '-q', '-b', 'main']);
    await _git(seedDir.path, ['config', 'user.name', 'Test User']);
    await _git(seedDir.path, ['config', 'user.email', 'test@example.com']);
    File(p.join(seedDir.path, 'seed.txt')).writeAsStringSync('seed');
    await _git(seedDir.path, ['add', '--', 'seed.txt']);
    await _git(seedDir.path, ['commit', '-q', '-m', 'seed commit']);
    await _git(seedDir.path, ['remote', 'add', 'origin', remoteDir.path]);
    await _git(seedDir.path, ['push', '-q', 'origin', 'main']);

    final cloneDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_clone_');
    addTearDown(() => cloneDir.deleteSync(recursive: true));
    await _git(Directory.systemTemp.path, ['clone', '-q', remoteDir.path, cloneDir.path]);
    await _git(cloneDir.path, ['config', 'user.name', 'Test User']);
    await _git(cloneDir.path, ['config', 'user.email', 'test@example.com']);

    return (remoteDir, cloneDir);
  }

  group('status', () {
    test('a repo with no commits yet reports a null-safe state, not a crash', () async {
      final status = await git.status(repoDir.path);
      expect(status['files'], isEmpty);
    });

    test('a clean repo reports no files and the branch name', () async {
      await commitFile('a.txt', 'hello');
      final status = await git.status(repoDir.path);
      expect(status['branch'], 'main');
      expect(status['files'], isEmpty);
    });

    test('a modified, unstaged file is reported with worktree status M', () async {
      await commitFile('a.txt', 'hello');
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('changed');
      final status = await git.status(repoDir.path);
      final files = status['files'] as List;
      expect(files.single['path'], 'a.txt');
      expect(files.single['worktree_status'], 'M');
      expect(files.single['staged'], isFalse);
    });

    test('a staged new file is reported with index status A and staged true', () async {
      await commitFile('a.txt', 'hello');
      File(p.join(repoDir.path, 'b.txt')).writeAsStringSync('new');
      await _git(repoDir.path, ['add', 'b.txt']);
      final status = await git.status(repoDir.path);
      final files = (status['files'] as List).cast<Map<String, Object?>>();
      final entry = files.firstWhere((f) => f['path'] == 'b.txt');
      expect(entry['index_status'], 'A');
      expect(entry['staged'], isTrue);
    });

    test('an untracked file is reported with status ?? and staged false', () async {
      await commitFile('a.txt', 'hello');
      File(p.join(repoDir.path, 'c.txt')).writeAsStringSync('untracked');
      final status = await git.status(repoDir.path);
      final files = (status['files'] as List).cast<Map<String, Object?>>();
      final entry = files.firstWhere((f) => f['path'] == 'c.txt');
      expect(entry['index_status'], '?');
      expect(entry['staged'], isFalse);
    });

    test('a renamed, staged file reports renamed_from', () async {
      await commitFile('old.txt', 'content');
      await _git(repoDir.path, ['mv', 'old.txt', 'new.txt']);
      final status = await git.status(repoDir.path);
      final files = (status['files'] as List).cast<Map<String, Object?>>();
      expect(files.single['path'], 'new.txt');
      expect(files.single['renamed_from'], 'old.txt');
    });

    test('detached HEAD reports a null branch instead of crashing', () async {
      await commitFile('a.txt', 'hello');
      final sha = (await _git(repoDir.path, ['rev-parse', 'HEAD'])).stdout.toString().trim();
      await _git(repoDir.path, ['checkout', '-q', sha]);
      final status = await git.status(repoDir.path);
      expect(status['branch'], isNull);
    });

    test('a non-git directory throws GitNotARepositoryError', () async {
      final plainDir = Directory.systemTemp.createTempSync('aura_hostd_not_a_repo_');
      addTearDown(() => plainDir.deleteSync(recursive: true));
      expect(() => git.status(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
    });
  });

  group('diff', () {
    test('workingTree scope shows an unstaged change; staged scope does not', () async {
      await commitFile('a.txt', 'hello\n');
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('changed\n');
      final workingTree = await git.diff(repoDir.path, scope: GitDiffScope.workingTree);
      final staged = await git.diff(repoDir.path, scope: GitDiffScope.staged);
      expect(workingTree['diff'], contains('changed'));
      expect(staged['diff'], isEmpty);
    });

    test('staged scope shows a staged change; workingTree scope does not (once staged)', () async {
      await commitFile('a.txt', 'hello\n');
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('changed\n');
      await _git(repoDir.path, ['add', 'a.txt']);
      final staged = await git.diff(repoDir.path, scope: GitDiffScope.staged);
      final workingTree = await git.diff(repoDir.path, scope: GitDiffScope.workingTree);
      expect(staged['diff'], contains('changed'));
      expect(workingTree['diff'], isEmpty);
    });
  });

  group('log', () {
    test('an empty repo (no commits yet) returns an empty commit list, not an error', () async {
      final log = await git.log(repoDir.path);
      expect(log['commits'], isEmpty);
    });

    test('lists commits with sha/author/date/subject', () async {
      await commitFile('a.txt', 'hello', message: 'first commit');
      final log = await git.log(repoDir.path);
      final commits = (log['commits'] as List).cast<Map<String, Object?>>();
      expect(commits, hasLength(1));
      expect(commits.single['subject'], 'first commit');
      expect(commits.single['sha'], isNotEmpty);
    });
  });

  group('stage / unstage / commit', () {
    test('stage then commit records exactly what was staged, not the whole working tree', () async {
      await commitFile('a.txt', 'hello');
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('staged change');
      File(p.join(repoDir.path, 'untracked.txt')).writeAsStringSync('should not be committed');
      await git.stage(repoDir.path, [p.join(repoDir.path, 'a.txt')]);
      final result = await git.commit(repoDir.path, 'stage exactly what was staged');
      expect(result['ok'], true);
      final status = await git.status(repoDir.path);
      final files = (status['files'] as List).cast<Map<String, Object?>>();
      expect(files.map((f) => f['path']), contains('untracked.txt'));
      expect(files.map((f) => f['path']), isNot(contains('a.txt')));
    });

    test('commit with nothing staged returns ok:false, reason nothing_staged, not an exception', () async {
      await commitFile('a.txt', 'hello');
      final result = await git.commit(repoDir.path, 'empty');
      expect(result['ok'], false);
      expect(result['reason'], 'nothing_staged');
    });

    test('unstage moves a staged file back to unstaged', () async {
      await commitFile('a.txt', 'hello');
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('changed');
      await git.stage(repoDir.path, [p.join(repoDir.path, 'a.txt')]);
      await git.unstage(repoDir.path, [p.join(repoDir.path, 'a.txt')]);
      final status = await git.status(repoDir.path);
      final files = (status['files'] as List).cast<Map<String, Object?>>();
      expect(files.single['staged'], isFalse);
    });
  });

  group('checkout', () {
    test('create: true creates and switches to a new branch', () async {
      await commitFile('a.txt', 'hello');
      final result = await git.checkout(repoDir.path, 'feature-x', create: true);
      expect(result['ok'], true);
      final status = await git.status(repoDir.path);
      expect(status['branch'], 'feature-x');
    });

    test('switching branches with uncommitted changes that would be overwritten returns a structured failure',
        () async {
      await commitFile('a.txt', 'hello');
      await _git(repoDir.path, ['checkout', '-q', '-b', 'other']);
      await commitFile('a.txt', 'other-branch-version', message: 'diverge');
      await _git(repoDir.path, ['checkout', '-q', 'main']);
      File(p.join(repoDir.path, 'a.txt')).writeAsStringSync('uncommitted local edit');

      final result = await git.checkout(repoDir.path, 'other');
      expect(result['ok'], false);
      expect(result['reason'], 'local_changes_would_be_overwritten');
      expect(result['affected_files'], contains('a.txt'));
    });
  });

  group('worktrees (D8 Part B)', () {
    test('createWorktree makes a real, separate checkout on its own branch', () async {
      await commitFile('a.txt', 'hello');

      final result = await git.createWorktree(repoDir.path, taskId: 'task1');
      expect(result['branch'], 'aura/task-task1');
      final worktreePath = result['worktree_path'] as String;
      expect(Directory(worktreePath).existsSync(), isTrue);
      expect(File(p.join(worktreePath, 'a.txt')).existsSync(), isTrue);

      // A write inside the worktree must not touch the parent checkout -- that's the entire
      // point of isolation.
      File(p.join(worktreePath, 'a.txt')).writeAsStringSync('changed inside the task worktree');
      expect(File(p.join(repoDir.path, 'a.txt')).readAsStringSync(), 'hello');

      final listResult = await _git(repoDir.path, ['worktree', 'list']);
      // `git worktree list` normalizes to forward slashes on Windows regardless of the path
      // style passed to `worktree add` -- compare on the slash-normalized form, not the raw
      // platform path.
      expect(listResult.stdout.replaceAll('\\', '/'), contains(worktreePath.replaceAll('\\', '/')));
    });

    test('createWorktree branches from an explicit baseBranch, not just HEAD', () async {
      await commitFile('a.txt', 'on main');
      await _git(repoDir.path, ['checkout', '-q', '-b', 'feature']);
      await commitFile('b.txt', 'on feature');
      await _git(repoDir.path, ['checkout', '-q', 'main']);

      final result = await git.createWorktree(repoDir.path, taskId: 'task2', baseBranch: 'feature');
      final worktreePath = result['worktree_path'] as String;
      expect(File(p.join(worktreePath, 'b.txt')).existsSync(), isTrue);
    });

    test('createWorktree excludes .aura-workspaces from the parent repo\'s own git status', () async {
      await commitFile('a.txt', 'hello');
      await git.createWorktree(repoDir.path, taskId: 'task3');

      final excludeFile = File(p.join(repoDir.path, '.git', 'info', 'exclude'));
      expect(excludeFile.readAsStringSync(), contains('.aura-workspaces/'));

      final status = await git.status(repoDir.path);
      expect(status['files'], isEmpty);
    });

    test('removeWorktree tears down both the worktree directory and its branch', () async {
      await commitFile('a.txt', 'hello');
      final created = await git.createWorktree(repoDir.path, taskId: 'task4');
      final worktreePath = created['worktree_path'] as String;
      final branch = created['branch'] as String;

      final result = await git.removeWorktree(repoDir.path, worktreePath, branch: branch);
      expect(result['ok'], true);
      expect(result['branch_deleted'], true);
      expect(Directory(worktreePath).existsSync(), isFalse);

      final branchList = await _git(repoDir.path, ['branch', '--list', branch]);
      expect(branchList.stdout.trim(), isEmpty);
    });

    test('removeWorktree tolerates a worktree directory that is already gone', () async {
      await commitFile('a.txt', 'hello');
      final created = await git.createWorktree(repoDir.path, taskId: 'task5');
      final worktreePath = created['worktree_path'] as String;
      final branch = created['branch'] as String;

      Directory(worktreePath).deleteSync(recursive: true);

      final result = await git.removeWorktree(repoDir.path, worktreePath, branch: branch);
      expect(result['ok'], true);
    });
  });

  // F45: git runs programs the repository configures -- hooks, `core.hooksPath`,
  // `core.fsmonitor` -- on worktree add, checkout, merge and commit. Unless the caller says the
  // folder is trusted, none of them may run.
  group('repository programs (F45)', () {
    late Directory markers;

    setUp(() {
      markers = Directory.systemTemp.createTempSync('aura_hostd_git_hook_markers_');
    });

    tearDown(() => markers.deleteSync(recursive: true));

    /// A hook script that records that it ran by creating `markers/<name>`.
    void installHook(String hooksDir, String name) {
      Directory(hooksDir).createSync(recursive: true);
      final hook = File(p.join(hooksDir, name))
        ..writeAsStringSync('#!/bin/sh\ntouch "${p.join(markers.path, name)}"\n');
      Process.runSync('chmod', ['+x', hook.path]);
    }

    bool ran(String name) => File(p.join(markers.path, name)).existsSync();

    test('createWorktree runs no post-checkout hook from .git/hooks', () async {
      await commitFile('a.txt', 'hello');
      installHook(p.join(repoDir.path, '.git', 'hooks'), 'post-checkout');

      await git.createWorktree(repoDir.path, taskId: 'hooked');

      expect(ran('post-checkout'), isFalse);
    }, testOn: '!windows');

    test('createWorktree runs no hook from a repository-set core.hooksPath', () async {
      await commitFile('a.txt', 'hello');
      installHook(p.join(repoDir.path, '.githooks'), 'post-checkout');
      await _git(repoDir.path, ['config', 'core.hooksPath', '.githooks']);

      await git.createWorktree(repoDir.path, taskId: 'hooked2');

      expect(ran('post-checkout'), isFalse);
    }, testOn: '!windows');

    test('commit, checkout and merge run no hook either', () async {
      await commitFile('a.txt', 'hello');
      final hooks = p.join(repoDir.path, '.git', 'hooks');
      for (final name in ['pre-commit', 'post-commit', 'post-checkout', 'post-merge']) {
        installHook(hooks, name);
      }
      File(p.join(repoDir.path, 'b.txt')).writeAsStringSync('b');
      await git.stage(repoDir.path, ['b.txt']);
      await git.commit(repoDir.path, 'add b');
      await git.checkout(repoDir.path, 'side', create: true);
      File(p.join(repoDir.path, 'c.txt')).writeAsStringSync('c');
      await git.stage(repoDir.path, ['c.txt']);
      await git.commit(repoDir.path, 'add c');
      await git.checkout(repoDir.path, 'main');
      await git.mergeBranch(repoDir.path, 'side');

      for (final name in ['pre-commit', 'post-commit', 'post-checkout', 'post-merge']) {
        expect(ran(name), isFalse, reason: name);
      }
    }, testOn: '!windows');

    test('a folder the caller says is trusted keeps its own hooks', () async {
      await commitFile('a.txt', 'hello');
      installHook(p.join(repoDir.path, '.git', 'hooks'), 'pre-commit');
      final trustedGit = GitService(runsRepositoryPrograms: (cwd) => p.equals(cwd, repoDir.path));

      File(p.join(repoDir.path, 'b.txt')).writeAsStringSync('b');
      await trustedGit.stage(repoDir.path, ['b.txt']);
      await trustedGit.commit(repoDir.path, 'add b');

      expect(ran('pre-commit'), isTrue);
    }, testOn: '!windows');
  });

  group('branches/fetch/pull/push (human remote sync)', () {
    test('an empty repo lists no branches and a null current branch', () async {
      final result = await git.branches(repoDir.path);
      expect(result['branches'], isEmpty);
      expect(result['current'], isNull);
    });

    test('lists local branches with the checked-out one marked current', () async {
      await commitFile('a.txt', 'hello');
      await _git(repoDir.path, ['checkout', '-q', '-b', 'feature']);
      final result = await git.branches(repoDir.path);
      final branches = (result['branches'] as List).cast<Map<String, Object?>>();
      expect(branches.map((b) => b['name']), containsAll(['main', 'feature']));
      final current = branches.firstWhere((b) => b['current'] == true);
      expect(current['name'], 'feature');
      expect(result['current'], 'feature');
      expect(current['upstream'], isNull);
      expect(current['is_remote'], isFalse);
    });

    test('a non-git directory throws GitNotARepositoryError', () async {
      final plainDir = Directory.systemTemp.createTempSync('aura_hostd_not_a_repo_sync_');
      addTearDown(() => plainDir.deleteSync(recursive: true));
      expect(() => git.branches(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
      expect(() => git.fetch(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
      expect(() => git.pull(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
      expect(() => git.push(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
    });

    test('fetch with no remotes is a clean no-op', () async {
      await commitFile('a.txt', 'hello');
      final result = await git.fetch(repoDir.path);
      expect(result['ok'], true);
      expect(result['remotes'], isEmpty);
    });

    test('pull with no upstream returns a structured no_upstream failure', () async {
      await commitFile('a.txt', 'hello');
      final result = await git.pull(repoDir.path);
      expect(result['ok'], false);
      expect(result['reason'], 'no_upstream');
    });

    test('push with no remote returns a structured no_upstream failure', () async {
      await commitFile('a.txt', 'hello');
      final result = await git.push(repoDir.path);
      expect(result['ok'], false);
      expect(result['reason'], 'no_upstream');
    });

    test('fetch and pull retrieve a real commit made on a second local repository', () async {
      final (remoteDir, cloneDir) = await setUpRemoteWithClone();

      // A second commit lands on the remote after the clone was made, so fetch+pull have
      // something real to bring down rather than trivially no-opping.
      final pusherDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_pusher_');
      addTearDown(() => pusherDir.deleteSync(recursive: true));
      await _git(Directory.systemTemp.path, ['clone', '-q', remoteDir.path, pusherDir.path]);
      await _git(pusherDir.path, ['config', 'user.name', 'Test User']);
      await _git(pusherDir.path, ['config', 'user.email', 'test@example.com']);
      File(p.join(pusherDir.path, 'later.txt')).writeAsStringSync('later commit');
      await _git(pusherDir.path, ['add', '--', 'later.txt']);
      await _git(pusherDir.path, ['commit', '-q', '-m', 'later commit']);
      await _git(pusherDir.path, ['push', '-q', 'origin', 'main']);

      final fetchResult = await git.fetch(cloneDir.path);
      expect(fetchResult['ok'], true);
      expect(fetchResult['remotes'], ['origin']);

      final branchesAfterFetch = await git.branches(cloneDir.path);
      final remoteBranches = (branchesAfterFetch['branches'] as List).cast<Map<String, Object?>>();
      expect(remoteBranches.any((b) => b['name'] == 'remotes/origin/main'), isTrue);
      // Not yet in the clone's own working tree -- fetch only moves remote-tracking branches.
      expect(File(p.join(cloneDir.path, 'later.txt')).existsSync(), isFalse);

      final pullResult = await git.pull(cloneDir.path);
      expect(pullResult['ok'], true);
      expect(File(p.join(cloneDir.path, 'later.txt')).existsSync(), isTrue);
    });

    test('push sends a real local commit to a second local repository', () async {
      final (remoteDir, cloneDir) = await setUpRemoteWithClone();

      File(p.join(cloneDir.path, 'pushed.txt')).writeAsStringSync('pushed');
      await _git(cloneDir.path, ['add', '--', 'pushed.txt']);
      await _git(cloneDir.path, ['commit', '-q', '-m', 'from the clone']);

      final pushResult = await git.push(cloneDir.path);
      expect(pushResult['ok'], true);

      // Verify it actually landed on the remote, not just that the client reported success --
      // check out the bare remote's own main branch into a fresh directory and look for the file.
      final checkoutDir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_checkout_');
      addTearDown(() => checkoutDir.deleteSync(recursive: true));
      await _git(Directory.systemTemp.path, ['clone', '-q', remoteDir.path, checkoutDir.path]);
      expect(File(p.join(checkoutDir.path, 'pushed.txt')).existsSync(), isTrue);
    });
  });

  group('network timeout (fetch/pull/push against an unresponsive remote)', () {
    test('fetch against a remote that never responds is killed and reports a timeout, not a hang', () async {
      // A raw TCP listener that accepts the connection and then never writes anything back --
      // `git fetch` blocks forever waiting for the ref advertisement that never comes, the same
      // way it would against a genuinely stalled network peer. No real network is involved: both
      // ends are loopback-only.
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(() => server.close());
      final subscription = server.listen((socket) => socket.listen((_) {}));
      addTearDown(subscription.cancel);

      await commitFile('a.txt', 'hello');
      await _git(repoDir.path, ['remote', 'add', 'origin', 'git://127.0.0.1:${server.port}/unresponsive.git']);

      // A short override so this test doesn't have to wait out GitService's real default.
      final impatientGit = GitService(networkTimeout: const Duration(milliseconds: 300));
      final stopwatch = Stopwatch()..start();
      await expectLater(
        impatientGit.fetch(repoDir.path),
        throwsA(isA<GitCommandError>().having((e) => e.message, 'message', contains('timed out'))),
      );
      stopwatch.stop();
      // A generous upper bound relative to the 300ms timeout -- this is the assertion that
      // actually distinguishes "the process was killed" from "the test merely gave up waiting on
      // it," since the latter (a bare `Future.timeout` with no `Process.kill`) would leave the
      // real `git fetch` process running in the background indefinitely while still making a
      // weaker version of this test pass.
      expect(stopwatch.elapsed, lessThan(const Duration(seconds: 5)));
    }, timeout: const Timeout(Duration(seconds: 15)));

    test('the timeout message names the limit that was exceeded (D-02)', () async {
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(() => server.close());
      final subscription = server.listen((socket) => socket.listen((_) {}));
      addTearDown(subscription.cancel);
      await commitFile('a.txt', 'hello');
      await _git(repoDir.path, ['remote', 'add', 'origin', 'git://127.0.0.1:${server.port}/unresponsive.git']);

      await expectLater(
        GitService(networkTimeout: const Duration(seconds: 1)).fetch(repoDir.path),
        throwsA(isA<GitCommandError>().having((e) => e.message, 'message', contains('timed out after 1s'))),
      );
    }, timeout: const Timeout(Duration(seconds: 20)));

    test('fetch, pull and push default to five minutes, not the old 30 seconds (D-02)', () {
      expect(GitService().networkTimeout, const Duration(seconds: 300));
      expect(defaultGitNetworkTimeout, const Duration(seconds: 300));
    });
  });

  group('remotes, fetch --all, pull and push outcomes, publish', () {
    Future<String> head(String cwd) async => ((await _git(cwd, ['rev-parse', 'HEAD'])).stdout as String).trim();

    /// Commits [file] on [branch] in a fresh clone of [remote], pushes it, and returns the commit.
    /// [createBranch] makes the branch first -- a branch the remote does not have yet.
    Future<String> pushNewCommit(
      Directory remote,
      String file, {
      String branch = 'main',
      bool createBranch = false,
    }) async {
      final dir = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_other_');
      addTearDown(() => dir.deleteSync(recursive: true));
      await _git(Directory.systemTemp.path, ['clone', '-q', remote.path, dir.path]);
      await _git(dir.path, ['config', 'user.name', 'Test User']);
      await _git(dir.path, ['config', 'user.email', 'test@example.com']);
      await _git(dir.path, ['checkout', '-q', if (createBranch) '-b', branch]);
      File(p.join(dir.path, file)).writeAsStringSync(file);
      await _git(dir.path, ['add', '--', file]);
      await _git(dir.path, ['commit', '-q', '-m', 'add $file']);
      await _git(dir.path, ['push', '-q', 'origin', branch]);
      return head(dir.path);
    }

    Future<Directory> secondBareRemote(Directory clone) async {
      final remote = Directory.systemTemp.createTempSync('aura_hostd_git_service_test_remote2_');
      addTearDown(() => remote.deleteSync(recursive: true));
      await _git(remote.path, ['init', '-q', '--bare', '-b', 'main']);
      await _git(clone.path, ['remote', 'add', 'upstream', remote.path]);
      await _git(clone.path, ['push', '-q', 'upstream', 'main']);
      return remote;
    }

    test('remotes lists every configured remote, and none for a purely local repo', () async {
      await commitFile('a.txt', 'hello');
      expect((await git.remotes(repoDir.path))['remotes'], isEmpty);

      final (_, clone) = await setUpRemoteWithClone();
      await secondBareRemote(clone);
      expect((await git.remotes(clone.path))['remotes'], ['origin', 'upstream']);
    });

    test('fetch updates every remote when a repository has two (F1: it used to fail)', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      final upstream = await secondBareRemote(clone);
      final originTip = await pushNewCommit(origin, 'from-origin.txt');
      final upstreamTip = await pushNewCommit(upstream, 'from-upstream.txt');

      final result = await git.fetch(clone.path);

      expect(result['ok'], true);
      expect(result['remotes'], ['origin', 'upstream']);
      expect(await head(clone.path), isNot(originTip), reason: 'fetch must not move the working tree');
      expect(((await _git(clone.path, ['rev-parse', 'origin/main'])).stdout as String).trim(), originTip);
      expect(((await _git(clone.path, ['rev-parse', 'upstream/main'])).stdout as String).trim(), upstreamTip);
    });

    test('pull reports a diverged branch as a structured failure and changes nothing', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      await pushNewCommit(origin, 'remote.txt');
      File(p.join(clone.path, 'local.txt')).writeAsStringSync('local');
      await _git(clone.path, ['add', '--', 'local.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'local commit']);
      final before = await head(clone.path);

      final result = await git.pull(clone.path);

      expect(result['ok'], false);
      expect(result['reason'], 'diverged');
      expect(await head(clone.path), before);
    });

    test('pull reports an untracked file it would overwrite, naming the file', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      await pushNewCommit(origin, 'clash.txt');
      File(p.join(clone.path, 'clash.txt')).writeAsStringSync('mine');

      final result = await git.pull(clone.path);

      expect(result['ok'], false);
      expect(result['reason'], 'local_changes_would_be_overwritten');
      expect(result['affected_files'], contains('clash.txt'));
      expect(File(p.join(clone.path, 'clash.txt')).readAsStringSync(), 'mine');
    });

    test('push reports a branch the remote has moved past as diverged, never forcing', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      final remoteTip = await pushNewCommit(origin, 'remote.txt');
      File(p.join(clone.path, 'local.txt')).writeAsStringSync('local');
      await _git(clone.path, ['add', '--', 'local.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'local commit']);

      final result = await git.push(clone.path);

      expect(result['ok'], false);
      expect(result['reason'], 'diverged');
      final remoteHead = (await _git(origin.path, ['rev-parse', 'main'])).stdout as String;
      expect(remoteHead.trim(), remoteTip, reason: 'the remote branch must be untouched');
    });

    test('publish pushes the checked-out branch under its own name and sets its upstream (D-01)', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      await _git(clone.path, ['checkout', '-q', '-b', 'feature/login']);
      File(p.join(clone.path, 'f.txt')).writeAsStringSync('f');
      await _git(clone.path, ['add', '--', 'f.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'feature work']);
      expect((await git.push(clone.path))['reason'], 'no_upstream', reason: 'the situation publish exists for');

      final result = await git.publish(clone.path, remote: 'origin');

      expect(result, {'ok': true, 'branch': 'feature/login', 'remote': 'origin'});
      final onRemote = (await _git(origin.path, ['rev-parse', 'refs/heads/feature/login'])).stdout as String;
      expect(onRemote.trim(), await head(clone.path));
      final status = await git.status(clone.path);
      expect(status['upstream'], 'origin/feature/login');
      expect(status['ahead'], 0);
    });

    test('publish chooses the remote it is told to when there are several', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      final upstream = await secondBareRemote(clone);
      await _git(clone.path, ['checkout', '-q', '-b', 'topic']);
      File(p.join(clone.path, 't.txt')).writeAsStringSync('t');
      await _git(clone.path, ['add', '--', 't.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'topic']);

      final result = await git.publish(clone.path, remote: 'upstream');

      expect(result['ok'], true);
      expect((await _git(upstream.path, ['rev-parse', '--verify', '--quiet', 'refs/heads/topic'])).exitCode, 0);
      expect((await _git(origin.path, ['rev-parse', '--verify', '--quiet', 'refs/heads/topic'])).exitCode, isNot(0));
      expect((await git.status(clone.path))['upstream'], 'upstream/topic');
    });

    test('publish refuses a name that is not a configured remote, so it can never be an option or URL', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      await _git(clone.path, ['checkout', '-q', '-b', 'topic']);
      File(p.join(clone.path, 't.txt')).writeAsStringSync('t');
      await _git(clone.path, ['add', '--', 't.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'topic']);

      for (final name in ['nope', '--force', '--mirror', origin.path]) {
        final result = await git.publish(clone.path, remote: name);
        expect(result, {'ok': false, 'reason': 'unknown_remote'}, reason: name);
      }
      expect((await _git(origin.path, ['rev-parse', '--verify', '--quiet', 'refs/heads/topic'])).exitCode, isNot(0));
    });

    test('publish on a detached HEAD and on a branch with no commit are structured failures', () async {
      final (_, clone) = await setUpRemoteWithClone();
      await _git(clone.path, ['checkout', '-q', '--detach']);
      expect(await git.publish(clone.path, remote: 'origin'), {'ok': false, 'reason': 'detached_head'});

      await _git(repoDir.path, ['remote', 'add', 'origin', clone.path]);
      expect(await git.publish(repoDir.path, remote: 'origin'), {'ok': false, 'reason': 'no_commits'});
    });

    test('publish reports a remote that already has a different branch of that name as diverged', () async {
      final (origin, clone) = await setUpRemoteWithClone();
      final theirs = await pushNewCommit(origin, 'theirs.txt', branch: 'shared', createBranch: true);
      await _git(clone.path, ['checkout', '-q', '-b', 'shared']);
      File(p.join(clone.path, 'mine.txt')).writeAsStringSync('mine');
      await _git(clone.path, ['add', '--', 'mine.txt']);
      await _git(clone.path, ['commit', '-q', '-m', 'mine']);

      final result = await git.publish(clone.path, remote: 'origin');

      expect(result, {'ok': false, 'reason': 'diverged'});
      final remoteTip = (await _git(origin.path, ['rev-parse', 'refs/heads/shared'])).stdout as String;
      expect(remoteTip.trim(), theirs, reason: 'never forced');
    });

    test('publish and the other remote operations are not reachable outside a repository', () async {
      final plainDir = Directory.systemTemp.createTempSync('aura_hostd_not_a_repo_publish_');
      addTearDown(() => plainDir.deleteSync(recursive: true));
      expect(() => git.publish(plainDir.path, remote: 'origin'), throwsA(isA<GitNotARepositoryError>()));
      expect(() => git.remotes(plainDir.path), throwsA(isA<GitNotARepositoryError>()));
    });
  });

  group('sign-in failures and message language (D-04, R-01, R-02)', () {
    test('git\'s sign-in refusals are recognised, and ordinary failures are not', () {
      for (final refused in [
        "fatal: Authentication failed for 'https://github.com/a/b.git/'",
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
        "fatal: could not read Password for 'https://x@github.com': terminal prompts disabled",
        'git@github.com: Permission denied (publickey).',
        'Host key verification failed.',
        "fatal: unable to access 'https://h/r.git/': The requested URL returned error: 403",
        "fatal: unable to access 'https://h/r.git/': The requested URL returned error: 401",
        'remote: Invalid username or password.',
        'remote: Support for password authentication was removed on August 13, 2021.',
        'fatal: Logon failed, use ctrl+c to cancel basic credential prompt.',
      ]) {
        expect(looksLikeGitAuthFailure(refused), isTrue, reason: refused);
      }
      for (final other in [
        '',
        'fatal: not a git repository',
        '! [rejected]        main -> main (non-fast-forward)',
        "fatal: unable to access 'https://h/r.git/': Could not resolve host: h",
        "fatal: unable to access 'https://h/r.git/': The requested URL returned error: 404",
        'error: failed to push some refs',
      ]) {
        expect(looksLikeGitAuthFailure(other), isFalse, reason: other);
      }
    });

    test('a URL\'s embedded credential is masked in what is shown, other text is untouched', () {
      expect(
        scrubGitUrlCredentials("fatal: unable to access 'https://octo:ghp_SECRET123@github.com/a/b.git/': 403"),
        "fatal: unable to access 'https://***@github.com/a/b.git/': 403",
      );
      expect(scrubGitUrlCredentials('http://ghp_ONLYTOKEN@host/r.git'), 'http://***@host/r.git');
      expect(scrubGitUrlCredentials('https://github.com/a/b.git and git@github.com:a/b.git'),
          'https://github.com/a/b.git and git@github.com:a/b.git');
      expect(scrubGitUrlCredentials('ssh://git@host/r.git'), 'ssh://***@host/r.git');
    });

    /// A real HTTP remote that answers every request with 401, and a service whose git sees no
    /// credential helper at all (the machine's own -- Git Credential Manager on Windows -- must not
    /// be asked), so a refused sign-in is what git reports.
    Future<(GitService, String url)> refusingRemote() async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(() => server.close(force: true));
      server.listen((request) async {
        request.response
          ..statusCode = HttpStatus.unauthorized
          ..headers.set(HttpHeaders.wwwAuthenticateHeader, 'Basic realm="aura-test"');
        await request.response.close();
      });
      final emptyConfig = File(p.join(repoDir.path, '.empty-gitconfig'))..writeAsStringSync('');
      final service = GitService(environment: {
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': emptyConfig.path,
      });
      return (service, 'http://127.0.0.1:${server.port}/private/repo.git');
    }

    test('fetch, pull, push and publish report a refused sign-in as auth_failed, not a git error', () async {
      final (service, url) = await refusingRemote();
      await commitFile('a.txt', 'hello');
      await _git(repoDir.path, ['remote', 'add', 'origin', url]);
      await _git(repoDir.path, ['config', 'branch.main.remote', 'origin']);
      await _git(repoDir.path, ['config', 'branch.main.merge', 'refs/heads/main']);

      expect(await service.fetch(repoDir.path), {'ok': false, 'reason': 'auth_failed'});
      expect(await service.pull(repoDir.path), {'ok': false, 'reason': 'auth_failed'});
      expect(await service.push(repoDir.path), {'ok': false, 'reason': 'auth_failed'});
      expect(await service.publish(repoDir.path, remote: 'origin'), {'ok': false, 'reason': 'auth_failed'});
    }, timeout: const Timeout(Duration(seconds: 60)));

    test('the structured outcomes do not depend on the machine\'s language', () async {
      // The person's environment asks for German; the remote operations pin English themselves.
      // (Without German catalogs installed this passes trivially; with them it is the proof.)
      final german = GitService(environment: {'LC_ALL': 'de_DE.UTF-8', 'LANG': 'de_DE.UTF-8', 'LANGUAGE': 'de'});
      await commitFile('a.txt', 'hello');
      expect(await german.pull(repoDir.path), {'ok': false, 'reason': 'no_upstream'});
      expect(await german.push(repoDir.path), {'ok': false, 'reason': 'no_upstream'});
    });
  });
}
