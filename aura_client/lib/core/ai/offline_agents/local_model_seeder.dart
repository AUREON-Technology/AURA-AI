import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:drift/drift.dart' hide Column;
import 'package:path_provider/path_provider.dart';
import 'package:flutter_llama/flutter_llama.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';

class LocalModelSeeder {
  static const String modelId = "qwen2:1.5b";
  static const String assetName = "assets/qwen2-1_5b-instruct-q4_k_m.gguf";
  static const String modelName = "qwen2:1.5b";
  static const String modelSize = "900 MB";

  static final isExtractingProvider = StateProvider<bool>((ref) => false);
  static final extractionProgressProvider = StateProvider<double>((ref) => 0.0);

  /// Seeds the local database and extracts the pre-bundled asset in the background.
  static Future<void> seedAndExtractIfNeeded(ProviderContainer container) async {
    final db = container.read(databaseProvider);
    
    // Check if the model is already registered as downloaded
    final existingModel = await (db.select(db.offlineModelWeights)
          ..where((t) => t.id.equals(modelId)))
        .getSingleOrNull();

    final appDir = await getApplicationDocumentsDirectory();
    final modelsDir = Directory("${appDir.path}/models");
    if (!await modelsDir.exists()) {
      await modelsDir.create(recursive: true);
    }
    
    // The path on Android matches modelsDir.path/modelName
    final destFile = File("${modelsDir.path}/${modelName.replaceAll(':', '_')}");

    const expectedSize = 986045824;
    bool isSeededCorrectly = false;
    if (await destFile.exists()) {
      final actualSize = await destFile.length();
      if (actualSize == expectedSize) {
        isSeededCorrectly = true;
      } else {
        if (kDebugMode) {
          print("[LocalModelSeeder] Corrupted/truncated model found: $actualSize bytes (expected $expectedSize). Deleting...");
        }
        try {
          await destFile.delete();
        } catch (_) {}
      }
    }

    // If it's already copied and registered, we are done
    if (existingModel != null && existingModel.isDownloaded && isSeededCorrectly) {
      if (kDebugMode) {
        print("[LocalModelSeeder] Qwen-2 model is already seeded and present.");
      }
      return;
    }

    container.read(isExtractingProvider.notifier).state = true;
    container.read(extractionProgressProvider.notifier).state = 0.1; // Started

    try {
      // 1. Seed database weights table first to register Qwen2 as the default downloaded and active model
      await db.transaction(() async {
        // Deactivate other models first
        await (db.update(db.offlineModelWeights)..where((t) => Constant(true))).write(
          OfflineModelWeightsCompanion(isActive: const Value(false)),
        );
        // Insert or update Qwen2
        await db.into(db.offlineModelWeights).insertOnConflictUpdate(
          OfflineModelWeightsCompanion.insert(
            id: modelId,
            modelName: modelName,
            sizeBytes: modelSize,
            isDownloaded: const Value(true),
            isActive: const Value(true),
            updatedAt: DateTime.now(),
          ),
        );
      });

      container.read(extractionProgressProvider.notifier).state = 0.3; // Registered in DB

      // 2. Perform native asset copy
      if (!await destFile.exists()) {
        if (kDebugMode) {
          print("[LocalModelSeeder] Extracting Qwen-2 model asset to ${destFile.path}...");
        }
        
        final llama = FlutterLlama.instance;
        final success = await llama.copyAssetToFile(
          assetName: assetName,
          destPath: destFile.path,
        );

        if (!success) {
          throw Exception("Native copyAssetToFile returned false");
        }
      }

      container.read(extractionProgressProvider.notifier).state = 1.0; // Completed
      if (kDebugMode) {
        print("[LocalModelSeeder] Qwen-2 model extracted successfully!");
      }
    } catch (e) {
      if (kDebugMode) {
        print("[LocalModelSeeder] Error seeding/extracting local model: $e");
      }
      container.read(extractionProgressProvider.notifier).state = -1.0; // Error state
    } finally {
      container.read(isExtractingProvider.notifier).state = false;
    }
  }
}
