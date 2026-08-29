import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/features/finance/presentation/widgets/finance_transaction_history.dart';

void main() {
  testWidgets('history card lays out inside an unbounded ListView', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          home: Scaffold(
            body: ListView(
              children: [
                FinanceTransactionHistory(
                  transactionsAsync: AsyncValue<List<FinanceRecord>>.data(
                    <FinanceRecord>[],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    expect(find.text('Recent Activities'), findsOneWidget);
    expect(find.text('No activities recorded yet'), findsOneWidget);
  });
}
