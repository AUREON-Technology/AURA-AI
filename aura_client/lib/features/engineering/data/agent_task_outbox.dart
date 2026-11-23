// Offline queue for agent task submissions (inventory row 91, client half of ADR-041).
//
// A task sent while the device is offline (or while the server is down) is not lost and is not
// sent twice. Every submission is written to a small JSON list in local preferences *before* the
// first network attempt, with a generated `client_request_id`; it is then replayed in order, with
// the SAME id on every attempt, until the server answers. The server's idempotency does the rest:
//
//   * 202                 accepted: remove from the queue.
//   * 200 idempotent      an earlier attempt got through and only its answer was lost: remove.
//   * network, 5xx, 429,  keep, with backoff (honouring Retry-After). Nothing was accepted, or we
//     401, 409 in-progress cannot tell, and a replay under the same id is safe either way.
//   * every other 4xx     (403 consent, 402 plan limit, 422, 409 key-reused ...) the server will
//                         never accept this request as it stands: keep it VISIBLE as "not sent"
//                         with the reason, never silently drop it, and never retry it.
//
// Order is preserved: while the oldest waiting entry is backing off or the server is unreachable,
// the ones behind it wait too.
//
// Why preferences and not the Drift outbox: that outbox is the `/api/v3/sync` mutation log (one
// batched push of table rows). A task submission is a different protocol with its own idempotency
// contract, and adding a Drift table for it would need a schema migration for a list that is a
// few entries long. What this reuses from the sync engine is the shape -- a periodic trigger and
// a durable queue drained oldest-first -- and the same "park, keep visible" rule for rows the
// server refuses for good (`OutboxEntries.attempts`, audit F-11).

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';

import 'agent_task_repository.dart';

const _prefsKey = 'aura.agent_task_outbox.v1';

/// Most submissions kept at once (waiting or failed). A queue that grows without bound for a
/// device left offline is a bug report waiting to happen.
const int kMaxQueuedAgentTasks = 50;

/// The backend's ceiling for a goal (`goal: max_length=32_000`).
const int kMaxAgentGoalChars = 32000;

/// How long to wait before attempt number [attempts] + 1: 5 s, 10 s, 20 s ... capped at 5 minutes.
Duration agentTaskBackoff(int attempts) {
  if (attempts <= 0) return Duration.zero;
  final seconds = 5 * (1 << (attempts - 1).clamp(0, 6));
  return Duration(seconds: seconds > 300 ? 300 : seconds);
}

enum QueuedAgentTaskState {
  /// Will be (re)sent.
  waiting,

  /// The server refused it for good; kept so the person sees it, and never sent again.
  failed,
}

/// One submission that has not been accepted yet.
class QueuedAgentTask {
  const QueuedAgentTask({
    required this.clientRequestId,
    required this.owner,
    required this.goal,
    required this.createdAt,
    this.persona,
    this.attempts = 0,
    this.nextAttemptAt,
    this.state = QueuedAgentTaskState.waiting,
    this.lastError,
    this.errorKind,
  });

  /// Generated once and reused on every replay: the server's idempotency key.
  final String clientRequestId;

  /// The signed-in user it belongs to; another account's entries are never sent or shown.
  final String owner;
  final String goal;
  final String? persona;
  final DateTime createdAt;
  final int attempts;
  final DateTime? nextAttemptAt;
  final QueuedAgentTaskState state;
  final String? lastError;
  final String? errorKind;

  bool get isFailed => state == QueuedAgentTaskState.failed;

  QueuedAgentTask copyWith({
    int? attempts,
    DateTime? nextAttemptAt,
    QueuedAgentTaskState? state,
    String? lastError,
    String? errorKind,
  }) =>
      QueuedAgentTask(
        clientRequestId: clientRequestId,
        owner: owner,
        goal: goal,
        persona: persona,
        createdAt: createdAt,
        attempts: attempts ?? this.attempts,
        nextAttemptAt: nextAttemptAt ?? this.nextAttemptAt,
        state: state ?? this.state,
        lastError: lastError ?? this.lastError,
        errorKind: errorKind ?? this.errorKind,
      );

  Map<String, dynamic> toJson() => {
        'client_request_id': clientRequestId,
        'owner': owner,
        'goal': goal,
        if (persona != null) 'persona': persona,
        'created_at': createdAt.toIso8601String(),
        'attempts': attempts,
        if (nextAttemptAt != null) 'next_attempt_at': nextAttemptAt!.toIso8601String(),
        'state': state.name,
        if (lastError != null) 'last_error': lastError,
        if (errorKind != null) 'error_kind': errorKind,
      };

