import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// A terminal-styled monospace block. Used two ways: rendering a `run_command` confirmation's
/// proposed command (before it has run, so there is no output yet -- pass [body] as the command
/// itself), and rendering a completed run's captured stdout/stderr from an `EngineerRun`'s
/// `sandbox_transcript` (Engineer Task History). The dark terminal look stays constant in both
/// contexts because "this is raw command text" is the thing being communicated either way.
class SandboxTerminalView extends StatelessWidget {
  const SandboxTerminalView({
    super.key,
    required this.body,
    this.commandLabel,
    this.maxLines = 200,
  });

  /// The command about to run, or the full transcript (command + stdout/stderr) already run.
  final String body;

  /// Shown as a `$ <label>` prompt line above [body], when the caller already has the command
  /// text separate from the transcript (e.g. a pending confirmation, where [body] duplicates
  /// [commandLabel] and this can be omitted).
  final String? commandLabel;

  final int maxLines;

  @override
  Widget build(BuildContext context) {
    final lines = body.split('\n');
    final truncated = lines.length > maxLines;
    final visible = truncated ? lines.take(maxLines).toList() : lines;

    return Container(
      margin: const EdgeInsets.only(top: AuraSpacing.xs),
      width: double.infinity,
      decoration: BoxDecoration(
        color: const Color(0xFF14161B),
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      padding: const EdgeInsets.all(AuraSpacing.sm),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            minWidth: MediaQuery.sizeOf(context).width,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              if (commandLabel != null)
                Text(
                  '\$ $commandLabel',
                  style: const TextStyle(
                    fontFamily: 'Courier',
                    fontSize: 12,
                    color: Color(0xFF9CDCFE),
                    fontWeight: FontWeight.w600,
                  ),
                ),
              for (final line in visible)
                Text(
                  line,
                  style: const TextStyle(
                    fontFamily: 'Courier',
                    fontSize: 12,
                    color: Color(0xFFD4D4D4),
                    height: 1.4,
                  ),
                ),
              if (truncated)
                Padding(
                  padding: const EdgeInsets.only(top: AuraSpacing.xs),
                  child: Text(
                    '… ${lines.length - maxLines} more line(s)',
                    style: const TextStyle(fontSize: 11, color: Color(0xFF808080)),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
