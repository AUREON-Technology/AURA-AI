import 'dart:async';
import 'dart:io';

import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

void main() {
  group('isUnreachableBackendError', () {
    test('detects SocketException (connection refused)', () {
      final error = SocketException(
        'Connection refused',
        osError: const OSError('Connection refused', 111),
        address: InternetAddress.loopbackIPv4,
        port: 8000,
      );
      expect(isUnreachableBackendError(error), isTrue);
    });

    test('detects TimeoutException from the SSE connect timeout', () {
      expect(
        isUnreachableBackendError(TimeoutException('Timed out')),
        isTrue,
      );
    });

    test('detects http ClientException wrapping a dead host', () {
      expect(
        isUnreachableBackendError(
          http.ClientException('Connection refused', Uri.parse('http://localhost:8000/api/v3/chat/stream')),
        ),
        isTrue,
      );
    });

    test('detects a wrapped SocketException in a generic Exception string', () {
      expect(
        isUnreachableBackendError(
          Exception('SocketException: Connection refused (OS Error: Connection refused, errno = 111), address = localhost, port = 8000'),
        ),
        isTrue,
      );
    });

    test('does not treat an HTTP 500 from a reachable host as unreachable', () {
      expect(
        isUnreachableBackendError(
          Exception('SSE chat request failed with status 500: internal error'),
        ),
        isFalse,
      );
    });
  });

  group('isChatConnectionErrorText', () {
    test('matches the stored assistant error prefix', () {
      expect(isChatConnectionErrorText(kChatConnectionErrorMessage), isTrue);
      expect(isChatConnectionErrorText('Error: model refused'), isFalse);
    });
  });
}
