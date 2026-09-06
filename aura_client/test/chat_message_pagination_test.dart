import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// `ChatRepository.watchMessages`'s `limit` bounds how many encrypted rows Drift decrypts on
/// this isolate per load -- see the comment on that method for why an unbounded load of a long
/// conversation is real, measurable startup jank. These tests cover the query behavior only
/// (ordering, truncation, "load more" growth); the jank itself isn't something a widget/unit
/// test can observe.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late ChatRepository repo;
  late ProviderContainer container;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    container = ProviderContainer(
      overrides: [databaseProvider.overrideWithValue(db)],
    );
    repo = container.read(chatRepositoryProvider);
  });

  tearDown(() {
    container.dispose();
  });

  Future<void> insertMessages(String conversationId, int count) async {
    for (var i = 0; i < count; i++) {
      await db.into(db.messages).insert(
            MessagesCompanion.insert(
              id: 'm$i',
              conversationId: conversationId,
              role: 'user',
              content: 'message $i',
              createdAt: DateTime(2026, 1, 1).add(Duration(minutes: i)),
            ),
          );
    }
  }

  test('with no limit, returns every message oldest-first (back-compat)', () async {
    await insertMessages('c1', 5);
    final messages = await repo.watchMessages('c1').first;
    expect(messages.map((m) => m.id), ['m0', 'm1', 'm2', 'm3', 'm4']);
  });

  test('with a limit, returns only the most recent N, still oldest-first', () async {
    await insertMessages('c1', 10);
    final messages = await repo.watchMessages('c1', limit: 3).first;
    // The 3 most recent by createdAt are m7, m8, m9 -- returned oldest-first for display.
    expect(messages.map((m) => m.id), ['m7', 'm8', 'm9']);
  });

  test('a limit larger than the conversation returns everything', () async {
    await insertMessages('c1', 4);
    final messages = await repo.watchMessages('c1', limit: 100).first;
    expect(messages.length, 4);
  });

  test('ChatVisibleMessageLimit starts at pageSize and grows via loadMore', () {
    final notifier = container.read(chatVisibleMessageLimitProvider('c1').notifier);
    expect(container.read(chatVisibleMessageLimitProvider('c1')), ChatVisibleMessageLimit.pageSize);
    notifier.loadMore();
    expect(
      container.read(chatVisibleMessageLimitProvider('c1')),
      ChatVisibleMessageLimit.pageSize * 2,
    );
  });

  test('each conversation gets its own independent visible-message limit', () {
    container.read(chatVisibleMessageLimitProvider('c1').notifier).loadMore();
    expect(
      container.read(chatVisibleMessageLimitProvider('c1')),
      ChatVisibleMessageLimit.pageSize * 2,
    );
    expect(
      container.read(chatVisibleMessageLimitProvider('c2')),
      ChatVisibleMessageLimit.pageSize,
    );
  });
}
