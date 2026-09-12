import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/features/projects/data/project_repository.dart';

class ProjectsViewModel extends StateNotifier<AsyncValue<void>> {
  final ProjectRepository _repo;

  ProjectsViewModel(this._repo) : super(const AsyncValue.data(null));

  Future<void> addProject({
    required String title,
    String? description,
    required String status,
    DateTime? targetDate,
  }) async {
    state = const AsyncValue.loading();
    try {
      await _repo.addProject(
        title: title,
        description: description,
        status: status,
        targetDate: targetDate,
      );
      state = const AsyncValue.data(null);
    } catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }

  Future<void> updateProjectStatus(String id, String status) async {
    await _repo.updateProjectStatus(id, status);
  }

  Future<void> deleteProject(String id) async {
    await _repo.deleteProject(id);
  }
}

final projectsViewModelProvider = StateNotifierProvider<ProjectsViewModel, AsyncValue<void>>((ref) {
  final repo = ref.watch(projectRepositoryProvider);
  return ProjectsViewModel(repo);
});