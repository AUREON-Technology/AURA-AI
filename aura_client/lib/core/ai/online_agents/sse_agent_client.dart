import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';
import '../../auth/token_manager.dart';
import '../../config/config.dart';
import '../../network/aura_api_error.dart';
import '../../security/device_validation_service.dart';
import '../../../features/chat/data/turn_meta.dart';
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
  final String? unknownType;
  final TurnMeta? turnMeta;
  final TurnStep? step;

  const SseChatEvent._({
    required this.kind,
    this.text,
    this.actions,
    this.message,
    this.citations,
    this.unknownType,
    this.turnMeta,
    this.step,
  });

  factory SseChatEvent.fromWire(String eventType, Map<String, dynamic> data) {
    switch (eventType) {
      case 'token':
        return SseChatEvent._(kind: SseEventKind.token, text: data['text'] as String?);
      case 'tool_calls':
        return SseChatEvent._(kind: SseEventKind.toolCalls, actions: data['calls'] as List<dynamic>?);
      case 'step':
        return SseChatEvent._(kind: SseEventKind.step, step: TurnStep.fromJson(data));
      case 'actions':
        return SseChatEvent._(kind: SseEventKind.actions, actions: data['actions'] as List<dynamic>?);
      case 'citations':
        return SseChatEvent._(kind: SseEventKind.citations, citations: data['citations'] as List<dynamic>?);
      case 'revision':
        return SseChatEvent._(kind: SseEventKind.revision, text: data['text'] as String?);
      case 'error':
        return SseChatEvent._(kind: SseEventKind.error, message: data['message'] as String?);
      case 'done':
        return SseChatEvent._(
          kind: SseEventKind.done,
          citations: data['citations'] as List<dynamic>?,
          turnMeta: TurnMeta.fromJson(data),
        );
      default:
        return SseChatEvent._(kind: SseEventKind.unknown, unknownType: eventType);
    }
  }
}

/// Parses a `text/event-stream` body delivered as arbitrary chunks into
/// complete [SseChatEvent]s.
class SseFrameParser {
  final StringBuffer _buffer = StringBuffer();

  List<SseChatEvent> feed(String chunk) {
    _buffer.write(chunk);
    final text = _buffer.toString();
    final frames = text.split('\n\n');

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
    } catch (_) {}
    return SseChatEvent.fromWire(eventType, data);
  }
}

class SseAgentClient {
  final Ref _ref;
  static const String _url = "${AppConfig.backendUrl}/api/v3/chat/stream";
  final _uuid = const Uuid();

  SseAgentClient(this._ref);

  Stream<SseChatEvent> streamAgentEvents({
    required String message,
    required String sessionId,
    required String agentId,
    bool useKnowledge = true,
    bool useMemory = true,
    bool useTools = true,
    List<String> confirmedTools = const [],
    String? correlationId,
    int maxOutputTokens = 1024,
    double temperature = 0.3,
  }) async* {
    final client = http.Client();
    final cid = correlationId ?? _uuid.v4();
    try {
      final request = http.Request('POST', Uri.parse(_url));
      Future<void> applyAuth(http.Request req) async {
        req.headers['Content-Type'] = 'application/json';
        req.headers['X-Correlation-Id'] = cid;
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
        "confirmed_tools": confirmedTools,
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
        throw AuraApiError.fromResponse(
          statusCode: response.statusCode,
          body: body,
          retryAfterHeader: response.headers['retry-after'],
          correlationId: response.headers['x-correlation-id'] ?? cid,
        );
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
