import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/project_repository.dart';

final projectsStreamProvider = StreamProvider<List<Project>>((ref) {
  final repo = ref.watch(projectRepositoryProvider);
  return repo.watchProjects();
});

final projectCategoryFilterProvider = StateProvider<String>((ref) => 'All');