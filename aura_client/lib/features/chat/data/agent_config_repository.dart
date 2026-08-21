import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';

class AgentConfigRepository {
  final AppDatabase _db;

  AgentConfigRepository(this._db);

  // Retrieve custom system instructions for all agents
  Future<Map<String, String>> loadAllInstructions() async {
    final configs = await _db.select(_db.agentConfigurations).get();
    final map = <String, String>{};
    for (final config in configs) {
      map[config.agentId] = config.systemInstruction;
    }
    return map;
  }

  // Watch a single agent's instruction
  Stream<AgentConfiguration?> watchInstruction(String agentId) {
    return (_db.select(_db.agentConfigurations)
          ..where((c) => c.agentId.equals(agentId)))
        .watchSingleOrNull();
  }

  // Update or insert system instructions
  Future<void> saveInstruction(String agentId, String systemInstruction) async {
    await _db.into(_db.agentConfigurations).insertOnConflictUpdate(
      AgentConfigurationsCompanion.insert(
        agentId: agentId,
        systemInstruction: systemInstruction,
        updatedAt: DateTime.now(),
      ),
    );
  }

  // Reset to default instructions
  Future<void> deleteInstruction(String agentId) async {
    await (_db.delete(_db.agentConfigurations)
          ..where((c) => c.agentId.equals(agentId)))
        .go();
  }
}

final agentConfigRepositoryProvider = Provider<AgentConfigRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return AgentConfigRepository(db);
});

// Current selected active agent for chat
class ActiveAgent extends Notifier<String> {
  @override
  String build() => 'orchestrator';
  void setAgent(String agentId) => state = agentId;
}

final activeAgentIdProvider = NotifierProvider<ActiveAgent, String>(() {
  return ActiveAgent();
});

// Provider to get all system instructions loaded into memory
final allAgentInstructionsProvider = FutureProvider<Map<String, String>>((ref) async {
  final repo = ref.watch(agentConfigRepositoryProvider);
  return repo.loadAllInstructions();
});
