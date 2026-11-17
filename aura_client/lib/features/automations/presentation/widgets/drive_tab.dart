import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../drive_view_model.dart';

class DriveTab extends ConsumerStatefulWidget {
  final bool isMock;
  const DriveTab({super.key, required this.isMock});

  @override
  ConsumerState<DriveTab> createState() => _DriveTabState();
}

class _DriveTabState extends ConsumerState<DriveTab> {
  final _projectNameController = TextEditingController();
  final _projectDescController = TextEditingController();

  @override
  void dispose() {
    _projectNameController.dispose();
    _projectDescController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    if (widget.isMock) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: palette.warning),
              const SizedBox(height: 16),
              Text(
                'Google Drive Workspace Creation Unavailable',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: palette.textPrimary),
              ),
              const SizedBox(height: 8),
              Text(
                'You are signed in via a mock developer profile. Drive workspace directories can only be generated when logged in with a real Google Account.',
                textAlign: TextAlign.center,
                style: TextStyle(color: palette.textSecondary, fontSize: 13),
              ),
            ],
          ),
        ),
      );
    }

    final ui = ref.watch(driveViewModelProvider);
    final vm = ref.read(driveViewModelProvider.notifier);

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text('Drive Project Workspace Generator', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                  const SizedBox(height: 4),
                  Text(
                    'Enter project details. AURA will generate a Google Drive folder and populate it with a structured Markdown project outline document.',
                    style: TextStyle(color: palette.textSecondary, fontSize: 12),
                  ),
                  Divider(color: palette.border, height: 32),
                  TextField(
                    controller: _projectNameController,
                    decoration: const InputDecoration(
                      labelText: 'Project Name',
                      hintText: 'e.g. Freelance Mobile App',
                    ),
                    style: TextStyle(color: palette.textPrimary),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _projectDescController,
                    maxLines: 4,
                    decoration: const InputDecoration(
                      labelText: 'Project Description / Outline',
                      hintText: 'Describe details to generate template plan',
                    ),
                    style: TextStyle(color: palette.textPrimary),
                  ),
                  const SizedBox(height: 20),
                  ui.creating
                      ? Center(child: CircularProgressIndicator(color: palette.brand))
                      : ElevatedButton.icon(
                          onPressed: () async {
                            final created = await vm.generateWorkspace(
                              name: _projectNameController.text,
                              description: _projectDescController.text,
                            );
                            if (created) {
                              _projectNameController.clear();
                              _projectDescController.clear();
                            }
                          },
                          icon: const Icon(Icons.create_new_folder),
                          label: const Text('Generate Drive Workspace'),
                          style: ElevatedButton.styleFrom(backgroundColor: palette.success),
                        ),
                ],
              ),
            ),
          ),
          if (ui.logs.isNotEmpty) ...[
            const SizedBox(height: 20),
            Card(
              color: palette.background,
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Automation Activity Console',
                      style: TextStyle(color: palette.brandOnSurfaceText, fontWeight: FontWeight.bold, fontSize: 12),
                    ),
                    const SizedBox(height: 8),
                    ...ui.logs.map((log) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 4.0),
                          child: Text(
                            log,
                            style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.success),
                          ),
                        )),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
