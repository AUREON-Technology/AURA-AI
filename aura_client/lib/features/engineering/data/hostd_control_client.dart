import 'dart:convert';

import 'package:http/http.dart' as http;

/// Talks to `aura_hostd`'s loopback control plane (`http://127.0.0.1:8765`).
class HostdControlClient {
  HostdControlClient({
    this.baseUrl = 'http://127.0.0.1:8765',
    http.Client? httpClient,
  }) : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;

  Future<HostdStatus?> status() async {
    try {
      final response = await _http
          .get(Uri.parse('$baseUrl/status'))
          .timeout(const Duration(seconds: 2));
      if (response.statusCode != 200) return null;
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      return HostdStatus.fromJson(body);
    } catch (_) {
      return null;
    }
  }

  Future<bool> setYolo(bool enabled) async {
    try {
      final response = await _http
          .post(
            Uri.parse('$baseUrl/yolo'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'enabled': enabled}),
          )
          .timeout(const Duration(seconds: 3));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> grant(String path, {bool trusted = false}) async {
    try {
      final response = await _http
          .post(
            Uri.parse('$baseUrl/grant'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'path': path, 'trusted': trusted}),
          )
          .timeout(const Duration(seconds: 5));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> revoke(String path) async {
    try {
      final response = await _http
          .post(
            Uri.parse('$baseUrl/revoke'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'path': path}),
          )
          .timeout(const Duration(seconds: 5));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> setTrusted(String path, {required bool trusted}) async {
    try {
      final response = await _http
          .post(
            Uri.parse('$baseUrl/${trusted ? 'trust' : 'untrust'}'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'path': path}),
          )
          .timeout(const Duration(seconds: 5));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }
}

class HostdStatus {
  const HostdStatus({
    required this.connected,
    required this.yolo,
    required this.backendUrl,
    required this.hasAuthToken,
    required this.roots,
  });

  final bool connected;
  final bool yolo;
  final String backendUrl;
  final bool hasAuthToken;
  final List<HostdRoot> roots;

  factory HostdStatus.fromJson(Map<String, dynamic> json) {
    final rootsRaw = json['roots'] as List<dynamic>? ?? const [];
    return HostdStatus(
      connected: json['connected'] == true,
      yolo: json['yolo'] == true,
      backendUrl: json['backend_url'] as String? ?? '',
      hasAuthToken: json['has_auth_token'] == true,
      roots: [
        for (final item in rootsRaw)
          HostdRoot(
            path: (item as Map<String, dynamic>)['path'] as String? ?? '',
            trusted: item['trusted'] == true,
          ),
      ],
    );
  }
}

class HostdRoot {
  const HostdRoot({required this.path, required this.trusted});
  final String path;
  final bool trusted;
}
