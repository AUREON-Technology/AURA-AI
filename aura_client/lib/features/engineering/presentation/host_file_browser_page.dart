import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_file_view.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Read-only browser for one granted `aura_hostd` workspace root (ADR-011 Unit 8) -- lists
/// directories via `GET /list` and shows file content via `GET /read`, both containment-checked
/// daemon-side (`control_server.dart`), so this page can only ever see what was actually granted.
///
/// This is a *browser*, not a diff reviewer -- reviewing what the agent is about to change already
/// happens in chat (`engineer_confirmation_card.dart` + `SandboxDiffView`) before a write lands.
/// What's shown here is just the current state of a file on disk.
///
/// Directory listing goes through the same [ExplorerService] the desktop IDE Explorer uses
/// (`allowLocalFallback: false`, since this page is reachable from any platform pairing with a
/// remote hostd, not just the desktop app co-located with it -- see that flag's doc comment) so
/// the two pages' listing/parsing logic can't drift, per the 2026-09-25 audit's "duplication, not
/// reuse" finding. File reads stay on a direct `HostdControlClient` call rather than
/// [ExplorerService.readFile]: that method's `String?` return can't carry the `truncated` flag or
/// the daemon's actual error message, both of which this page's file view surfaces.
class HostFileBrowserPage extends ConsumerStatefulWidget {
  const HostFileBrowserPage({super.key, required this.root});

  final String root;

  @override
  ConsumerState<HostFileBrowserPage> createState() => _HostFileBrowserPageState();
}

class _HostFileBrowserPageState extends ConsumerState<HostFileBrowserPage> {
  String _relativePath = '';
  List<FileSystemEntry>? _entries;
  String? _selectedFileContent;
  String? _selectedFileName;
  bool _truncated = false;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadDirectory();
  }

  Future<void> _loadDirectory() async {
    setState(() {
      _loading = true;
      _error = null;
      _selectedFileContent = null;
    });
    final hostdClient = ref.read(hostdControlClientProvider);
    final service = ExplorerService(hostdClient, widget.root);
    final result = await service.listDirectory(
      relativePath: _relativePath,
      grantedRoots: [widget.root],
      hostConnected: true,
      allowLocalFallback: false,
    );
    setState(() {
      _entries = result.entries;
      _error = result.error;
      _loading = false;
    });
  }

  Future<void> _openFile(String name) async {
    final relative = _relativePath.isEmpty ? name : '$_relativePath/$name';
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final client = ref.read(hostdControlClientProvider);
      final result = await client.readFile(root: widget.root, path: relative);
      setState(() {
        _selectedFileName = relative;
        _selectedFileContent = result['content'] as String;
        _truncated = result['truncated'] as bool? ?? false;
      });
    } on HostdControlError catch (error) {
      setState(() => _error = error.message);
    } catch (error) {
      setState(() => _error = error.toString());
    } finally {
      setState(() => _loading = false);
    }
  }

  void _enterDirectory(String name) {
    setState(() {
      _relativePath = _relativePath.isEmpty ? name : '$_relativePath/$name';
    });
    _loadDirectory();
  }

  void _goUp() {
    if (_relativePath.isEmpty) return;
    final segments = _relativePath.split('/')..removeLast();
    setState(() => _relativePath = segments.join('/'));
    _loadDirectory();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(
          _relativePath.isEmpty ? widget.root : '${widget.root}/$_relativePath',
          style: const TextStyle(fontSize: 14),
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: _selectedFileContent != null
          ? _buildFileView(palette)
          : _buildDirectoryListing(palette),
    );
  }

  Widget _buildFileView(AuraPalette palette) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.all(AuraSpacing.md),
          child: AuraButton(
            label: context.l10n.engineeringBackToFileList,
            icon: Icons.arrow_back,
            onPressed: () => setState(() => _selectedFileContent = null),
          ),
        ),
        if (_truncated)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md),
            child: Text(
              context.l10n.engineeringFileTruncated,
              style: TextStyle(color: palette.textSecondary, fontSize: 12),
            ),
          ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AuraSpacing.md),
            child: SandboxFileView(
              path: _selectedFileName ?? '',
              content: _selectedFileContent ?? '',
              maxLines: 4000,
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildDirectoryListing(AuraPalette palette) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.lg),
          child: Text(context.l10n.engineeringReadDirFailed(_error!), style: TextStyle(color: palette.danger)),
        ),
      );
    }
    final entries = _entries ?? const [];
    return ListView(
      children: [
        if (_relativePath.isNotEmpty)
          ListTile(
            leading: const Icon(Icons.arrow_upward),
            title: const Text('..'),
            onTap: _goUp,
          ),
        for (final entry in entries)
          ListTile(
            leading: Icon(
              entry.isDirectory ? Icons.folder_outlined : Icons.insert_drive_file_outlined,
            ),
            title: Text(entry.name),
            onTap: () => entry.isDirectory ? _enterDirectory(entry.name) : _openFile(entry.name),
          ),
        if (entries.isEmpty)
          Padding(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: Text(context.l10n.engineeringFolderEmpty, style: TextStyle(color: palette.textSecondary)),
          ),
      ],
    );
  }
}
