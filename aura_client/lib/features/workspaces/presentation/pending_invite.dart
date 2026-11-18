import 'package:flutter_riverpod/flutter_riverpod.dart';

/// An invite token from a link opened while signed out, held **in memory only** until the
/// person signs in (SaaS P1-F2). It is a single-use credential: never written to disk or logged.
class PendingInvite extends Notifier<String?> {
  @override
  String? build() => null;

  void hold(String token) => state = token;

  /// Returns the token and forgets it.
  String? take() {
    final token = state;
    state = null;
    return token;
  }
}

final pendingInviteProvider = NotifierProvider<PendingInvite, String?>(PendingInvite.new);
