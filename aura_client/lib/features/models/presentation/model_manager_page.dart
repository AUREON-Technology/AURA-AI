import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/drift.dart' hide Column;
import 'package:path_provider/path_provider.dart';
import 'package:http/http.dart' as http;
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';
import '../../../core/theme/theme.dart';
import '../../../core/ai/offline_agents/model_download_manager.dart';
import '../../../core/ai/offline_agents/offline_agent_manager.dart';

final installedModelsProvider = StreamProvider.autoDispose<List<OfflineModelWeight>>((ref) {
  final db = ref.watch(databaseProvider);
  return (db.select(db.offlineModelWeights)).watch();
});

class ModelManagerPage extends ConsumerWidget {
  const ModelManagerPage({super.key});

  // Predefined models details
  static const List<Map<String, String>> _availableModels = [
    {
      "id": "gemma2:2b",
      "name": "Google Gemma 2 (2B)",
      "size": "1.6 GB",
      "desc": "Lightweight, highly capable model optimized by Google. Best for general use on average hardware."
    },
    {
      "id": "phi3:latest",
      "name": "Microsoft Phi-3 (3.8B)",
      "size": "2.2 GB",
      "desc": "Microsoft's state-of-the-art small language model with excellent reasoning capabilities."
    },
    {
      "id": "qwen2:1.5b",
      "name": "Alibaba Qwen 2 (1.5B)",
      "size": "900 MB",
      "desc": "Extremely fast, multilingual model. Ideal for low-end configurations."
    },
    {
      "id": "llama3:8b",
      "name": "Meta LLaMA 3 (8B)",
      "size": "4.7 GB",
      "desc": "Meta's flagship model. Requires at least 8GB-12GB of RAM. High capability."
    }
  ];

  Future<void> _activateModel(WidgetRef ref, String modelId) async {
    final db = ref.read(databaseProvider);
    await db.transaction(() async {
      // Deactivate all first
      await (db.update(db.offlineModelWeights)..where((t) => Constant(true))).write(
        OfflineModelWeightsCompanion(isActive: Value(false)),
      );
      // Activate selected
      await (db.update(db.offlineModelWeights)..where((t) => t.id.equals(modelId))).write(
        OfflineModelWeightsCompanion(isActive: Value(true)),
      );
    });
  }

