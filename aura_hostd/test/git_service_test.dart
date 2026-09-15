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

  group('createWorktree / removeWorktree (D8 per-task isolation)', () {
    test('creates a real, independently-writable checkout on a new branch', () async {
      await commitFile('a.txt', 'hello');
      final worktree = await git.createWorktree(
        repoDir.path,
        taskId: 't_1',
        branchName: 'aura/task-t_1',
      );
      expect(worktree.branch, 'aura/task-t_1');
      expect(Directory(worktree.path).existsSync(), isTrue);
      expect(File(p.join(worktree.path, 'a.txt')).readAsStringSync(), 'hello');

      // Writing in the worktree must not touch the parent checkout.
      File(p.join(worktree.path, 'a.txt')).writeAsStringSync('changed in worktree');
      expect(File(p.join(repoDir.path, 'a.txt')).readAsStringSync(), 'hello');

      final worktreeStatus = await git.status(worktree.path);
      expect(worktreeStatus['branch'], 'aura/task-t_1');
    });

    test('excludes .aura-workspaces/ via .git/info/exclude, not the tracked .gitignore', () async {
      await commitFile('a.txt', 'hello');
      await git.createWorktree(repoDir.path, taskId: 't_1', branchName: 'aura/task-t_1');

      final excludeFile = File(p.join(repoDir.path, '.git', 'info', 'exclude'));
      expect(excludeFile.existsSync(), isTrue);
      expect(excludeFile.readAsStringSync(), contains('.aura-workspaces/'));
      expect(File(p.join(repoDir.path, '.gitignore')).existsSync(), isFalse);

      // The parent checkout's own status must not show the task worktree as untracked clutter.
      final parentStatus = await git.status(repoDir.path);
      expect(parentStatus['files'], isEmpty);
    });

    test('is idempotent about the exclude marker across repeated calls', () async {
      await commitFile('a.txt', 'hello');
      await git.createWorktree(repoDir.path, taskId: 't_1', branchName: 'aura/task-t_1');
      await git.createWorktree(repoDir.path, taskId: 't_2', branchName: 'aura/task-t_2');
      final excludeFile = File(p.join(repoDir.path, '.git', 'info', 'exclude'));
      final marker = RegExp('.aura-workspaces/');
      expect(marker.allMatches(excludeFile.readAsStringSync()), hasLength(1));
    });

    test('honors an explicit base branch', () async {
      await commitFile('a.txt', 'on-main');
      await _git(repoDir.path, ['checkout', '-q', '-b', 'other']);
      await commitFile('b.txt', 'on-other');
      await _git(repoDir.path, ['checkout', '-q', 'main']);

      final worktree = await git.createWorktree(
        repoDir.path,
        taskId: 't_1',
        branchName: 'aura/task-t_1',
        baseBranch: 'other',
      );
      expect(File(p.join(worktree.path, 'b.txt')).existsSync(), isTrue);
    });

    test('removeWorktree deletes the working directory and the branch', () async {
      await commitFile('a.txt', 'hello');
      final worktree = await git.createWorktree(repoDir.path, taskId: 't_1', branchName: 'aura/task-t_1');
      await git.removeWorktree(repoDir.path, worktree.path, worktree.branch);

      expect(Directory(worktree.path).existsSync(), isFalse);
      final branches = await _git(repoDir.path, ['branch', '--list', worktree.branch]);
      expect(branches.stdout.toString().trim(), isEmpty);
    });

    test('removeWorktree is best-effort when the worktree directory is already gone', () async {
      await commitFile('a.txt', 'hello');
      final worktree = await git.createWorktree(repoDir.path, taskId: 't_1', branchName: 'aura/task-t_1');
      Directory(worktree.path).deleteSync(recursive: true);

      await git.removeWorktree(repoDir.path, worktree.path, worktree.branch);
      final branches = await _git(repoDir.path, ['branch', '--list', worktree.branch]);
      expect(branches.stdout.toString().trim(), isEmpty);
    });
  });
}
