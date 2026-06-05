import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/drift.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';
import 'offline_agent_manager.dart';

class DownloadProgress {
  final String modelName;
  final double progress; // 0.0 to 1.0
  final String speed; // e.g. "5.4 MB/s"
  final String status; // "idle", "downloading", "completed", "error", "paused"
  final int totalBytes;
  final int completedBytes;

  DownloadProgress({
    required this.modelName,
    required this.progress,
    required this.speed,
    required this.status,
    required this.totalBytes,
    required this.completedBytes,
  });

  DownloadProgress copyWith({
    String? modelName,
    double? progress,
    String? speed,
    String? status,
    int? totalBytes,
    int? completedBytes,
  }) {
    return DownloadProgress(
      modelName: modelName ?? this.modelName,
      progress: progress ?? this.progress,
      speed: speed ?? this.speed,
      status: status ?? this.status,
      totalBytes: totalBytes ?? this.totalBytes,
      completedBytes: completedBytes ?? this.completedBytes,
    );
  }
}

class ModelDownloadNotifier extends Notifier<Map<String, DownloadProgress>> {
  final _clientMap = <String, http.Client>{};

  static const _modelUrls = {
    "gemma2:2b": "https://huggingface.co/lmstudio-community/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
    "phi3:latest": "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf",
    "qwen2:1.5b": "https://huggingface.co/Qwen/Qwen2-1.5B-Instruct-GGUF/resolve/main/qwen2-1_5b-instruct-q4_k_m.gguf",
    "llama3:8b": "https://huggingface.co/lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct-Q4_K_M.gguf",
  };

  @override
  Map<String, DownloadProgress> build() => {};

  /// Downloads GGUF model directly from Hugging Face to local device storage
  Future<void> startDownload(String modelName) async {
    if (state.containsKey(modelName) && state[modelName]?.status == "downloading") {
      return;
    }

    state = {
      ...state,
      modelName: DownloadProgress(
        modelName: modelName,
        progress: 0.0,
        speed: "0 KB/s",
        status: "downloading",
        totalBytes: 0,
        completedBytes: 0,
      )
    };

    final urlString = _modelUrls[modelName];
    if (urlString == null) {
      debugPrint("Unknown model URL for $modelName");
      state = {
        ...state,
        modelName: state[modelName]!.copyWith(status: "error", speed: "Unsupported")
      };
      return;
    }

    final url = Uri.parse(urlString);
    final client = http.Client();
    _clientMap[modelName] = client;

    IOSink? sink;
    try {
      final request = http.Request('GET', url);
      final response = await client.send(request);
      if (response.statusCode != 200) {
        throw Exception("Server returned code ${response.statusCode}");
      }

      final totalBytes = response.contentLength ?? 0;
      int completedBytes = 0;

      final appDir = await getApplicationDocumentsDirectory();
      final modelsDir = Directory("${appDir.path}/models");
      if (!await modelsDir.exists()) {
        await modelsDir.create(recursive: true);
      }
      
      final file = File("${modelsDir.path}/$modelName");
      sink = file.openWrite();

      int lastCompleted = 0;
      DateTime lastTime = DateTime.now();

      await for (final chunk in response.stream) {
        sink.add(chunk);
        completedBytes += chunk.length;

        final now = DateTime.now();
        final diffMs = now.difference(lastTime).inMilliseconds;
        String speedString = "0 KB/s";

        if (diffMs > 500) {
          final bytesDiff = completedBytes - lastCompleted;
          final speedBps = (bytesDiff / (diffMs / 1000.0)).round();
          if (speedBps > 1024 * 1024) {
            speedString = "${(speedBps / (1024 * 1024)).toStringAsFixed(1)} MB/s";
          } else {
            speedString = "${(speedBps / 1024).toStringAsFixed(0)} KB/s";
          }
          lastCompleted = completedBytes;
          lastTime = now;
        }

        state = {
          ...state,
          modelName: DownloadProgress(
            modelName: modelName,
            progress: totalBytes > 0 ? completedBytes / totalBytes : 0.0,
            speed: speedString,
            status: "downloading",
            totalBytes: totalBytes,
            completedBytes: completedBytes,
          )
        };
      }

      await sink.flush();
      await sink.close();
      sink = null;

      _finalizeDownload(modelName);
    } catch (e) {
      debugPrint("Download model $modelName failed: $e");
      if (sink != null) {
        try {
          await sink.close();
        } catch (_) {}
      }
      state = {
        ...state,
        modelName: state[modelName]!.copyWith(status: "error", speed: "Failed")
      };
    } finally {
      client.close();
      _clientMap.remove(modelName);
    }
  }

  void cancelDownload(String modelName) {
    if (_clientMap.containsKey(modelName)) {
      _clientMap[modelName]?.close();
      _clientMap.remove(modelName);
    }
    state = {
      ...state,
      modelName: state[modelName]!.copyWith(status: "paused", speed: "Paused")
    };
  }

  Future<void> _finalizeDownload(String modelName) async {
    state = {
      ...state,
      modelName: DownloadProgress(
        modelName: modelName,
        progress: 1.0,
        speed: "0 KB/s",
        status: "completed",
        totalBytes: state[modelName]?.totalBytes ?? 0,
        completedBytes: state[modelName]?.totalBytes ?? 0,
      )
    };

    // Save configuration parameters inside SQLite database weights table
    try {
      final db = ref.read(databaseProvider);
      final sizeMb = ((state[modelName]?.totalBytes ?? 0) / (1024 * 1024)).toStringAsFixed(1);
      await db.into(db.offlineModelWeights).insertOnConflictUpdate(
        OfflineModelWeightsCompanion.insert(
          id: modelName,
          modelName: modelName,
          sizeBytes: "${sizeMb} MB",
          isDownloaded: Value(true),
          isActive: Value(false),
          updatedAt: DateTime.now(),
        ),
      );
    } catch (e) {
      debugPrint("Failed to persist model weights status: $e");
    }
  }
}

final modelDownloadProvider =
    NotifierProvider<ModelDownloadNotifier, Map<String, DownloadProgress>>(() {
  return ModelDownloadNotifier();
});
