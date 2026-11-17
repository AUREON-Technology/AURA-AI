import 'dart:convert';

/// Parsed v3 error envelope. The gateway has three on-the-wire shapes:
/// `{error, message, retryable, details}`, `{error, correlation_id}`, and
/// a plain-text 429 from the IP middleware.
class AuraApiError implements Exception {
  final String error;
  final String message;
  final bool retryable;
  final int? statusCode;
  final Duration? retryAfter;
  final String? correlationId;
  final Map<String, dynamic> details;

  const AuraApiError({
    required this.error,
    required this.message,
    required this.retryable,
    this.statusCode,
    this.retryAfter,
    this.correlationId,
    this.details = const {},
  });

  bool get isRetryable =>
      retryable ||
      statusCode == null ||
      statusCode == 429 ||
      (statusCode != null && statusCode! >= 500 && statusCode! < 600);

  @override
  String toString() => message;

  /// Maps a status + body to a user-facing error. Never throws.
  factory AuraApiError.fromResponse({
    required int statusCode,
    required String body,
    String? retryAfterHeader,
    String? correlationId,
  }) {
    final retryAfter = _parseRetryAfter(retryAfterHeader);
    Map<String, dynamic>? json;
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic>) json = decoded;
      if (decoded is Map && json == null) {
        json = decoded.map((k, v) => MapEntry(k.toString(), v));
      }
    } catch (_) {
      json = null;
    }

    if (json != null) {
      final nested = json['detail'];
      // FastAPI's own errors (an unknown route, a validation failure, an HTTPException raised
      // with a plain string) put a string or a list under `detail`. Showing the raw body there
      // put `{"detail":[{"loc":...}]}` in front of the user (INT-028).
      final detailText = _formatDetail(nested);
      if (detailText != null) {
        return AuraApiError(
          error: nested is List ? 'validation_error' : 'http_$statusCode',
          message: detailText,
          retryable: statusCode == 429 || (statusCode >= 500 && statusCode < 600),
          statusCode: statusCode,
          retryAfter: retryAfter,
          correlationId: (json['correlation_id'] ?? correlationId)?.toString(),
        );
      }
      final map = nested is Map
          ? nested.map((k, v) => MapEntry(k.toString(), v))
          : json;
      final error = (map['error'] ?? 'http_$statusCode').toString();
      // Never the raw JSON body: an envelope without a message or slug gets the status copy.
      final message = (map['message'] ?? map['error'] ?? _fallbackMessage(statusCode)).toString();
      final retryable = map['retryable'] == true ||
          statusCode == 429 ||
          (statusCode >= 500 && statusCode < 600);
      final details = map['details'] is Map<String, dynamic>
          ? map['details'] as Map<String, dynamic>
          : const <String, dynamic>{};
      return AuraApiError(
        error: error,
        message: message.isEmpty ? _fallbackMessage(statusCode) : message,
        retryable: retryable,
        statusCode: statusCode,
        retryAfter: retryAfter,
        correlationId: (map['correlation_id'] ?? correlationId)?.toString(),
        details: details,
      );
    }

    final trimmed = body.trim();
    return AuraApiError(
      error: 'http_$statusCode',
      message: trimmed.isEmpty ? _fallbackMessage(statusCode) : trimmed,
      retryable: statusCode == 429 || (statusCode >= 500 && statusCode < 600),
      statusCode: statusCode,
      retryAfter: retryAfter,
      correlationId: correlationId,
    );
  }

  factory AuraApiError.unreachable([Object? cause]) {
    return AuraApiError(
      error: 'unreachable',
      message: cause == null
          ? 'Could not reach AURA.'
          : 'Could not reach AURA: $cause',
      retryable: true,
    );
  }

  static String _fallbackMessage(int status) {
    return switch (status) {
      400 => 'AURA blocked this turn.',
      401 => 'Sign in again to continue.',
      403 => 'You do not have permission for this action.',
      429 => 'Too many requests. Try again shortly.',
      503 => 'AURA is starting up. Try again in a moment.',
      _ => 'Request failed ($status).',
    };
  }

  /// A readable sentence from FastAPI's `detail` when it is a string or a validation list;
  /// null when it is absent or a map (the v3 envelope, handled by the caller).
  static String? _formatDetail(Object? detail) {
    if (detail is String) {
      final trimmed = detail.trim();
      return trimmed.isEmpty ? null : trimmed;
    }
    if (detail is! List || detail.isEmpty) return null;
    final parts = <String>[];
    for (final item in detail) {
      if (item is Map) {
        final msg = item['msg']?.toString() ?? 'Invalid value';
        final loc = item['loc'];
        // Drop the location's first element when it is only where the field came from.
        final path = loc is List
            ? loc
                .where((p) => !const {'body', 'query', 'path', 'header'}.contains(p))
                .map((p) => p.toString())
                .join('.')
            : '';
        parts.add(path.isEmpty ? msg : '$path: $msg');
      } else if (item != null) {
        parts.add(item.toString());
      }
    }
    return parts.isEmpty ? null : parts.join('; ');
  }

  static Duration? _parseRetryAfter(String? header) {
    if (header == null || header.isEmpty) return null;
    final seconds = int.tryParse(header);
    if (seconds == null) return null;
    return Duration(seconds: seconds);
  }
}

/// Legacy name used by the retired on-device router tests.
class AuraBackendException extends AuraApiError {
  // ignore: use_super_parameters
  const AuraBackendException(String message, {int? statusCode})
      : super(
          error: 'backend',
          message: message,
          retryable: statusCode == null ||
              statusCode == 429 ||
              (statusCode >= 500 && statusCode < 600),
          statusCode: statusCode,
        );
}
