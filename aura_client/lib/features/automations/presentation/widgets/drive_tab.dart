import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/theme/theme.dart';
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
    if (widget.isMock) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: AuraTheme.accentAmber),
              SizedBox(height: 16),
              Text(
                'Google Drive Workspace Creation Unavailable',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              SizedBox(height: 8),
              Text(
                'You are signed in via a mock developer profile. Drive workspace directories can only be generated when logged in with a real Google Account.',
                textAlign: TextAlign.center,
                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
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
                  const Text(
                    'Enter project details. AURA will generate a Google Drive folder and populate it with a structured Markdown project outline document.',
                    style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                  ),
                  const Divider(color: Color(0x1F94A3B8), height: 32),
                  TextField(
                    controller: _projectNameController,
                    decoration: const InputDecoration(
                      labelText: 'Project Name',
                      hintText: 'e.g. Freelance Mobile App',
                    ),
                    style: const TextStyle(color: Colors.white),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _projectDescController,
                    maxLines: 4,
                    decoration: const InputDecoration(
                      labelText: 'Project Description / Outline',
                      hintText: 'Describe details to generate template plan',
                    ),
                    style: const TextStyle(color: Colors.white),
                  ),
                  const SizedBox(height: 20),
                  ui.creating
                      ? const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo))
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
                          style: ElevatedButton.styleFrom(backgroundColor: AuraTheme.accentTeal),
                        ),
                ],
              ),
            ),
          ),
          if (ui.logs.isNotEmpty) ...[
            const SizedBox(height: 20),
            Card(
              color: const Color(0xFF07040D),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'Automation Activity Console',
                      style: TextStyle(color: AuraTheme.accentIndigo, fontWeight: FontWeight.bold, fontSize: 12),
                    ),
                    const SizedBox(height: 8),
                    ...ui.logs.map((log) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 4.0),
                          child: Text(
                            log,
                            style: const TextStyle(fontFamily: 'Courier', fontSize: 12, color: Colors.greenAccent),
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
