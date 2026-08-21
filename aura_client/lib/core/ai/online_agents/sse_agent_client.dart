import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../auth/token_manager.dart';
import '../../config/config.dart';
import '../../security/device_validation_service.dart';
import 'llm_timeouts.dart';

enum SseEventKind { token, toolCalls, step, actions, citations, revision, done, error, unknown }

/// One decoded frame from the `/api/v3/chat/stream` SSE stream
/// (`aura_core/kernel/kernel.py`'s `AIKernel.stream()`).
class SseChatEvent {
  final SseEventKind kind;
  final String? text;
  final List<dynamic>? actions;
  final String? message;
  final List<dynamic>? citations;

  /// The raw `event:` name, populated only when [kind] is
  /// [SseEventKind.unknown] — lets a caller log/ignore a frame type this
  /// client doesn't know about yet without misreading it as [SseEventKind.done].
  final String? unknownType;

  const SseChatEvent._({
    required this.kind,
    this.text,
    this.actions,
    this.message,
    this.citations,
    this.unknownType,
  });

  factory SseChatEvent.fromWire(String eventType, Map<String, dynamic> data) {
    switch (eventType) {
      case 'token':
        return SseChatEvent._(kind: SseEventKind.token, text: data['text'] as String?);
      case 'tool_calls':
        return SseChatEvent._(kind: SseEventKind.toolCalls, actions: data['calls'] as List<dynamic>?);
      case 'step':
        return SseChatEvent._(kind: SseEventKind.step);
      case 'actions':
        return SseChatEvent._(kind: SseEventKind.actions, actions: data['actions'] as List<dynamic>?);
      case 'citations':
        return SseChatEvent._(kind: SseEventKind.citations, citations: data['citations'] as List<dynamic>?);
      case 'revision':
        return SseChatEvent._(kind: SseEventKind.revision, text: data['text'] as String?);
      case 'error':
        return SseChatEvent._(kind: SseEventKind.error, message: data['message'] as String?);
      case 'done':
        // citations is repeated here as a safety net by the backend in case the
        // earlier dedicated `citations` frame was missed.
        return SseChatEvent._(kind: SseEventKind.done, citations: data['citations'] as List<dynamic>?);
      default:
        // Anything not recognised above must NOT fall through to `done` --
        // that used to silently discard `citations`/`revision` frames by
        // misreading them as stream completion. Callers ignore `unknown`.
        return SseChatEvent._(kind: SseEventKind.unknown, unknownType: eventType);
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
  static const String _url = "${AppConfig.backendUrl}/api/v3/chat/stream";

  SseAgentClient(this._ref);

  /// Streams one chat turn from the v3 kernel (`aura_core/api/v3.py`'s
  /// `ChatRequest`). Conversation history is loaded server-side from
  /// episodic memory keyed by [sessionId] -- unlike the legacy v1/v2 path,
  /// the client does not send a message history array.
  Stream<SseChatEvent> streamAgentEvents({
    required String message,
    required String sessionId,
    required String agentId,
    bool useKnowledge = true,
    bool useMemory = true,
    bool useTools = true,
    int maxOutputTokens = 1024,
    double temperature = 0.3,
  }) async* {
    final client = http.Client();
    try {
      final request = http.Request('POST', Uri.parse(_url));
      Future<void> applyAuth(http.Request req) async {
        req.headers['Content-Type'] = 'application/json';
        final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
        if (token != null) req.headers['Authorization'] = 'Bearer $token';
        req.headers.addAll(await DeviceValidationService.getAttestationHeaders());
      }

      await applyAuth(request);
      request.body = jsonEncode({
        "message": message,
        "session_id": sessionId,
        "agent": agentId,
        "use_knowledge": useKnowledge,
        "use_memory": useMemory,
        "use_tools": useTools,
        "max_output_tokens": maxOutputTokens,
        "temperature": temperature,
      });

      var response = await client.send(request).timeout(kLlmConnectTimeout);
      if (response.statusCode == 401) {
        _ref.read(tokenManagerProvider).invalidateAccessToken();
        final retry = http.Request('POST', Uri.parse(_url));
        await applyAuth(retry);
        retry.body = request.body;
        response = await client.send(retry).timeout(kLlmConnectTimeout);
      }
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
