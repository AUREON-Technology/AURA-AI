import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../chat/data/memory_repository.dart';

const kMemoryKinds = [
  'working',
  'episodic',
  'semantic',
  'entity',
  'procedural',
  'reflection',
];

class MemoryUiState {
  final String? kindFilter;
  final bool loading;
  final String? error;
  final List<MemoryItem> items;

  const MemoryUiState({
    this.kindFilter,
    this.loading = false,
    this.error,
    this.items = const [],
  });

  List<MemoryItem> get visible {
    final filter = kindFilter;
    if (filter == null) return items;
    return items.where((i) => i.kind == filter).toList();
  }

  MemoryUiState copyWith({
    String? kindFilter,
    bool clearFilter = false,
    bool? loading,
    String? error,
    bool clearError = false,
    List<MemoryItem>? items,
  }) {
    return MemoryUiState(
      kindFilter: clearFilter ? null : (kindFilter ?? this.kindFilter),
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
      items: items ?? this.items,
    );
  }
}

class MemoryViewModel extends Notifier<MemoryUiState> {
  @override
  MemoryUiState build() {
    Future.microtask(refresh);
    return const MemoryUiState(loading: true);
  }

  Future<void> refresh() async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final items = await ref.read(memoryRepositoryProvider).syncMemories();
      state = state.copyWith(loading: false, items: items);
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  void setFilter(String? kind) {
    state = kind == null
        ? state.copyWith(clearFilter: true)
        : state.copyWith(kindFilter: kind);
  }

  Future<void> add({
    required String content,
    required String kind,
    String? subject,
  }) async {
    await ref.read(memoryRepositoryProvider).saveMemory(
          content,
          kind: kind,
          subject: subject,
        );
    await refresh();
  }

  Future<void> remove(String id) async {
    await ref.read(memoryRepositoryProvider).deleteMemory(id);
    await refresh();
  }
}

final memoryViewModelProvider =
    NotifierProvider<MemoryViewModel, MemoryUiState>(MemoryViewModel.new);
