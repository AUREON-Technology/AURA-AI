/// Minimal Debug Adapter Protocol client (D7 Part A.3).
///
/// Frames/unframes `Content-Length: N\r\n\r\n<json>`-delimited messages over the raw byte stream
/// [HostdDebugClient] tunnels from the daemon's debugpy proxy, correlates `request`/`response`
/// pairs by `seq`, and exposes server-initiated `event` messages as a stream. This is DAP's own
/// wire format (a JSON-RPC-style framing), not something aura_hostd parses -- see
/// `debug_session_manager.dart`'s doc comment.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/hostd/hostd_debug_client.dart';

class DapClient {
  DapClient(this._transport) {
    _sub = _transport.dapBytes.listen(_onBytes);
  }

  final HostdDebugClient _transport;
  StreamSubscription<Uint8List>? _sub;
  final BytesBuilder _buffer = BytesBuilder(copy: false);
  int _seq = 1;
  final Map<int, Completer<Map<String, dynamic>>> _pending = {};
  final _events = StreamController<Map<String, dynamic>>.broadcast();

  /// Server-initiated `event` messages (`stopped`, `output`, `initialized`, `terminated`,
  /// `exited`, ...) -- everything that isn't a direct response to a request this client sent.
  Stream<Map<String, dynamic>> get events => _events.stream;

  Future<Map<String, dynamic>> sendRequest(String command, [Map<String, dynamic>? arguments]) {
    final seq = _seq++;
    final completer = Completer<Map<String, dynamic>>();
    _pending[seq] = completer;
    _send({
      'seq': seq,
      'type': 'request',
      'command': command,
      'arguments': ?arguments,
    });
    return completer.future.timeout(
      const Duration(seconds: 15),
      onTimeout: () {
        _pending.remove(seq);
        throw TimeoutException('DAP request "$command" timed out');
      },
    );
  }

  void _send(Map<String, dynamic> message) {
    final body = utf8.encode(jsonEncode(message));
    final header = ascii.encode('Content-Length: ${body.length}\r\n\r\n');
    final framed = BytesBuilder(copy: false)
      ..add(header)
      ..add(body);
    _transport.sendDap(framed.toBytes());
  }

  void _onBytes(Uint8List chunk) {
    _buffer.add(chunk);
    _drain();
  }

  void _drain() {
    while (true) {
      final bytes = _buffer.toBytes();
      final headerEnd = _findHeaderEnd(bytes);
      if (headerEnd == -1) return;
      final header = ascii.decode(bytes.sublist(0, headerEnd));
      final match = RegExp(r'Content-Length:\s*(\d+)', caseSensitive: false).firstMatch(header);
      if (match == null) {
        // A framing header without Content-Length should not happen from a real debugpy --
        // drop the buffer rather than looping forever on malformed input.
        _buffer.clear();
        return;
      }
      final length = int.parse(match.group(1)!);
      final bodyStart = headerEnd + 4; // skip the \r\n\r\n separator itself
      if (bytes.length < bodyStart + length) return; // wait for the rest of the body to arrive
      final bodyBytes = bytes.sublist(bodyStart, bodyStart + length);
      final remaining = bytes.sublist(bodyStart + length);
      _buffer.clear();
      if (remaining.isNotEmpty) _buffer.add(remaining);
      try {
        final message = jsonDecode(utf8.decode(bodyBytes)) as Map<String, dynamic>;
        _handleMessage(message);
      } catch (_) {
        // A single malformed message must not take down the whole session.
      }
    }
  }

  int _findHeaderEnd(Uint8List bytes) {
    for (var i = 0; i + 3 < bytes.length; i++) {
      if (bytes[i] == 13 && bytes[i + 1] == 10 && bytes[i + 2] == 13 && bytes[i + 3] == 10) return i;
    }
    return -1;
  }

  void _handleMessage(Map<String, dynamic> message) {
    switch (message['type'] as String?) {
      case 'response':
        final seq = (message['request_seq'] as num?)?.toInt();
        _pending.remove(seq)?.complete(message);
      case 'event':
        if (!_events.isClosed) _events.add(message);
      // A 'request' from the adapter back to the client (e.g. runInTerminal) is not handled in
      // this first cut -- debugpy does not send these for a plain script debug session.
    }
  }

  Future<void> dispose() async {
    await _sub?.cancel();
    for (final completer in _pending.values) {
      if (!completer.isCompleted) completer.completeError(StateError('DAP client disposed'));
    }
    _pending.clear();
    await _events.close();
  }
}
