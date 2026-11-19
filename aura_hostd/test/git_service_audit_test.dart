// Desktop IDE audit (GT-01, GT-02), against a real local git repository like git_service_test.dart.
//
//  * GT-01: `git status --short` wraps a file name containing a space in double quotes, and that
//    quoted string was handed on as the path. A file called `my file.txt` then could not be
//    staged, unstaged or diffed, and "Stage all" failed for every file if one existed.
//  * GT-02: "Create branch" ran `git checkout -B`, which resets a branch that already exists to
//    HEAD -- moving it and orphaning its commits -- instead of refusing.
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
    repoDir = Directory.systemTemp.createTempSync('aura_hostd_git_audit_test_');
    await _git(repoDir.path, ['init', '-q', '-b', 'main']);
    await _git(repoDir.path, ['config', 'user.name', 'Test User']);
    await _git(repoDir.path, ['config', 'user.email', 'test@example.com']);
    git = GitService();
  });

  tearDown(() => repoDir.deleteSync(recursive: true));

  Future<void> commitFile(String name, String content, {String message = 'commit'}) async {
    File(p.join(repoDir.path, name)).writeAsStringSync(content);
    await _git(repoDir.path, ['add', '--', name]);
    await _git(repoDir.path, ['commit', '-q', '-m', message]);
  }

  Future<List<String>> statusPaths() async {
    final status = await git.status(repoDir.path);
    return (status['files'] as List).cast<Map<String, Object?>>().map((f) => f['path'] as String).toList();
  }

  group('status reports file names exactly as they are on disk (GT-01)', () {
    test('a name with spaces, and a folder with spaces, come back unquoted', () async {
      File(p.join(repoDir.path, 'my file.txt')).writeAsStringSync('x');
      Directory(p.join(repoDir.path, 'dir x')).createSync();
      File(p.join(repoDir.path, 'dir x', 'inner.txt')).writeAsStringSync('y');

      final paths = await statusPaths();

      expect(paths, containsAll(['my file.txt', 'dir x/']));
      expect(paths.any((path) => path.contains('"')), isFalse, reason: 'no git quoting leaks through');
    });

    test('non-ASCII names come back as written', () async {
      File(p.join(repoDir.path, 'ملف.txt')).writeAsStringSync('x');
      File(p.join(repoDir.path, 'café au lait.txt')).writeAsStringSync('y');

      expect(await statusPaths(), containsAll(['ملف.txt', 'café au lait.txt']));
    });

    test('a file with a space in its name can be staged and unstaged by the path status gave', () async {
      await commitFile('seed.txt', 'seed');
      File(p.join(repoDir.path, 'my file.txt')).writeAsStringSync('x');
      final path = (await statusPaths()).single;

      await git.stage(repoDir.path, [path]);
      var status = await git.status(repoDir.path);
      var file = (status['files'] as List).cast<Map<String, Object?>>().single;
      expect(file['staged'], isTrue);

      await git.unstage(repoDir.path, [path]);
      status = await git.status(repoDir.path);
      file = (status['files'] as List).cast<Map<String, Object?>>().single;
      expect(file['staged'], isFalse);
    });

    test('staging every reported path at once works even when one has a space', () async {
      await commitFile('seed.txt', 'seed');
      File(p.join(repoDir.path, 'plain.txt')).writeAsStringSync('a');
      File(p.join(repoDir.path, 'with space.txt')).writeAsStringSync('b');

      await git.stage(repoDir.path, await statusPaths());

      final files = ((await git.status(repoDir.path))['files'] as List).cast<Map<String, Object?>>();
      expect(files, hasLength(2));
      expect(files.every((f) => f['staged'] == true), isTrue);
    });

    test('a rename reports the new and the old name, spaces included', () async {
      await commitFile('old name.txt', 'content that is long enough to be detected as a rename');
      await _git(repoDir.path, ['mv', 'old name.txt', 'new name.txt']);

      final files = ((await git.status(repoDir.path))['files'] as List).cast<Map<String, Object?>>();

      expect(files, hasLength(1));
      expect(files.single['path'], 'new name.txt');
      expect(files.single['renamed_from'], 'old name.txt');
      expect(files.single['staged'], isTrue);
    });

    test('files after a rename are not swallowed or shifted', () async {
      await commitFile('old.txt', 'content that is long enough to be detected as a rename');
      await _git(repoDir.path, ['mv', 'old.txt', 'new.txt']);
      File(p.join(repoDir.path, 'after.txt')).writeAsStringSync('z');

      expect(await statusPaths(), unorderedEquals(['new.txt', 'after.txt']));
    });

    test('a clean repository and an empty repository both report no files', () async {
      expect(await statusPaths(), isEmpty);
      await commitFile('a.txt', 'a');
      expect(await statusPaths(), isEmpty);
    });
  });

  group('Create branch never resets an existing branch (GT-02)', () {
    Future<String> revision(String ref) async =>
        ((await _git(repoDir.path, ['rev-parse', ref])).stdout as String).trim();

    test('creating a branch whose name is taken is refused and the branch is untouched', () async {
      await commitFile('a.txt', 'a', message: 'first');
      await _git(repoDir.path, ['checkout', '-q', '-b', 'develop']);
      await commitFile('b.txt', 'b', message: 'only on develop');
      final developBefore = await revision('develop');
      await _git(repoDir.path, ['checkout', '-q', 'main']);

      final result = await git.checkout(repoDir.path, 'develop', create: true);

      expect(result, {'ok': false, 'reason': 'branch_exists'});
      expect(await revision('develop'), developBefore, reason: 'develop must not move');
      final head = (await _git(repoDir.path, ['rev-parse', '--abbrev-ref', 'HEAD'])).stdout as String;
      expect(head.trim(), 'main', reason: 'and nothing was checked out');
    });

    test('creating a genuinely new branch still works', () async {
      await commitFile('a.txt', 'a');

      final result = await git.checkout(repoDir.path, 'feature-x', create: true);

      expect(result['ok'], isTrue);
      final head = (await _git(repoDir.path, ['rev-parse', '--abbrev-ref', 'HEAD'])).stdout as String;
      expect(head.trim(), 'feature-x');
    });
  });
}
