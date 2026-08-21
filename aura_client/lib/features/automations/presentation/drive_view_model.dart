import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/automation_repository.dart';

@immutable
class DriveUiState {
  final bool creating;
  final List<String> logs;

  const DriveUiState({
    this.creating = false,
    this.logs = const [],
  });

  DriveUiState copyWith({bool? creating, List<String>? logs}) {
    return DriveUiState(
      creating: creating ?? this.creating,
      logs: logs ?? this.logs,
    );
  }
}

class DriveViewModel extends Notifier<DriveUiState> {
  @override
  DriveUiState build() => const DriveUiState();

  Future<bool> generateWorkspace({required String name, required String description}) async {
    final trimmedName = name.trim();
    final trimmedDesc = description.trim();
    if (trimmedName.isEmpty || trimmedDesc.isEmpty) return false;

    state = state.copyWith(creating: true, logs: const ['Initializing project workspace folders...']);
    try {
      final logs = await ref.read(automationRepositoryProvider).createProjectWorkspace(
            name: trimmedName,
            description: trimmedDesc,
          );
      state = state.copyWith(creating: false, logs: logs);
      return true;
    } catch (e) {
      state = state.copyWith(
        creating: false,
        logs: [...state.logs, 'Workspace creation failed: $e'],
      );
      return false;
    }
  }
}

final driveViewModelProvider = NotifierProvider<DriveViewModel, DriveUiState>(DriveViewModel.new);
