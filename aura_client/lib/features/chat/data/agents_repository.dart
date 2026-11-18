import 'dart:collection';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
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
  // True on the `engineer` row while this caller has no current Anthropic consent (D-03). The
  // send gate asks `/api/v3/engineering/consent` itself, which also says which notice version
  // the server requires; this is the same fact for anything that only has the agent list.
  final bool consentRequired;

  const AgentInfo({
    required this.id,
    required this.description,
    required this.version,
    this.usable = true,
    this.requiredCapabilities = const {},
    this.allowedTools,
    this.riskCeiling = '',
    this.consentRequired = false,
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
      // Absent on a backend without consent (and on every persona but Engineer).
      consentRequired: json['consent_required'] as bool? ?? false,
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
/// Invalidates [ref]'s provider when the app resumes. No-op where no binding exists yet (a plain
/// unit test reading the provider), since there is no app lifecycle to follow there.
void _refetchOnResume(Ref ref) {
  final AppLifecycleListener listener;
  try {
    listener = AppLifecycleListener(onResume: ref.invalidateSelf);
  } on Object {
    return;
  }
  ref.onDispose(listener.dispose);
}

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
///
/// Through [AuraApiClient] (INT-024): attestation headers, one retry on 401, and a deadline --
/// a bare `http.get` had none of the three, so an expired token showed as "failed to load".
///
/// G-15: refetched when the app comes back to the foreground, so a capability granted (or
/// revoked) while it was in the background shows without a restart; the persona picker's error
/// view also offers Retry.
///
/// Fetched again when the active workspace changes: `usable` for the Engineer depends on the
/// active workspace's plan, so switching to a team that includes it must unlock it at once.
final agentsProvider = FutureProvider<List<AgentInfo>>((ref) async {
  _refetchOnResume(ref);
  // The id only: a rename or a changed role (ActiveWorkspace.reconcile) changes no plan.
  ref.watch(activeWorkspaceProvider.select((w) => w?.id));
  final decoded = await ref.read(auraApiClientProvider).json(
        method: 'GET',
        path: '/api/v3/agents',
      );
  final rows = decoded['agents'];
  final elsewhere = decoded['engineer_workspaces'];
  return AgentList(
    (rows is List ? rows : const [])
        .whereType<Map>()
        .map((a) => AgentInfo.fromJson(a.map((k, v) => MapEntry(k.toString(), v)))),
    engineerWorkspaces: (elsewhere is List ? elsewhere : const [])
        .whereType<Map>()
        .map((w) => WorkspaceSummary.fromJson(w.map((k, v) => MapEntry(k.toString(), v))))
        .where((w) => w.id.isNotEmpty)
        .toList(),
  );
});

/// The persona list, plus the caller's *other* workspaces whose plan includes the Engineer
/// (`engineer_workspaces` on `/api/v3/agents`). Sign-in and pairing accept any of them, but a
/// turn uses the active workspace, so a team member whose own workspace has no Engineer is told
/// where it is and offered a switch instead of a dead end.
class AgentList extends UnmodifiableListView<AgentInfo> {
  AgentList(super.source, {this.engineerWorkspaces = const []});

  final List<WorkspaceSummary> engineerWorkspaces;
}

/// [AgentList.engineerWorkspaces] of the current list; empty while it loads, on an error, on an
/// older backend, and whenever the Engineer is usable where the person is.
final engineerWorkspacesProvider = Provider<List<WorkspaceSummary>>((ref) {
  final agents = ref.watch(agentsProvider).value;
  return agents is AgentList ? agents.engineerWorkspaces : const [];
});
