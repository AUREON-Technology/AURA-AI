/// WP4 (D-13): "Initialize git here" -- making a granted folder a git repository with a first
/// commit, so the Engineer can work on its own worktree of it (D-09: isolation by default).
///
/// One implementation behind both ways in: the control server's `POST /git/init` (the person, at
/// the desk) and the agent-facing `init_git` RPC (the person, tapping the button on their phone or
/// the web; `task_handler.dart` adds the remote-control and confirmation checks). Either way the
/// folder must be exactly a granted root -- never a subfolder, never anything else on the machine --
/// and must not already be a repository with a commit.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

import 'containment.dart';
import 'error_codes.dart';
import 'git_service.dart';

/// The first commit's message.
const initialCommitMessage = "Initial commit (made by AURA before the Engineer's first change)";

/// What the Engineer says when isolation is impossible because the folder is not a git work tree
/// with a commit ([HostdErrorCode.isolationNeedsGit]).
const isolationNeedsGitMessage = "This folder isn't a git repository, so the Engineer can't work "
    'on its own copy of it. Use "Initialize git here" in AURA first.';

/// Why "Initialize git here" did nothing: [code] is one of [HostdErrorCode.rootNotGranted],
/// [HostdErrorCode.alreadyAGitRepo] or [HostdErrorCode.gitInitFailed] (then [detail] is git's
/// own message).
class GitInitRefusal implements Exception {
  GitInitRefusal(this.code, this.message, {this.detail});
  final String code;
  final String message;
  final String? detail;
  @override
  String toString() => detail == null ? message : '$message: $detail';
}

/// The granted root [path] names, or null. [path] must be absolute and canonicalize (symlinks
/// resolved) to exactly a granted root.
WorkspaceRoot? grantedRootAt(WorkspaceRoots roots, String path) {
  if (!p.isAbsolute(path)) return null;
  final String canonical;
  try {
    canonical = Directory(path).resolveSymbolicLinksSync();
  } on FileSystemException {
    return null;
  }
  return roots.byCanonicalPath(canonical);
}

/// Makes the granted root [path] a git repository with one commit and returns
/// `{"ok": true, "commit": <sha>}`. Throws [GitInitRefusal].
Future<Map<String, Object?>> initializeGrantedFolder(
  WorkspaceRoots roots,
  GitService git,
  String path,
) async {
  final root = grantedRootAt(roots, path);
  if (root == null) {
    throw GitInitRefusal(HostdErrorCode.rootNotGranted, '$path is not a granted root');
  }
  final GitIsolationState state;
  try {
    state = await git.isolationState(root.canonicalPath);
  } on GitCommandError catch (error) {
    throw GitInitRefusal(HostdErrorCode.gitInitFailed, 'git could not inspect this folder',
        detail: error.message);
  } on GitDecodeError catch (error) {
    throw GitInitRefusal(HostdErrorCode.gitInitFailed, 'git could not inspect this folder',
        detail: error.message);
  } on ProcessException catch (error) {
    throw GitInitRefusal(HostdErrorCode.gitInitFailed, 'git could not be started',
        detail: error.message);
  }
  if (state == GitIsolationState.ready) {
    throw GitInitRefusal(
      HostdErrorCode.alreadyAGitRepo,
      '${root.canonicalPath} is already a git repository',
    );
  }
  try {
    // A work tree with no commit yet only needs its first commit: running `git init` again would
    // be harmless, but there is nothing for it to do.
    final sha = await git.initRepository(
      root.canonicalPath,
      message: initialCommitMessage,
      init: state == GitIsolationState.notAWorkTree,
    );
    return {'ok': true, 'commit': sha};
  } on GitInitError catch (error) {
    throw GitInitRefusal(HostdErrorCode.gitInitFailed, error.message, detail: error.detail);
  }
}
