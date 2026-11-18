import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'dart:async';
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';
import '../../auth/token_manager.dart';
import '../../config/backend_url_provider.dart';
import '../../network/aura_api_error.dart';
import '../../security/device_validation_service.dart';
import '../vision/chat_images.dart';
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

  /// The backend's error slug on an `error` frame (`aura_core/domain/errors.py`'s `kind`,
  /// e.g. `capability_unavailable`), so a caller can branch on it rather than on prose.
  final String? errorKind;

  /// Set on an `error` frame the output guard sent *after* the answer's tokens had already
  /// streamed (`kernel.py`: `"post_hoc": true`): the text on screen is what was withheld.
  final bool postHoc;
  final Object? details;
  final List<dynamic>? citations;
  final String? unknownType;
  final TurnMeta? turnMeta;
  final TurnStep? step;

  const SseChatEvent._({
    required this.kind,
    this.text,
    this.actions,
    this.message,
    this.errorKind,
    this.postHoc = false,
    this.details,
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
        return SseChatEvent._(
          kind: SseEventKind.error,
          message: data['message'] as String?,
          errorKind: data['error'] as String?,
          postHoc: data['post_hoc'] == true,
          details: data['details'],
        );
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
      // An SSE comment, e.g. the server's `: keepalive` heartbeat. It exists only to keep bytes
      // flowing (and so the client's idle timeout from firing); it carries no event.
      if (line.startsWith(':')) continue;
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

/// The `/api/v3/chat/stream` request body. `images` is omitted when empty, so a text turn is
/// exactly the body it was before images existed. `workspaceRoot` is omitted when null for the
/// same reason: it is the Desktop IDE's active workspace root (audit FSA-006), forwarded by the
/// backend to the paired daemon as the `cwd` of every command, and a turn from a surface with no
/// open workspace must look exactly as it did before the field existed.
Map<String, dynamic> chatStreamBody({
  required String message,
  required String sessionId,
  required String agentId,
  bool useKnowledge = true,
  bool useMemory = true,
  bool useTools = true,
  List<String> approvalIds = const [],
  int maxOutputTokens = 1024,
  double temperature = 0.3,
  List<ChatImageAttachment> images = const [],
  String? workspaceRoot,
}) {
  return {
    "message": message,
    "session_id": sessionId,
    "agent": agentId,
    "use_knowledge": useKnowledge,
    "use_memory": useMemory,
    "use_tools": useTools,
    "approval_ids": approvalIds,
    "max_output_tokens": maxOutputTokens,
    "temperature": temperature,
    if (images.isNotEmpty) "images": [for (final image in images) image.toWire()],
    "workspace_root": ?workspaceRoot,
  };
}

/// The open chat stream went silent for longer than its idle timeout -- no frame and no
/// keepalive -- so the connection is treated as dead.
class ChatStreamIdleTimeoutException extends TimeoutException {
  ChatStreamIdleTimeoutException(Duration idle)
      : super('No data from the chat stream for ${idle.inMilliseconds}ms', idle);
}

class SseAgentClient {
  final Ref _ref;
  final _uuid = const Uuid();
  // Injected in tests; null means a fresh client per stream, closed when the stream ends.
  final http.Client? _httpClient;
  final Duration _idleTimeout;

  SseAgentClient(
    this._ref, {
    http.Client? httpClient,
    Duration idleTimeout = kChatStreamIdleTimeout,
  })  : _httpClient = httpClient,
        _idleTimeout = idleTimeout;

  String get _url => '${_ref.read(backendUrlProvider)}/api/v3/chat/stream';
  String get _backendUrl => _ref.read(backendUrlProvider);

  Stream<SseChatEvent> streamAgentEvents({
    required String message,
    required String sessionId,
    required String agentId,
    bool useKnowledge = true,
    bool useMemory = true,
    bool useTools = true,
    // Server-issued approval ids (`PendingToolCall.approvalId`) of the held calls the user
    // approved. The backend then resumes that stored turn and runs only those exact calls (F-04).
    List<String> approvalIds = const [],
    String? correlationId,
    int maxOutputTokens = 1024,
    double temperature = 0.3,
    // ADR-031: images for this turn only. Sent as base64 and never persisted on either side.
    List<ChatImageAttachment> images = const [],
    // FSA-006: the Desktop IDE's active workspace root, so the daemon runs commands there and
    // not in whichever root was granted first. Null from every surface without one.
    String? workspaceRoot,
  }) {
    final ownsClient = _httpClient == null;
    final client = _httpClient ?? http.Client();
    StreamSubscription<SseChatEvent>? inner;
    late final StreamController<SseChatEvent> controller;
    controller = StreamController<SseChatEvent>(
      onListen: () {
        inner = _events(
          client: client,
          ownsClient: ownsClient,
          message: message,
          sessionId: sessionId,
          agentId: agentId,
          useKnowledge: useKnowledge,
          useMemory: useMemory,
          useTools: useTools,
          approvalIds: approvalIds,
          correlationId: correlationId,
          maxOutputTokens: maxOutputTokens,
          temperature: temperature,
          images: images,
          workspaceRoot: workspaceRoot,
        ).listen(controller.add, onError: controller.addError, onDone: controller.close);
      },
      onPause: () => inner?.pause(),
      onResume: () => inner?.resume(),
      onCancel: () {
        // A cancelled stream (the user pressed Stop) must drop the connection now. The generator
        // below would otherwise only notice at its next `yield`, i.e. whenever the server next
        // sends something -- possibly never, on exactly the stalled stream Stop exists for.
        if (ownsClient) client.close();
        final sub = inner;
        inner = null;
        unawaited(sub?.cancel().catchError((_) {}));
      },
    );
    return controller.stream;
  }

  Stream<SseChatEvent> _events({
    required http.Client client,
    required bool ownsClient,
    required String message,
    required String sessionId,
    required String agentId,
    required bool useKnowledge,
    required bool useMemory,
    required bool useTools,
    required List<String> approvalIds,
    required String? correlationId,
    required int maxOutputTokens,
    required double temperature,
    required List<ChatImageAttachment> images,
    required String? workspaceRoot,
  }) async* {
    final cid = correlationId ?? _uuid.v4();
    try {
      final request = http.Request('POST', Uri.parse(_url));
      Future<void> applyAuth(http.Request req) async {
        req.headers['Content-Type'] = 'application/json';
        req.headers['X-Correlation-Id'] = cid;
        final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
        if (token != null) req.headers['Authorization'] = 'Bearer $token';
        req.headers.addAll(
          await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
        );
        // A team turn retrieves the team's corpus and is metered to the team (ADR-034).
        req.headers.addAll(await workspaceHeaders(_ref));
      }

      await applyAuth(request);
      request.body = jsonEncode(chatStreamBody(
        message: message,
        sessionId: sessionId,
        agentId: agentId,
        useKnowledge: useKnowledge,
        useMemory: useMemory,
        useTools: useTools,
        approvalIds: approvalIds,
        maxOutputTokens: maxOutputTokens,
        temperature: temperature,
        images: images,
        workspaceRoot: workspaceRoot,
      ));

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
      // The connect timeout above only covers the headers. This one covers the body: a gap
      // between chunks longer than [_idleTimeout] ends the stream with an error instead of
      // leaving the chat locked on a connection that will never deliver again. Keepalive
      // comments count as chunks, so a slow-but-alive turn never trips it.
      final body = response.stream.timeout(_idleTimeout, onTimeout: (sink) {
        sink
          ..addError(ChatStreamIdleTimeoutException(_idleTimeout))
          ..close();
      });
      await for (final chunk in body.transform(utf8.decoder)) {
        for (final event in parser.feed(chunk)) {
          yield event;
        }
      }
    } finally {
      if (ownsClient) client.close();
    }
  }
}

final sseAgentClientProvider = Provider<SseAgentClient>((ref) => SseAgentClient(ref));