  static QueuedAgentTask? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final id = raw['client_request_id']?.toString();
    final goal = raw['goal']?.toString();
    if (id == null || id.isEmpty || goal == null || goal.isEmpty) return null;
    return QueuedAgentTask(
      clientRequestId: id,
      owner: raw['owner']?.toString() ?? '',
      goal: goal,
      persona: raw['persona']?.toString(),
      createdAt: DateTime.tryParse(raw['created_at']?.toString() ?? '') ?? DateTime.now(),
      attempts: raw['attempts'] is int ? raw['attempts'] as int : 0,
      nextAttemptAt: DateTime.tryParse(raw['next_attempt_at']?.toString() ?? ''),
      state: raw['state'] == 'failed' ? QueuedAgentTaskState.failed : QueuedAgentTaskState.waiting,
      lastError: raw['last_error']?.toString(),
      errorKind: raw['error_kind']?.toString(),
    );
  }
}

class AgentTaskOutboxState {
  const AgentTaskOutboxState({
    this.entries = const [],
    this.sendingId,
    this.lastAccepted,
    this.acceptedCount = 0,
  });

  /// Oldest first. Includes every account's entries; use [forOwner] for what to show.
  final List<QueuedAgentTask> entries;

  /// The entry whose request is in flight right now.
  final String? sendingId;

  /// The most recent submission the server accepted (for a "sent" confirmation).
  final AgentTaskAccepted? lastAccepted;

  /// Bumps on every accepted submission, so a screen can refresh its server list.
  final int acceptedCount;

  List<QueuedAgentTask> forOwner(String owner) => entries.where((e) => e.owner == owner).toList();

  AgentTaskOutboxState copyWith({
    List<QueuedAgentTask>? entries,
    Object? sendingId = _keep,
    AgentTaskAccepted? lastAccepted,
    int? acceptedCount,
  }) =>
      AgentTaskOutboxState(
        entries: entries ?? this.entries,
        sendingId: identical(sendingId, _keep) ? this.sendingId : sendingId as String?,
        lastAccepted: lastAccepted ?? this.lastAccepted,
        acceptedCount: acceptedCount ?? this.acceptedCount,
      );
}

const Object _keep = Object();

/// Clock for backoff, replaceable in tests.
final agentTaskOutboxClockProvider = Provider<DateTime Function()>((ref) => DateTime.now);

final _uuid = const Uuid();

class AgentTaskOutbox extends Notifier<AgentTaskOutboxState> {
  late Future<void> ready;
  bool _flushing = false;

  @override
  AgentTaskOutboxState build() {
    ready = _restore();
    return const AgentTaskOutboxState();
  }

  DateTime _now() => ref.read(agentTaskOutboxClockProvider)();

  String? _owner() => ref.read(authProvider).value?.id;

