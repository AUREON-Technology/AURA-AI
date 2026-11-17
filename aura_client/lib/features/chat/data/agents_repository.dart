import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/l10n/l10n.dart';

/// One persona from `GET /api/v3/agents` (`aura_core/agents/registry.py`'s
/// `AgentRegistry`, plus any `bp_*` agents a deployed blueprint adds).
/// Distinct from `agent_config_repository.dart`'s per-agent custom
/// system-prompt overrides, which have no v3 equivalent and are not
/// surfaced by the rebuilt chat screen.
class AgentInfo {
  final String id;
  final String description;
  final String version;
  // Whether *this caller's* granted capabilities satisfy the persona's required_capabilities
  // (backend-computed, `AgentRegistry.resolve()`'s own check mirrored for the client). A picker
  // must never let the user select a persona where this is false -- doing so doesn't error, it
  // silently answers as the orchestrator instead (see `requiredCapabilities` below).
  final bool usable;
  // The capabilities `AgentRegistry.resolve()` requires before it will resolve to this persona
  // rather than silently falling back to the orchestrator. Empty for personas with no gate.
  final Set<String> requiredCapabilities;
  // Null means "no restriction beyond the caller's own capabilities" (the orchestrator); a list
  // is this persona's actual tool grant.
  final List<String>? allowedTools;
  final String riskCeiling;

  const AgentInfo({
    required this.id,
    required this.description,
    required this.version,
    this.usable = true,
    this.requiredCapabilities = const {},
    this.allowedTools,
    this.riskCeiling = '',
  });

  factory AgentInfo.fromJson(Map<String, dynamic> json) {
    final rawRequired = json['required_capabilities'];
    final rawAllowed = json['allowed_tools'];
    return AgentInfo(
      id: json['id'] as String? ?? '',
      description: json['description'] as String? ?? '',
      version: json['version'] as String? ?? '',
      // Absent on an un-upgraded backend -- default true so an older server's behavior (no
      // client-side gating at all) is unchanged rather than everything becoming unselectable.
      usable: json['usable'] as bool? ?? true,
      requiredCapabilities: rawRequired is List
          ? rawRequired.map((c) => c.toString()).toSet()
          : const {},
      allowedTools: rawAllowed is List ? rawAllowed.map((t) => t.toString()).toList() : null,
      riskCeiling: json['risk_ceiling'] as String? ?? '',
    );
  }
}

/// Icon + display label for a known persona id. Falls back to a
/// title-cased id + generic icon for anything the backend adds later that
/// this map doesn't know about yet, so a new persona never breaks the UI.
Map<String, (String, IconData)> _knownPersonas(AppLocalizations l10n) => {
      'orchestrator': (l10n.chatPersonaOrchestrator, Icons.hub_outlined),
      'task_planner': (l10n.chatPersonaTaskPlanner, Icons.checklist_outlined),
      'finance_advisor': (l10n.chatPersonaFinanceAdvisor, Icons.account_balance_outlined),
      'health_companion': (l10n.chatPersonaHealthCompanion, Icons.favorite_outline),
      'research_agent': (l10n.chatPersonaResearchAgent, Icons.travel_explore_outlined),
      'coding_agent': (l10n.chatPersonaCodingAgent, Icons.code_outlined),
      'decision_agent': (l10n.chatPersonaDecisionAgent, Icons.balance_outlined),
      // The gated persona backing tools/coding.py's sandbox + GitHub tools -- distinct from the
      // much weaker 'coding_agent' above (read-only tools, no capability gate).
      'engineer': (l10n.chatPersonaEngineer, Icons.build_circle_outlined),
    };

/// Labels are read in the language on screen ([currentL10n]) when called, i.e. at build time.
(String label, IconData icon) personaDisplay(String agentId) {
  final known = _knownPersonas(currentL10n)[agentId];
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
  final backendUrl = ref.read(backendUrlProvider);
  final response = await http.get(
    Uri.parse('$backendUrl/api/v3/agents'),
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