  Future<void> _deleteModel(WidgetRef ref, String modelId) async {
    final db = ref.read(databaseProvider);
    await (db.delete(db.offlineModelWeights)..where((t) => t.id.equals(modelId))).go();
    
    // Delete local GGUF weight file
    try {
      final appDir = await getApplicationDocumentsDirectory();
      final file = File("${appDir.path}/models/$modelId");
      if (await file.exists()) {
        await file.delete();
        debugPrint("Deleted local model file: ${file.path}");
      }
    } catch (e) {
      debugPrint("Failed to delete local model file: $e");
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final installedAsync = ref.watch(installedModelsProvider);
    final downloads = ref.watch(modelDownloadProvider);
    final ollamaHost = ref.watch(ollamaHostProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "Offline Model Manager",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      ),
      body: installedAsync.when(
        data: (installedList) {
          return Padding(
            padding: const EdgeInsets.all(24.0),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _buildOllamaStatusCard(ref, ollamaHost),
                  const SizedBox(height: 24),
                  Text(
                    "Available Offline Models",
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                        ),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    "Download and configure local models for offline execution.",
                    style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                  ),
                  const SizedBox(height: 20),
                  ListView.builder(
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    itemCount: _availableModels.length,
                    itemBuilder: (context, index) {
                      final model = _availableModels[index];
                      final modelId = model["id"]!;
                      
                      final isDownloaded = installedList.any((m) => m.id == modelId && m.isDownloaded);
                      final isActive = installedList.any((m) => m.id == modelId && m.isActive);
                      final progress = downloads[modelId];

                      return _buildModelCard(
                        context: context,
                        ref: ref,
                        id: modelId,
                        name: model["name"]!,
                        size: model["size"]!,
                        desc: model["desc"]!,
                        isDownloaded: isDownloaded,
                        isActive: isActive,
                        progress: progress,
                      );
                    },
                  ),
                ],
              ),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
        error: (err, _) => Center(
          child: Text(
            "Failed to load models configuration: $err",
            style: const TextStyle(color: Colors.redAccent),
          ),
        ),
      ),
    );
  }

  Widget _buildOllamaStatusCard(WidgetRef ref, String host) {
    final healthAsync = ref.watch(ollamaHealthProvider);
    final connected = healthAsync.value ?? false;
    final color = connected ? AuraTheme.accentTeal : AuraTheme.textSecondary;
    final statusText = connected ? "On-Device Engine Ready" : "No Active Local Model";
    final descText = connected ? "Active local model loaded in memory" : "Download a model and toggle it Active";

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(18.0),
        child: Row(
          children: [
            CircleAvatar(
              backgroundColor: color.withOpacity(0.1),
              child: Icon(connected ? Icons.memory : Icons.no_sim_outlined, color: color),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    statusText,
                    style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 15),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    descText,
                    style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                  ),
                ],
              ),
            ),
            ElevatedButton.icon(
              onPressed: () {
                ref.refresh(ollamaHealthProvider);
              },
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text("Refresh"),
              style: ElevatedButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildModelCard({
    required BuildContext context,
    required WidgetRef ref,
    required String id,
    required String name,
    required String size,
    required String desc,
    required bool isDownloaded,
    required bool isActive,
    required DownloadProgress? progress,
  }) {
    final isDownloading = progress != null && progress.status == "downloading";
    
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Text(
                    name,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
                  ),
                ),
                Text(
                  size,
                  style: const TextStyle(fontSize: 13, color: AuraTheme.accentAmber, fontWeight: FontWeight.bold),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              desc,
              style: const TextStyle(fontSize: 12, color: AuraTheme.textSecondary),
            ),
            const SizedBox(height: 18),
            if (isDownloading) ...[
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    "Downloading: ${(progress.progress * 100).toStringAsFixed(1)}%",
                    style: const TextStyle(color: Colors.white70, fontSize: 12),
                  ),
                  Text(
                    progress.speed,
                    style: const TextStyle(color: AuraTheme.accentTeal, fontSize: 12),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              LinearProgressIndicator(
                value: progress.progress,
                backgroundColor: Colors.white12,
                color: AuraTheme.accentIndigo,
                borderRadius: BorderRadius.circular(4),
              ),
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton.icon(
                    onPressed: () => ref.read(modelDownloadProvider.notifier).cancelDownload(id),
                    icon: const Icon(Icons.pause, size: 16, color: Colors.redAccent),
                    label: const Text("Pause", style: TextStyle(color: Colors.redAccent)),
                  ),
                ],
              ),
            ] else if (isDownloaded) ...[
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Switch(
                        value: isActive,
                        activeColor: AuraTheme.accentTeal,
                        onChanged: (val) {
                          if (val) {
                            _activateModel(ref, id);
                          }
                        },
                      ),
                      const SizedBox(width: 8),
                      Text(
                        isActive ? "Active Model" : "Inactive Model",
                        style: TextStyle(
                          color: isActive ? AuraTheme.accentTeal : AuraTheme.textSecondary,
                          fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      TextButton.icon(
                        onPressed: () => _deleteModel(ref, id),
                        icon: const Icon(Icons.delete_outline, size: 16, color: Colors.redAccent),
                        label: const Text("Delete", style: TextStyle(color: Colors.redAccent)),
                      ),
                    ],
                  ),
                ],
              )
            ] else ...[
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  ElevatedButton.icon(
                    onPressed: () => ref.read(modelDownloadProvider.notifier).startDownload(id),
                    icon: const Icon(Icons.download, size: 16),
                    label: const Text("Download"),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentIndigo,
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    ),
                  ),
                ],
              )
            ]
          ],
        ),
      ),
    );
  }
}
