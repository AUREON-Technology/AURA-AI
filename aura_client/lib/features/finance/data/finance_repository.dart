import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';

class FinanceStats {
  final double totalIncome;
  final double totalExpense;
  final double balance;
  final Map<String, double> expenseByCategory;

  FinanceStats({
    required this.totalIncome,
    required this.totalExpense,
    required this.balance,
    required this.expenseByCategory,
  });
}

class FinanceRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  FinanceRepository(this._db);

  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.financeRecords)..where((t) => t.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(table: 'finance_records', recordId: id, operation: 'upsert', payload: SyncPayloads.financeRecord(row));
  }

  // Watch transactions ordered by date descending
  Stream<List<FinanceRecord>> watchTransactions() {
    return (_db.select(_db.financeRecords)
          ..orderBy([
            (t) => OrderingTerm(expression: t.date, mode: OrderingMode.desc),
            (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
          ]))
        .watch();
  }

  // Insert a transaction
  Future<void> addTransaction({
    required String type, // income or expense
    required double amount,
    required String category,
    String? description,
    required DateTime date,
  }) async {
    final id = _uuid.v4();
    final record = FinanceRecordsCompanion.insert(
      id: id,
      type: type.toLowerCase(),
      amount: amount,
      category: category.trim(),
      description: Value(description),
      date: date,
      createdAt: DateTime.now(),
      isSynced: const Value(false),
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.financeRecords).insert(record);
    await _enqueueUpsert(id);
  }

  // Delete a transaction. See TaskRepository.deleteTask for why this no
  // longer writes to PendingDeletes.
  Future<void> deleteTransaction(String id) async {
    await (_db.delete(_db.financeRecords)..where((t) => t.id.equals(id))).go();
    await _db.enqueueOutbox(table: 'finance_records', recordId: id, operation: 'delete');
  }
}

final financeRepositoryProvider = Provider<FinanceRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return FinanceRepository(db);
});

final financeTransactionsStreamProvider = StreamProvider.autoDispose<List<FinanceRecord>>((ref) {
  final repository = ref.watch(financeRepositoryProvider);
  return repository.watchTransactions();
});

// Real-time Aggregations Provider
final financeStatsProvider = Provider.autoDispose<AsyncValue<FinanceStats>>((ref) {
  final txsAsync = ref.watch(financeTransactionsStreamProvider);
  
  return txsAsync.whenData((txs) {
    double totalIncome = 0;
    double totalExpense = 0;
    final Map<String, double> expenseByCategory = {};

    for (final tx in txs) {
      if (tx.type == 'income') {
        totalIncome += tx.amount;
      } else if (tx.type == 'expense') {
        totalExpense += tx.amount;
        
        // Capitalize category name for formatting
        final cat = tx.category.isNotEmpty 
            ? tx.category[0].toUpperCase() + tx.category.substring(1).toLowerCase() 
            : 'Other';
        expenseByCategory[cat] = (expenseByCategory[cat] ?? 0) + tx.amount;
      }
    }

    return FinanceStats(
      totalIncome: totalIncome,
      totalExpense: totalExpense,
      balance: totalIncome - totalExpense,
      expenseByCategory: expenseByCategory,
    );
  });
});
