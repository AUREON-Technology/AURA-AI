import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/drift.dart';
import 'package:http/http.dart' as http;
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

  @override
  Map<String, DownloadProgress> build() => {};

  /// Triggers a pull request via Ollama API streaming progress update chunks
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

    final host = ref.read(ollamaHostProvider);
    final url = Uri.parse("$host/api/pull");
    final client = http.Client();
    _clientMap[modelName] = client;

    try {
      final request = http.Request('POST', url);
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode({
        "name": modelName,
        "stream": true,
      });

      final response = await client.send(request);
      if (response.statusCode != 200) {
        throw Exception("Server returned code ${response.statusCode}");
      }

      int lastCompleted = 0;
      DateTime lastTime = DateTime.now();

      await for (final chunk in response.stream.transform(utf8.decoder)) {
        final lines = chunk.split('\n');
        for (final line in lines) {
          if (line.trim().isEmpty) continue;
          try {
            final parsed = jsonDecode(line);
            final status = parsed["status"] as String?;
            
            if (status == "success") {
              _finalizeDownload(modelName);
              return;
            }

            final total = parsed["total"] as int?;
            final completed = parsed["completed"] as int?;

            if (total != null && completed != null && total > 0) {
              final now = DateTime.now();
              final diffMs = now.difference(lastTime).inMilliseconds;
              String speedString = "0 KB/s";
              
              if (diffMs > 500) {
                final bytesDiff = completed - lastCompleted;
                final speedBps = (bytesDiff / (diffMs / 1000.0)).round();
                if (speedBps > 1024 * 1024) {
                  speedString = "${(speedBps / (1024 * 1024)).toStringAsFixed(1)} MB/s";
                } else {
                  speedString = "${(speedBps / 1024).toStringAsFixed(0)} KB/s";
                }
                lastCompleted = completed;
                lastTime = now;
              }

              state = {
                ...state,
                modelName: DownloadProgress(
                  modelName: modelName,
                  progress: completed / total,
                  speed: speedString,
                  status: "downloading",
                  totalBytes: total,
                  completedBytes: completed,
                )
              };
            }
          } catch (_) {}
        }
      }
      _finalizeDownload(modelName);
    } catch (e) {
      debugPrint("Download model $modelName failed: $e");
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
