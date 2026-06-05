import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'inference_provider.dart';

class OllamaHostNotifier extends Notifier<String> {
  @override
  String build() => "http://localhost:11434";
  void setHost(String host) => state = host;
}

final ollamaHostProvider = NotifierProvider<OllamaHostNotifier, String>(() {
  return OllamaHostNotifier();
});

class OllamaInferenceProvider implements InferenceProvider {
  final Ref _ref;

  OllamaInferenceProvider(this._ref);

  @override
  Future<bool> checkHealth() async {
    final host = _ref.read(ollamaHostProvider);
    try {
      final response = await http
          .get(Uri.parse("$host/api/tags"))
          .timeout(const Duration(seconds: 2));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  @override
  Stream<String> generateStream({
    required String model,
    required List<Map<String, String>> messages,
    required String systemInstruction,
  }) async* {
    final host = _ref.read(ollamaHostProvider);
    final url = Uri.parse("$host/api/chat");

    final payloadMessages = <Map<String, String>>[];
    if (systemInstruction.isNotEmpty) {
      payloadMessages.add({
        "role": "system",
        "content": systemInstruction,
      });
    }

    for (final m in messages) {
      final role =
          m["role"] == "assistant" || m["role"] == "model" ? "assistant" : "user";
      payloadMessages.add({
        "role": role,
        "content": m["content"] ?? "",
      });
    }

    try {
      final client = http.Client();
      final request = http.Request('POST', url);
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode({
        "model": model,
        "messages": payloadMessages,
        "stream": true,
        "options": {
          "temperature": 0.7,
        }
      });

      final response =
          await client.send(request).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        await for (final chunk in response.stream.transform(utf8.decoder)) {
          final lines = chunk.split('\n');
          for (final line in lines) {
            if (line.trim().isEmpty) continue;
            try {
              final parsed = jsonDecode(line);
              final content = parsed["message"]?["content"] as String?;
              if (content != null) {
                yield content;
              }
            } catch (_) {
              // Ignore split JSON chunks errors
            }
          }
        }
      } else {
        yield "\n[Offline Inference Error: Local Ollama service returned status ${response.statusCode}]";
      }
    } catch (e) {
      yield "\n[Offline Inference Error: Could not connect to local Ollama service. Please make sure Ollama is running on $host]";
    }
  }
}

/// Dynamic active inference provider, allowing swapping local providers
final activeInferenceProvider = Provider<InferenceProvider>((ref) {
  return OllamaInferenceProvider(ref);
});

class OfflineAgentManager {
  final Ref _ref;

  OfflineAgentManager(this._ref);

  /// Streams response delegating to the active InferenceProvider
  Stream<String> streamResponse({
    required String model,
    required List<Map<String, String>> messages,
    required String systemInstruction,
  }) {
    final provider = _ref.read(activeInferenceProvider);
    return provider.generateStream(
      model: model,
      messages: messages,
      systemInstruction: systemInstruction,
    );
  }
}

final offlineAgentManagerProvider = Provider<OfflineAgentManager>((ref) {
  return OfflineAgentManager(ref);
});
