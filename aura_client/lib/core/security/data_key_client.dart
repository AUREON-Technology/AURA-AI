import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import 'device_validation_service.dart';
import 'encryption_service.dart';

/// Fetches the account data key from `GET /api/v3/keys/data` (ADR-032) and
/// installs it in [EncryptionService].
///
/// The key is shared with the server: that is what lets the assistant read
/// and write this account's tasks, events and records, and what lets every
/// device of the account read what the others wrote. It travels only over the
/// authenticated session (TLS in release builds -- `BackendUrlPolicy`), is
/// never logged here, and is kept in secure storage.
///
/// Offline-first: any failure (no session, no network, a 503 from a backend
/// whose database is not migrated yet, a malformed answer) returns false and
/// the app keeps working on the device's legacy key. The next sync retries.
class DataKeyClient {
  final Ref _ref;
  final http.Client? _http;

  DataKeyClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  String get _backendUrl => _ref.read(backendUrlProvider);

  /// True when keys were fetched and installed.
  Future<bool> fetchAndInstall() async {
    try {
      final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
      if (token == null) return false;
      final headers = <String, String>{
        'Authorization': 'Bearer $token',
        'Cache-Control': 'no-store',
      };
      headers.addAll(
        await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
      );
      final uri = Uri.parse('$_backendUrl/api/v3/keys/data');
      final client = _http;
      final response = await (client != null
              ? client.get(uri, headers: headers)
              : http.get(uri, headers: headers))
          .timeout(const Duration(seconds: 20));
      if (response.statusCode != 200) {
        debugPrint('[DataKeyClient] key fetch refused: HTTP ${response.statusCode}');
        return false;
      }
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (body['format'] != 'gcm2') return false;
      final keys = <String, String>{
        for (final entry in body['keys'] as List<dynamic>)
          (entry as Map<String, dynamic>)['kid'] as String: entry['key'] as String,
      };
      await EncryptionService.installDataKeys(body['primary'] as String, keys);
      return true;
    } catch (e) {
      // Never include the response body: on success it is the key itself.
      debugPrint('[DataKeyClient] key fetch failed (${e.runtimeType}); staying on the device key');
      return false;
    }
  }
}

final dataKeyClientProvider = Provider<DataKeyClient>((ref) => DataKeyClient(ref));
