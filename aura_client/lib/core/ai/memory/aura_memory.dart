import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';
import '../../../features/chat/data/memory_repository.dart';

class ShortTermMemory {
  final List<Map<String, String>> _messages = [];

  void addMessage(String role, String content) {
    _messages.add({"role": role, "content": content});
    if (_messages.length > 20) {
      _messages.removeAt(0); // Keep last 20 messages as sliding window
    }
  }

  List<Map<String, String>> getHistory() => List.unmodifiable(_messages);
  void clear() => _messages.clear();
}

class LongTermMemory {
  final AppDatabase _db;
  LongTermMemory(this._db);

  Future<List<UserMemory>> loadMemories() async {
    return _db.select(_db.userMemories).get();
  }

  Future<void> saveFact(String fact) async {
    final id = (fact.hashCode & 0xFFFFFFFFFFFFFFFF).toString();
    await _db.into(_db.userMemories).insertOnConflictUpdate(
      UserMemoriesCompanion.insert(
        id: id,
        fact: fact,
        createdAt: DateTime.now(),
      ),
    );
  }
}

class SemanticMemory {
  final MemoryRepository _repo;
  SemanticMemory(this._repo);

  /// Performs a semantic/keyword search on the memories sync bank
  Future<List<MemoryItem>> queryMemories(String prompt) async {
    final all = await _repo.syncMemories();
    final query = prompt.toLowerCase();
    
    // Fallback search criteria matching keywords client-side
    return all.where((item) {
      final content = item.content.toLowerCase();
      return content.contains(query) ||
          query.split(' ').any((word) => word.length > 3 && content.contains(word));
    }).toList();
  }
}

class TaskMemory {
  final AppDatabase _db;
  TaskMemory(this._db);

  Future<List<Task>> loadActiveTasks() async {
    return (_db.select(_db.tasks)..where((t) => t.isCompleted.equals(false))).get();
  }

  Future<List<Task>> loadTasksByCategory(String category) async {
    return (_db.select(_db.tasks)..where((t) => t.category.equals(category))).get();
  }
}

class AuraMemory {
  final ShortTermMemory shortTerm = ShortTermMemory();
  late final LongTermMemory longTerm;
  late final SemanticMemory semantic;
  late final TaskMemory task;

  AuraMemory(AppDatabase db, MemoryRepository repo) {
    longTerm = LongTermMemory(db);
    semantic = SemanticMemory(repo);
    task = TaskMemory(db);
  }
}

final auraMemoryProvider = Provider<AuraMemory>((ref) {
  final db = ref.watch(databaseProvider);
  final repo = ref.watch(memoryRepositoryProvider);
  return AuraMemory(db, repo);
});
