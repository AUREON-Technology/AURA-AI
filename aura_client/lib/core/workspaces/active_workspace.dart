// The workspace the app is working in (SaaS P1-F2, ADR-034).
//
// Personal is the default and needs no header: the server scopes to the person. When a team is
// active, every workspace-scoped request carries `X-Aura-Workspace` and the server checks
// membership on each one. The choice survives restarts (shared_preferences).

import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// One workspace the signed-in person belongs to, as `GET /api/v3/workspaces` lists it.
@immutable
class WorkspaceSummary {
  const WorkspaceSummary({required this.id, required this.kind, required this.name, required this.role});

  final String id;

  /// `personal` or `team`.
  final String kind;
  final String name;

  /// `owner`, `admin` or `member`.
  final String role;

  bool get isTeam => kind == 'team';
  bool get canManage => role == 'owner' || role == 'admin';
  bool get isOwner => role == 'owner';

  factory WorkspaceSummary.fromJson(Map<String, dynamic> json) => WorkspaceSummary(
        id: '${json['id']}',
        kind: '${json['kind']}',
        name: '${json['name'] ?? ''}',
        role: '${json['role'] ?? 'member'}',
      );

  Map<String, dynamic> toJson() => {'id': id, 'kind': kind, 'name': name, 'role': role};

  @override
  bool operator ==(Object other) =>
      other is WorkspaceSummary && other.id == id && other.kind == kind && other.name == name && other.role == role;

  @override
  int get hashCode => Object.hash(id, kind, name, role);
}

/// The server's answer when a request names a workspace the caller no longer belongs to
/// (`aura_core/tenancy/service.py`'s `_NOT_A_MEMBER`).
const notAMemberMessage = 'not a member of this workspace';

/// Header name the gateway reads (`aura_service.WORKSPACE_HEADER`).
const workspaceHeaderName = 'X-Aura-Workspace';

class ActiveWorkspace extends Notifier<WorkspaceSummary?> {
  static const prefsKey = 'aura.active_workspace';

  /// Completes once the saved choice is loaded: a request awaits it, so nothing goes out in the
  /// personal scope while the app is still restoring a team (F2 review #6).
  late Future<void> ready;

  @override
  WorkspaceSummary? build() {
    ready = restore();
    return null;
  }

  /// Loads the saved choice. Called once on build; awaitable for tests.
  Future<void> restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(prefsKey);
      if (raw == null) return;
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) state = WorkspaceSummary.fromJson(decoded);
    } catch (_) {
      // A corrupt entry is not worth failing start-up over: personal is a safe default.
    }
  }

  /// Keeps the saved summary true to the server's list: a changed role or name is updated, and a
  /// team that is no longer ours is dropped (F2 review #5).
  Future<void> reconcile(List<WorkspaceSummary> listed) async {
    final active = state;
    if (active == null) return;
    final fresh = listed.where((w) => w.id == active.id).firstOrNull;
    if (fresh == null) {
      await clear();
    } else if (fresh != active) {
      await select(fresh);
    }
  }

  Future<void> select(WorkspaceSummary workspace) async {
    state = workspace;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(prefsKey, jsonEncode(workspace.toJson()));
  }

  /// Back to personal: on sign-out, or when the server says the team is no longer ours.
  Future<void> clear() async {
    state = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(prefsKey);
  }
}

final activeWorkspaceProvider = NotifierProvider<ActiveWorkspace, WorkspaceSummary?>(ActiveWorkspace.new);

/// `X-Aura-Workspace` when a team is active; nothing for personal (the server's default). Waits
/// for the saved choice to load first.
Future<Map<String, String>> workspaceHeaders(Ref ref) async {
  ref.read(activeWorkspaceProvider);
  await ref.read(activeWorkspaceProvider.notifier).ready;
  final active = ref.read(activeWorkspaceProvider);
  return active != null && active.isTeam ? {workspaceHeaderName: active.id} : const {};
}

/// Called with any error response from a request that carried [workspaceHeaders]: a 403 saying
/// we are not a member means the team is gone or we were removed, so fall back to personal
/// instead of failing every later request the same way.
void healActiveWorkspace(Ref ref, {required int statusCode, required String body}) {
  if (statusCode == 403 && body.contains(notAMemberMessage) && ref.read(activeWorkspaceProvider) != null) {
    ref.read(activeWorkspaceProvider.notifier).clear();
  }
}
