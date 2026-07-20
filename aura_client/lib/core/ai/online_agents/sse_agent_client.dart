import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:google_sign_in/google_sign_in.dart';
import 'package:flutter/foundation.dart';
import '../../auth/token_manager.dart';
import '../../config/config.dart';
import '../../security/device_validation_service.dart';

enum SseEventKind { token, toolCalls, done, error }

/// One decoded frame from the `/api/v2/chat` SSE stream (Doc/V2_BLUEPRINT.md
/// §7). Replaces string-splitting `[ACTIONS]` out of a raw text stream
/// (V1's approach, still used by the v1 `/api/chat/agent` endpoint this
/// falls back to) with typed events the caller switches on directly.
class SseChatEvent {
  final SseEventKind kind;
  final String? text;
  final List<dynamic>? actions;
  final String? message;

  const SseChatEvent._({required this.kind, this.text, this.actions, this.message});

  factory SseChatEvent.fromWire(String eventType, Map<String, dynamic> data) {
    switch (eventType) {
      case 'token':
        return SseChatEvent._(kind: SseEventKind.token, text: data['text'] as String?);
      case 'tool_calls':
        return SseChatEvent._(kind: SseEventKind.toolCalls, actions: data['actions'] as List<dynamic>?);
      case 'error':
        return SseChatEvent._(kind: SseEventKind.error, message: data['message'] as String?);
      case 'done':
      default:
        return const SseChatEvent._(kind: SseEventKind.done);
    }
  }
}

/// Parses a `text/event-stream` body delivered as arbitrary chunks (an
/// HTTP chunk boundary can land anywhere, including mid-frame) into
/// complete [SseChatEvent]s. Pure/stateful-but-network-free so it's unit
/// tested directly in test/sse_frame_parser_test.dart without a server.
class SseFrameParser {
  final StringBuffer _buffer = StringBuffer();

  List<SseChatEvent> feed(String chunk) {
    _buffer.write(chunk);
    final text = _buffer.toString();
    final frames = text.split('\n\n');

    // The last split segment is either empty (chunk ended exactly on a
    // frame boundary) or an incomplete frame — hold it back either way.
    final complete = frames.sublist(0, frames.length - 1);
    _buffer
      ..clear()
      ..write(frames.last);

    final events = <SseChatEvent>[];
    for (final frame in complete) {
      final event = _parseFrame(frame);
      if (event != null) events.add(event);
    }
    return events;
  }

  SseChatEvent? _parseFrame(String frame) {
    String? eventType;
    String? dataLine;
    for (final line in frame.split('\n')) {
      if (line.startsWith('event: ')) eventType = line.substring(7).trim();
      if (line.startsWith('data: ')) dataLine = line.substring(6);
    }
    if (eventType == null || dataLine == null) return null;

    Map<String, dynamic> data = const {};
    try {
      final decoded = jsonDecode(dataLine);
      if (decoded is Map<String, dynamic>) data = decoded;
    } catch (_) {
      // Malformed data payload — treat as an empty-data event of this type
      // rather than dropping it silently or throwing mid-stream.
    }
    return SseChatEvent.fromWire(eventType, data);
  }
}

class SseAgentClient {
  final Ref _ref;
  static const String _url = "${AppConfig.backendUrl}/api/v2/chat";

  SseAgentClient(this._ref);

  Stream<SseChatEvent> streamAgentEvents({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();

    String? googleAccessToken;
    try {
      final scopes = ['https://www.googleapis.com/auth/drive.readonly'];
      final authorization = await GoogleSignIn.instance.authorizationClient.authorizeScopes(scopes);
      googleAccessToken = authorization.accessToken;
    } catch (e) {
      debugPrint("[SseAgentClient] Failed to fetch Google access token: $e");
    }

    final client = http.Client();
    try {
      final request = http.Request('POST', Uri.parse(_url));
      request.headers['Content-Type'] = 'application/json';
      if (token != null) request.headers['Authorization'] = 'Bearer $token';
      request.headers.addAll(DeviceValidationService.getAttestationHeaders());
      request.body = jsonEncode({
        "agent_id": agentId,
        "messages": messages.map((m) => {"role": m["role"], "content": m["content"]}).toList(),
        "custom_instructions": customInstructions,
        "use_rag": useRag,
        "google_access_token": googleAccessToken,
      });

      final response = await client.send(request).timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) {
        final body = await response.stream.bytesToString();
        throw Exception("SSE chat request failed with status ${response.statusCode}: $body");
      }

      final parser = SseFrameParser();
      await for (final chunk in response.stream.transform(utf8.decoder)) {
        for (final event in parser.feed(chunk)) {
          yield event;
        }
      }
    } finally {
      client.close();
    }
  }
}

final sseAgentClientProvider = Provider<SseAgentClient>((ref) => SseAgentClient(ref));
