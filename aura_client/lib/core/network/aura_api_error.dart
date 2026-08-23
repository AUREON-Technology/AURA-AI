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
      final map = nested is Map<String, dynamic> ? nested : json;
      final error = (map['error'] ?? 'http_$statusCode').toString();
      final message = (map['message'] ?? map['error'] ?? body).toString();
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
