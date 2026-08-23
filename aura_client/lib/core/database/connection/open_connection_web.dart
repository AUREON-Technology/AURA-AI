import 'package:drift/drift.dart';
import 'package:drift/wasm.dart';
import 'package:flutter/foundation.dart';

/// Opens Drift on the web via sqlite3.wasm + drift_worker.js in `web/`.
///
/// Those assets must match the locked `sqlite3` / `drift` versions
/// (currently sqlite3 3.3.2 and drift 2.33.0).
QueryExecutor openConnection() {
  return DatabaseConnection.delayed(Future(() async {
    final result = await WasmDatabase.open(
      databaseName: 'aura_db',
      sqlite3Uri: Uri.parse('sqlite3.wasm'),
      driftWorkerUri: Uri.parse('drift_worker.js'),
    );
    if (result.missingFeatures.isNotEmpty) {
      debugPrint(
        '[Database] Using ${result.chosenImplementation} '
        '(missing: ${result.missingFeatures})',
      );
    }
    return result.resolvedExecutor;
  }));
}