  Future<void> _restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw == null) return;
      final decoded = jsonDecode(raw);
      if (decoded is! List) return;
      final restored = decoded.map(QueuedAgentTask.fromJson).whereType<QueuedAgentTask>().toList();
      // Anything queued before this finished loading (it cannot be: enqueue awaits `ready`)
      // would be kept after the restored ones.
      state = state.copyWith(entries: [...restored, ...state.entries]);
    } catch (e) {
      // A corrupt list is not worth failing start-up over; the queue just starts empty.
      debugPrint('[AgentTaskOutbox] could not read the saved queue: $e');
    }
  }

  Future<void> _persist() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, jsonEncode(state.entries.map((e) => e.toJson()).toList()));
    } catch (e) {
      debugPrint('[AgentTaskOutbox] could not save the queue: $e');
    }
  }

  /// Queues a submission and tries to send the queue at once. Returns the entry (it may already
  /// be gone from the queue if the server accepted it) or null when the queue is full or [goal]
  /// is empty. The entry is saved before the first attempt, so even a crash mid-request replays
  /// under the same `client_request_id`.
  Future<QueuedAgentTask?> enqueue(String goal, {String? persona}) async {
    await ready;
    final text = goal.trim();
    final owner = _owner();
    if (text.isEmpty || text.length > kMaxAgentGoalChars || owner == null) return null;
    if (state.forOwner(owner).length >= kMaxQueuedAgentTasks) return null;
    final entry = QueuedAgentTask(
      clientRequestId: _uuid.v4(),
      owner: owner,
      goal: text,
      persona: persona,
      createdAt: _now(),
    );
    state = state.copyWith(entries: [...state.entries, entry]);
    await _persist();
    await flush(force: true);
    return entry;
  }

  /// Sends the waiting entries oldest-first. Stops at the first one that must wait (offline,
  /// server error, backing off) so order is kept. [force] ignores the backoff clock: used for a
  /// new submission, the app resuming, and a person pressing "retry now".
  Future<void> flush({bool force = false}) async {
    if (_flushing) return;
    await ready;
    final owner = _owner();
    if (owner == null) return;
    _flushing = true;
    try {
      while (ref.mounted) {
        final next = state.entries
            .where((e) => e.owner == owner && e.state == QueuedAgentTaskState.waiting)
            .firstOrNull;
        if (next == null) break;
        if (!force && next.nextAttemptAt != null && next.nextAttemptAt!.isAfter(_now())) break;
        final keepGoing = await _attempt(next);
        if (!keepGoing) break;
      }
    } finally {
      _flushing = false;
      if (ref.mounted) state = state.copyWith(sendingId: null);
    }
  }

  /// One send. Returns true when the next entry may be tried (this one is settled: accepted or
  /// refused for good), false when it must wait.
  Future<bool> _attempt(QueuedAgentTask entry) async {
    state = state.copyWith(sendingId: entry.clientRequestId);
    try {
      final accepted = await ref.read(agentTaskRepositoryProvider).submit(
            goal: entry.goal,
            clientRequestId: entry.clientRequestId,
            persona: entry.persona,
          );
      if (!ref.mounted) return false;
      state = state.copyWith(
        entries: state.entries.where((e) => e.clientRequestId != entry.clientRequestId).toList(),
        lastAccepted: accepted,
        acceptedCount: state.acceptedCount + 1,
      );
      await _persist();
      return true;
    } on AuraApiError catch (e) {
      if (!ref.mounted) return false;
      if (_refusedForGood(e)) {
        _replace(entry.copyWith(
          state: QueuedAgentTaskState.failed,
          lastError: e.message,
          errorKind: e.error,
          attempts: entry.attempts + 1,
        ));
        await _persist();
        return true;
      }
      await _scheduleRetry(entry, e.message, e.retryAfter);
      return false;
    } catch (e) {
      if (!ref.mounted) return false;
      await _scheduleRetry(entry, '$e', null);
      return false;
    }
  }

  /// Whether replaying [e] can never succeed. Network failures, 5xx, 429 (rate limit and
  /// `task_queue_full`, both with Retry-After), a lapsed login (401) and a 409 that only says the
  /// first attempt is still being accepted are all "try again"; everything else in 4xx is the
  /// server refusing this request as it stands.
  static bool _refusedForGood(AuraApiError e) {
    final code = e.statusCode;
    if (code == null || code >= 500) return false;
    if (code == 429 || code == 401 || code == 408) return false;
    if (code == 409 && e.error == 'idempotency_in_progress') return false;
    return code >= 400 && code < 500;
  }

  Future<void> _scheduleRetry(QueuedAgentTask entry, String message, Duration? retryAfter) async {
    final attempts = entry.attempts + 1;
    var wait = agentTaskBackoff(attempts);
    if (retryAfter != null && retryAfter > wait) wait = retryAfter;
    _replace(entry.copyWith(attempts: attempts, nextAttemptAt: _now().add(wait), lastError: message));
    await _persist();
  }

  void _replace(QueuedAgentTask updated) {
    state = state.copyWith(
      entries: [
        for (final e in state.entries) e.clientRequestId == updated.clientRequestId ? updated : e,
      ],
    );
  }

  /// Removes one entry (a failed one the person has read, or a waiting one they changed their
  /// mind about). Removing a waiting entry that was already accepted server-side cannot undo
  /// that; it only stops further replays.
  Future<void> dismiss(String clientRequestId) async {
    await ready;
    state = state.copyWith(
      entries: state.entries.where((e) => e.clientRequestId != clientRequestId).toList(),
    );
    await _persist();
  }

  /// Sends a refused entry's text again as a NEW submission (a new `client_request_id`: the old
  /// key would only return the old refusal).
  Future<QueuedAgentTask?> resendAsNew(String clientRequestId) async {
    final old = state.entries.where((e) => e.clientRequestId == clientRequestId).firstOrNull;
    if (old == null) return null;
    await dismiss(clientRequestId);
    return enqueue(old.goal, persona: old.persona);
  }

  /// Drops everything: sign-out and account deletion, so the next account never replays this
  /// one's tasks.
  Future<void> clear() async {
    state = const AgentTaskOutboxState();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_prefsKey);
    } catch (_) {}
  }
}

final agentTaskOutboxProvider = NotifierProvider<AgentTaskOutbox, AgentTaskOutboxState>(AgentTaskOutbox.new);

/// Replays the queue when connectivity may have returned: when the signed-in session starts, every
/// 30 s while the app is open (a send that fails on the network backs off, so this is cheap), and
/// when the app comes back to the foreground. Listened to by `signedInSessionProvider`.
///
/// There is no connectivity plugin in this app, on purpose: "online" is whether the request
/// reaches the server, and the same unreachable error that parks an entry is the signal.
final agentTaskOutboxDriverProvider = Provider<void>((ref) {
  final signedIn = ref.watch(authProvider.select((auth) => auth.value != null));
  if (!signedIn) return;

  final outbox = ref.read(agentTaskOutboxProvider.notifier);
  final timer = Timer.periodic(const Duration(seconds: 30), (_) => unawaited(outbox.flush()));
  final observer = _ResumeObserver(() => unawaited(outbox.flush(force: true)));
  WidgetsBinding.instance.addObserver(observer);
  ref.onDispose(() {
    timer.cancel();
    WidgetsBinding.instance.removeObserver(observer);
  });
  Future.microtask(() {
    if (ref.mounted) unawaited(outbox.flush());
  });
});

class _ResumeObserver with WidgetsBindingObserver {
  _ResumeObserver(this._onResume);
  final VoidCallback _onResume;

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _onResume();
  }
}
