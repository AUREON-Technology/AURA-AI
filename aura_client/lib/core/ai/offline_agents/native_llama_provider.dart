import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/drift.dart';
import 'package:flutter_llama/flutter_llama.dart';
import 'package:path_provider/path_provider.dart';
import 'inference_provider.dart';
import 'offline_agent_manager.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';

class NativeLlamaInferenceProvider implements InferenceProvider {
  final Ref _ref;
  String? _loadedModelId;
  bool _isInitializing = false;

  NativeLlamaInferenceProvider(this._ref);

  @override
  Future<bool> checkHealth() async {
    try {
      final db = _ref.read(databaseProvider);
      final activeModel = await (db.select(db.offlineModelWeights)
            ..where((t) => t.isActive.equals(true) & t.isDownloaded.equals(true)))
          .getSingleOrNull();

      if (activeModel == null) return false;

      final appDir = await getApplicationDocumentsDirectory();
      final modelFile = File("${appDir.path}/models/${activeModel.modelName}");
      return await modelFile.exists();
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
    final appDir = await getApplicationDocumentsDirectory();
    
    // Ensure the models folder exists
    final modelsDir = Directory("${appDir.path}/models");
    if (!await modelsDir.exists()) {
      await modelsDir.create(recursive: true);
    }

    // Query the SQLite database to resolve the currently active, downloaded model name
    final db = _ref.read(databaseProvider);
    final activeModel = await (db.select(db.offlineModelWeights)
          ..where((t) => t.isActive.equals(true) & t.isDownloaded.equals(true)))
        .getSingleOrNull();

    if (activeModel == null) {
      yield "\n[Offline Inference Error: No active model selected or downloaded. Please configure this in the Model Manager.]";
      return;
    }

    final modelFile = File("${modelsDir.path}/${activeModel.modelName}");

    if (!await modelFile.exists()) {
      yield "\n[Offline Inference Error: Model file '${activeModel.modelName}' not found on-device. Please download it in the Model Manager first.]";
      return;
    }

    if (_loadedModelId != activeModel.modelName) {
      if (_isInitializing) {
        yield "\n[System: On-Device model initialization in progress. Please wait...]";
        return;
      }
      _isInitializing = true;
      try {
        final llama = FlutterLlama.instance;
        final config = LlamaConfig(
          modelPath: modelFile.path,
          nThreads: 4,
          nGpuLayers: -1, // Load as many layers to GPU as possible
          contextSize: 2048,
          batchSize: 512,
          useGpu: true,
        );
        final success = await llama.loadModel(config);
        if (!success) {
          throw Exception("Failed to load local GGUF model weights");
        }
        _loadedModelId = activeModel.modelName;
      } catch (e) {
        yield "\n[Offline Inference Error: Failed to initialize native LLM engine: $e]";
        return;
      } finally {
        _isInitializing = false;
      }
    }

    // Format chat history with Llama 3 Instruct / ChatML template
    final prompt = _formatPrompt(
      messages: messages,
      systemInstruction: systemInstruction,
    );

    try {
      final llama = FlutterLlama.instance;
      final params = GenerationParams(
        prompt: prompt,
        temperature: 0.7,
        maxTokens: 512,
      );

      await for (final token in llama.generateStream(params)) {
        yield token;
      }
    } catch (e) {
      yield "\n[Offline Inference Error during generation: $e]";
    }
  }

  String _formatPrompt({
    required List<Map<String, String>> messages,
    required String systemInstruction,
  }) {
    final buffer = StringBuffer();
    
    // Llama 3 Instruct Format
    buffer.write("<|begin_of_text|>");
    if (systemInstruction.isNotEmpty) {
      buffer.write("<|start_header_id|>system<|end_header_id|>\n$systemInstruction<|eot_id|>");
    }
    
    for (final m in messages) {
      final role = m["role"] == "assistant" || m["role"] == "model" ? "assistant" : "user";
      final content = m["content"] ?? "";
      buffer.write("<|start_header_id|>$role<|end_header_id|>\n$content<|eot_id|>");
    }
    
    buffer.write("<|start_header_id|>assistant<|end_header_id|>\n");
    return buffer.toString();
  }
}
