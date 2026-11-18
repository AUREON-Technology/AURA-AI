import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../config/backend_url_provider.dart';
import '../security/device_validation_service.dart';
import 'token_manager.dart';

/// What `POST /api/v3/account/delete` answered (`aura_backend/api/account.py`).
@immutable
class AccountDeletionResult {
  const AccountDeletionResult({required this.forgotten, this.failed = const []});

  /// True when every store erased the account and the account row itself is gone. False
  /// means the server kept the row so the same call can be retried; [failed] names what failed.
  final bool forgotten;
  final List<String> failed;

  factory AccountDeletionResult.fromJson(Map<String, dynamic> json) => AccountDeletionResult(
        forgotten: json['forgotten'] == true,
        failed: [for (final f in (json['failed'] as List<dynamic>? ?? const [])) '$f'],
      );
}

/// The server refused to delete the account because the person owns teams (SaaS P1-F1/F2):
/// each must be handed over or deleted first. Nothing was erased.
class AccountDeletionBlocked implements Exception {
  const AccountDeletionBlocked(this.workspaces);

  /// `(id, name)` of each team the person owns.
  final List<({String id, String name})> workspaces;

  @override
  String toString() => 'AccountDeletionBlocked(${workspaces.length} teams)';
}

/// Thin client for the signed-in user's own account. Today that is one call: deleting it, which
/// both app stores require an app that creates accounts to offer in-app (Apple 5.1.1(v), Google
/// Play's account deletion policy). Same JWT + attestation header pattern as every other
/// `core/*` client.
class AccountClient {
  AccountClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  final Ref _ref;
  final http.Client? _http;

  /// Erases the account on the server: memories, documents, synced records, conversations, the
  /// account row, and every session and paired device. Irreversible. The server revokes the
  /// sessions even when [AccountDeletionResult.forgotten] is false, so the caller signs out
  /// locally whatever this returns.
  Future<AccountDeletionResult> deleteAccount() async {
    final backend = _ref.read(backendUrlProvider);
    final uri = Uri.parse('$backend/api/v3/account/delete');
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    if (token == null) throw StateError('not signed in');
    final headers = <String, String>{
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
      ...await DeviceValidationService.getAttestationHeaders(backendUrl: backend),
    };
    // The server refuses any other body (422): the phrase is the guard against an accidental call.
    final body = jsonEncode({'confirm': 'DELETE'});
    final response = await (_http?.post(uri, headers: headers, body: body) ??
            http.post(uri, headers: headers, body: body))
        // Erasure walks every store; give it longer than an ordinary write.
        .timeout(const Duration(seconds: 60));
    if (response.statusCode == 409) {
      final body = jsonDecode(response.body);
      if (body is Map<String, dynamic> && body['error'] == 'owns_team_workspaces') {
        throw AccountDeletionBlocked([
          for (final w in (body['workspaces'] as List<dynamic>? ?? const []))
            if (w is Map<String, dynamic>) (id: '${w['id']}', name: '${w['name']}'),
        ]);
      }
    }
    if (response.statusCode != 200) {
      throw http.ClientException('account delete: HTTP ${response.statusCode}', uri);
    }
    return AccountDeletionResult.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }
}

final accountClientProvider = Provider<AccountClient>((ref) => AccountClient(ref));
