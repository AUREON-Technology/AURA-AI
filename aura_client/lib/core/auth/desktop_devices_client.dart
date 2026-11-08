import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../config/backend_url_provider.dart';
import '../security/device_validation_service.dart';
import 'token_manager.dart';

/// One desktop device the signed-in account owns: a desktop-app sign-in (`kind == "app"`) or a
/// paired Engineer daemon (`kind == "daemon"`). Mirrors `DesktopDevice` in
/// `aura_backend/api/v2/auth.py`.
@immutable
class DesktopDevice {
  const DesktopDevice({
    required this.deviceId,
    required this.kind,
    this.name,
    this.createdAt,
    this.current = false,
  });

  final String deviceId;
  final String kind;
  final String? name;
  final DateTime? createdAt;

  /// Whether the token this list was fetched with belongs to this very device.
  final bool current;

  factory DesktopDevice.fromJson(Map<String, dynamic> json) => DesktopDevice(
        deviceId: json['device_id'] as String,
        kind: (json['kind'] as String?) ?? 'unknown',
        name: json['name'] as String?,
        createdAt: json['created_at'] is String ? DateTime.tryParse(json['created_at'] as String) : null,
        current: json['current'] == true,
      );
}

/// Thin client for `GET /api/v3/auth/desktop/devices` and `POST /api/v3/auth/desktop/devices/revoke`
/// (audit FSA-004): the account owner's view of every desktop sign-in and paired daemon, and the
/// one place in the app a device can be revoked. Same JWT + attestation header pattern as every
/// other `core/*` client.
class DesktopDevicesClient {
  DesktopDevicesClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  final Ref _ref;
  final http.Client? _http;

  String get _authUrl => '${_ref.read(backendUrlProvider)}/api/v3/auth';

  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    if (token == null) throw StateError('not signed in');
    final headers = <String, String>{
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(backendUrl: _ref.read(backendUrlProvider)),
    );
    return headers;
  }

  Future<List<DesktopDevice>> list() async {
    final uri = Uri.parse('$_authUrl/desktop/devices');
    final headers = await _headers();
    final response = await (_http?.get(uri, headers: headers) ?? http.get(uri, headers: headers))
        .timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw http.ClientException('devices: HTTP ${response.statusCode}', uri);
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return (body['devices'] as List<dynamic>? ?? const [])
        .map((d) => DesktopDevice.fromJson(d as Map<String, dynamic>))
        .toList();
  }

  /// Revokes [deviceId]. The server refuses (404) a device this account does not own.
  Future<void> revoke(String deviceId) async {
    final uri = Uri.parse('$_authUrl/desktop/devices/revoke');
    final headers = await _headers();
    final body = jsonEncode({'device_id': deviceId});
    final response = await (_http?.post(uri, headers: headers, body: body) ??
            http.post(uri, headers: headers, body: body))
        .timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw http.ClientException('revoke: HTTP ${response.statusCode}', uri);
    }
  }
}

final desktopDevicesClientProvider = Provider<DesktopDevicesClient>((ref) => DesktopDevicesClient(ref));
