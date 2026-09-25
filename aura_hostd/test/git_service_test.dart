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
  });
}
