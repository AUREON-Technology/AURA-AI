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

  /// The failure itself, not its `toString()`, so the page can render it
  /// through `AuraFailure` (audit F-33).
  final Object? error;
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
    Object? error,
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
    final repository = ref.read(memoryRepositoryProvider);
    try {
      final items = await repository.syncMemories(throwOnFailure: true);
      state = state.copyWith(loading: false, items: items);
    } catch (e) {
      // Still show what this device has, but say the list may be stale.
      List<MemoryItem>? cached;
      try {
        cached = await repository.loadLocalMemories();
      } catch (_) {}
      state = state.copyWith(loading: false, items: cached, error: e);
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

  /// Forgets a memory. Returns whether the server confirmed it; throws when
  /// the delete could not even be recorded on this device. Either way the
  /// page tells the user -- a failed delete used to vanish silently.
  Future<bool> remove(String id) async {
    final confirmed = await ref.read(memoryRepositoryProvider).deleteMemory(id);
    await refresh();
    return confirmed;
  }
}

final memoryViewModelProvider =
    NotifierProvider<MemoryViewModel, MemoryUiState>(MemoryViewModel.new);
