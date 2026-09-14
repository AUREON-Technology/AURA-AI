/// Full-page diff view for a single file, pushed from `git_panel.dart` when a changed file is
/// tapped (D5). Reuses `SandboxDiffView` directly -- confirmed presentation-only, no chat
/// coupling -- fed by `gitNotifierProvider`'s loaded diff text, rather than reimplementing a
/// diff renderer.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';

class GitDiffPage extends ConsumerStatefulWidget {
  const GitDiffPage({super.key, required this.filePath, required this.scope});

  final String filePath;
  final GitDiffScope scope;

  @override
  ConsumerState<GitDiffPage> createState() => _GitDiffPageState();
}

class _GitDiffPageState extends ConsumerState<GitDiffPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        ref.read(gitNotifierProvider.notifier).loadDiff(file: widget.filePath, scope: widget.scope);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final git = ref.watch(gitNotifierProvider);
    final loadedForThisFile = git.diffScope == widget.scope;

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        title: Text(widget.filePath, overflow: TextOverflow.ellipsis),
        backgroundColor: palette.surface,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AuraSpacing.md),
        child: !loadedForThisFile
            ? const Center(child: Padding(padding: EdgeInsets.all(32), child: CircularProgressIndicator()))
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (git.diffTruncated)
                    Padding(
                      padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                      child: Text(
                        'Diff truncated -- view the full diff in a terminal.',
                        style: TextStyle(color: palette.warning, fontSize: 12),
                      ),
                    ),
                  SandboxDiffView(diffText: git.diffText ?? '', maxLines: 2000),
                ],
              ),
      ),
    );
  }
}
