import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../../core/auth/token_manager.dart';
import '../../../core/config/config.dart';

/// One persona from `GET /api/v3/chat/stream`'s sibling endpoint
/// `/api/v3/agents` (`aura_core/prompts/library.py`'s `AGENT_PROMPT_IDS`).
/// Distinct from `agent_config_repository.dart`'s per-agent custom
/// system-prompt overrides, which have no v3 equivalent and are not
/// surfaced by the rebuilt chat screen.
class AgentInfo {
  final String id;
  final String description;
  final String version;

  const AgentInfo({required this.id, required this.description, required this.version});

  factory AgentInfo.fromJson(Map<String, dynamic> json) {
    return AgentInfo(
      id: json['id'] as String? ?? '',
      description: json['description'] as String? ?? '',
      version: json['version'] as String? ?? '',
    );
  }
}

/// Icon + display label for a known persona id. Falls back to a
/// title-cased id + generic icon for anything the backend adds later that
/// this map doesn't know about yet, so a new persona never breaks the UI.
const Map<String, (String, IconData)> _knownPersonas = {
  'orchestrator': ('Orchestrator', Icons.hub_outlined),
  'task_planner': ('Task Planner', Icons.checklist_outlined),
  'finance_advisor': ('Finance Advisor', Icons.account_balance_outlined),
  'health_companion': ('Health Companion', Icons.favorite_outline),
  'research_agent': ('Research Agent', Icons.travel_explore_outlined),
  'coding_agent': ('Coding Agent', Icons.code_outlined),
  'decision_agent': ('Decision Agent', Icons.balance_outlined),
};

(String label, IconData icon) personaDisplay(String agentId) {
  final known = _knownPersonas[agentId];
  if (known != null) return known;
  final label = agentId
      .split('_')
      .where((w) => w.isNotEmpty)
      .map((w) => '${w[0].toUpperCase()}${w.substring(1)}')
      .join(' ');
  return (label.isEmpty ? agentId : label, Icons.smart_toy_outlined);
}

/// Live persona list from the backend. Replaces the client's previously
/// hardcoded list, which named a non-existent "academic_coach" and was
/// missing research_agent/coding_agent/decision_agent -- verified against
/// aura_core/prompts/library.py's actual AGENT_PROMPT_IDS.
final agentsProvider = FutureProvider<List<AgentInfo>>((ref) async {
  final token = await ref.read(tokenManagerProvider).getValidAccessToken();
  final response = await http.get(
    Uri.parse('${AppConfig.backendUrl}/api/v3/agents'),
    headers: {if (token != null) 'Authorization': 'Bearer $token'},
  );
  if (response.statusCode != 200) {
    throw Exception('Failed to load agents: ${response.statusCode}');
  }
  final decoded = jsonDecode(response.body) as Map<String, dynamic>;
  return (decoded['agents'] as List<dynamic>? ?? [])
      .map((a) => AgentInfo.fromJson(a as Map<String, dynamic>))
      .toList();
});
