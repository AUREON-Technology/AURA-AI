import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:googleapis/calendar/v3.dart' as calendar_api;
import 'package:extension_google_sign_in_as_googleapis_auth/extension_google_sign_in_as_googleapis_auth.dart';
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';

class CalendarRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  CalendarRepository(this._db);

  // Watch all events sorted by startTime
  Stream<List<CalendarEvent>> watchEvents() {
    return (_db.select(_db.calendarEvents)
          ..orderBy([
            (t) => OrderingTerm(expression: t.startTime, mode: OrderingMode.asc),
          ]))
        .watch();
  }

  // Insert calendar event
  Future<void> addEvent({
    required String title,
    String? description,
    required DateTime startTime,
    required DateTime endTime,
    bool isAllDay = false,
  }) async {
    final event = CalendarEventsCompanion.insert(
      id: _uuid.v4(),
      title: title,
      description: Value(description),
      startTime: startTime,
      endTime: endTime,
      isAllDay: isAllDay,
      createdAt: DateTime.now(),
    );
    await _db.into(_db.calendarEvents).insert(event);
  }

  // Update calendar event
  Future<void> updateEvent(CalendarEvent event) async {
    await _db.update(_db.calendarEvents).replace(event);
  }

  // Delete calendar event
  Future<void> deleteEvent(String id) async {
    await (_db.delete(_db.calendarEvents)..where((t) => t.id.equals(id))).go();
  }

  // Bidirectional Google Calendar Sync
  Future<List<String>> syncGoogleCalendar({required bool isMock}) async {
    final logs = <String>[];
    logs.add("Initializing sync process...");

    if (isMock) {
      // Simulated Sync for Offline / Dev environment
      await Future.delayed(const Duration(milliseconds: 1500));
      logs.add("Connected to local offline calendar provider.");

      final today = DateTime.now();
      final sampleEvents = [
        CalendarEventsCompanion.insert(
          id: _uuid.v4(),
          title: "AURA System Core Sync",
          description: const Value("Weekly synchronization of AURA AI local agents"),
          startTime: DateTime(today.year, today.month, today.day, 14, 0),
          endTime: DateTime(today.year, today.month, today.day, 15, 30),
          googleEventId: const Value("mock_g_event_1"),
          isAllDay: false,
          createdAt: DateTime.now(),
        ),
        CalendarEventsCompanion.insert(
          id: _uuid.v4(),
          title: "Google AI Research Reading Group",
          description: const Value("Read Gemini 1.5 Tech Report & Multi-Agent systems"),
          startTime: DateTime(today.year, today.month, today.day + 1, 10, 0),
          endTime: DateTime(today.year, today.month, today.day + 1, 12, 0),
          googleEventId: const Value("mock_g_event_2"),
          isAllDay: false,
          createdAt: DateTime.now(),
        ),
      ];

      for (final event in sampleEvents) {
        final existing = await (_db.select(_db.calendarEvents)
              ..where((t) => t.googleEventId.equals(event.googleEventId.value!)))
            .getSingleOrNull();

        if (existing == null) {
          await _db.into(_db.calendarEvents).insert(event);
          logs.add("Created mock event: ${event.title.value}");
        }
      }
      logs.add("Sync simulated successfully.");
      return logs;
    }

    try {
      final googleSignIn = GoogleSignIn.instance;
      
      // Request calendar events access scope
      final scopes = [
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events',
      ];

      logs.add("Requesting Google Calendar authorization...");
      final GoogleSignInClientAuthorization authorization = 
          await googleSignIn.authorizationClient.authorizeScopes(scopes);

      final client = authorization.authClient(scopes: scopes);

      final calendarApi = calendar_api.CalendarApi(client);
      logs.add("Established connection with Google Calendar API.");

      // 1. Fetch Google Calendar events (30 days window)
      final now = DateTime.now();
      final timeMin = now.subtract(const Duration(days: 30));
      final timeMax = now.add(const Duration(days: 30));

      final eventsList = await calendarApi.events.list(
        'primary',
        timeMin: timeMin.toUtc(),
        timeMax: timeMax.toUtc(),
        singleEvents: true,
      );

      final googleEvents = eventsList.items ?? [];
      logs.add("Retrieved ${googleEvents.length} events from Google Cloud Calendar.");

      // 2. Sync Google events into Drift database
      for (final ge in googleEvents) {
        if (ge.id == null || ge.summary == null) continue;

        final start = ge.start?.dateTime ?? ge.start?.date;
        final end = ge.end?.dateTime ?? ge.end?.date;
        if (start == null || end == null) continue;

        final existing = await (_db.select(_db.calendarEvents)
              ..where((t) => t.googleEventId.equals(ge.id!)))
            .getSingleOrNull();

        final companion = CalendarEventsCompanion(
          id: existing != null ? Value(existing.id) : Value(_uuid.v4()),
          title: Value(ge.summary!),
          description: Value(ge.description),
          startTime: Value(start.toLocal()),
          endTime: Value(end.toLocal()),
          googleEventId: Value(ge.id),
          isAllDay: Value(ge.start?.date != null),
          createdAt: Value(existing?.createdAt ?? DateTime.now()),
        );

        if (existing != null) {
          await (_db.update(_db.calendarEvents)..where((t) => t.id.equals(existing.id))).write(companion);
        } else {
          await _db.into(_db.calendarEvents).insert(companion);
          logs.add("Imported event: ${ge.summary}");
        }
      }

      // 3. Upload locally created events lacking a googleEventId
      final unsyncedEvents = await (_db.select(_db.calendarEvents)
            ..where((t) => t.googleEventId.isNull()))
          .get();

      if (unsyncedEvents.isNotEmpty) {
        logs.add("Uploading ${unsyncedEvents.length} new local events...");
        for (final le in unsyncedEvents) {
          final newEvent = calendar_api.Event()
            ..summary = le.title
            ..description = le.description
            ..start = (calendar_api.EventDateTime()
              ..dateTime = le.startTime.toUtc()
              ..timeZone = 'UTC')
            ..end = (calendar_api.EventDateTime()
              ..dateTime = le.endTime.toUtc()
              ..timeZone = 'UTC');

          try {
            final createdEvent = await calendarApi.events.insert(newEvent, 'primary');
            if (createdEvent.id != null) {
              await (_db.update(_db.calendarEvents)..where((t) => t.id.equals(le.id))).write(
                CalendarEventsCompanion(
                  googleEventId: Value(createdEvent.id),
                ),
              );
              logs.add("Uploaded event successfully: ${le.title}");
            }
          } catch (uploadError) {
            logs.add("Failed uploading '${le.title}': $uploadError");
          }
        }
      }

      logs.add("Google Calendar sync completed successfully!");
    } catch (e) {
      logs.add("Sync error occurred: $e");
    }

    return logs;
  }
}

final calendarRepositoryProvider = Provider<CalendarRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return CalendarRepository(db);
});

final calendarEventsStreamProvider = StreamProvider.autoDispose<List<CalendarEvent>>((ref) {
  final repository = ref.watch(calendarRepositoryProvider);
  return repository.watchEvents();
});
