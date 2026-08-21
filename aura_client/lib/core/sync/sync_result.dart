/// Outcome of one sync attempt, so the UI can distinguish retryable
/// network/503 states from an auth failure without a large refactor.
enum SyncFailureKind { unauthorized, unavailable, network, unknown }

class SyncResult {
  final bool ok;
  final SyncFailureKind? failure;

  const SyncResult.success()
      : ok = true,
        failure = null;

  const SyncResult.fail(this.failure) : ok = false;
}
