import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';

/// One row from `GET /api/v3/engineering/runs` -- a durable record of what an engineer-agent
/// tool call did (PR opened, branch created, sandbox command run), outliving the chat turn that
/// produced it. Read-only from the client's perspective: there is no create/update method here,
/// matching the backend's `EngineerRunStore` port, which the client never writes to directly --
/// every row is server-authored from an actual tool execution.
class EngineerRun {
  final String id;
  final String turnId;
  final String correlationId;
  final String persona;
  final String tool;
  final String callId;
  final String status; // pending | succeeded | failed
  final String? prUrl;
  final String? branch;
  final String? commitSha;
  final String? sandboxTranscript;
  final DateTime createdAt;
  final DateTime updatedAt;

  const EngineerRun({
    required this.id,
    required this.turnId,
    required this.correlationId,
    required this.persona,
    required this.tool,
    required this.callId,
    required this.status,
    this.prUrl,
    this.branch,
    this.commitSha,
    this.sandboxTranscript,
    required this.createdAt,
    required this.updatedAt,
  });

  factory EngineerRun.fromJson(Map<String, dynamic> json) {
    return EngineerRun(
      id: json['id']?.toString() ?? '',
      turnId: json['turn_id']?.toString() ?? '',
      correlationId: json['correlation_id']?.toString() ?? '',
      persona: json['persona']?.toString() ?? '',
      tool: json['tool']?.toString() ?? '',
      callId: json['call_id']?.toString() ?? '',
      status: json['status']?.toString() ?? 'pending',
      prUrl: json['pr_url']?.toString(),
      branch: json['branch']?.toString(),
      commitSha: json['commit_sha']?.toString(),
      sandboxTranscript: json['sandbox_transcript']?.toString(),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
      updatedAt: DateTime.tryParse(json['updated_at']?.toString() ?? '') ?? DateTime.now(),
    );
  }
}

/// Engineer Task History: every engineer-agent tool outcome for the signed-in user, newest
/// first. `autoDispose` so opening the history screen always sees fresh data rather than a stale
/// cached list from a much earlier session.
final engineerRunsProvider = FutureProvider.autoDispose<List<EngineerRun>>((ref) async {
  final token = await ref.read(tokenManagerProvider).getValidAccessToken();
  final backendUrl = ref.read(backendUrlProvider);
  final response = await http.get(
    Uri.parse('$backendUrl/api/v3/engineering/runs'),
    headers: {if (token != null) 'Authorization': 'Bearer $token'},
  );
  if (response.statusCode != 200) {
    throw Exception('Failed to load engineer runs: ${response.statusCode}');
  }
  final decoded = jsonDecode(response.body) as Map<String, dynamic>;
  return (decoded['runs'] as List<dynamic>? ?? [])
      .map((r) => EngineerRun.fromJson(r as Map<String, dynamic>))
      .toList();
});
