import 'package:drift/drift.dart';

QueryExecutor openConnection() {
  throw UnsupportedError(
    'No Drift database implementation was found for this platform.',
  );
}
