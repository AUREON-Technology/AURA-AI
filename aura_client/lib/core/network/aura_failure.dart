/// Turns a thrown error into something a person can act on.
///
/// The backend already answers with a stable, machine-readable `kind`
/// (`aura_core/domain/errors.py` — `all_providers_failed`, `rate_limited`,
/// `safety_violation`, ~30 of them) precisely so a client can branch on a slug
/// rather than parse prose. Until now the client did neither: every failure
/// rendered as its raw `message` in red, so "AURA is rate limited for 20
/// seconds", "you are signed out" and "the model is down" all looked like the
/// same unexplained breakage, and none of them said what to do next.
///
/// **What this deliberately does not claim.** A failed socket arrives as
/// `AuraApiError.unreachable` with no status code, and from the client's side
/// that is genuinely ambiguous: the network may be down, or the Control Plane
/// may be. Saying "you are offline" when the server is down is a lie that
/// sends the user to check their wifi. So the two collapse into one honestly
/// worded state. See `docs/ux/UX_TEST_PLAN.md` for the rest of rule 35's
/// vocabulary and which parts of it are not distinguishable today.
library;

import '../auth/sign_in_refused.dart';
import 'aura_api_error.dart';

/// Copy for [AuraFailureKind.featureUnavailable] (backend slug
/// `capability_unavailable`). Shared with the chat transcript, which shows a
/// failed turn as message text rather than through [AuraFailureView].
/// Owner-approved wording, 2026-11-08.
const kFeatureUnavailableTitle = "This needs a feature that isn't available yet";
const kFeatureUnavailableMessage =
    'AURA can answer plain questions right now. Answers that use your memory, '
    'documents, tools or images are coming soon.';

/// The kinds of failure that deserve different words and different buttons.
///
/// Anything not worth distinguishing collapses into [unknown] rather than
/// growing a case that says the same thing twice.
enum AuraFailureKind {
  /// Nothing answered. Could be the network, could be the Control Plane —
  /// the client cannot tell, and does not pretend to.
  unreachable,

  /// AURA answered; its model did not. Inference is down or every provider
  /// failed.
  inferenceUnavailable,

  /// AURA and its model are up, but no model it may use is allowed to see
  /// this turn's content (memory, documents, tools, images, the Engineer).
  /// The chat-only launch state. Retrying sends the same content to the same
  /// refusal, so this never offers a retry.
  featureUnavailable,

  /// AURA is up and busy. Different from [inferenceUnavailable] because
  /// waiting actually helps.
  busy,

  /// Rate limited, usually with a concrete wait.
  rateLimited,

  /// Another turn is already running for this user.
  turnInFlight,

  /// The session is no longer valid.
  authRequired,

  /// Signed in, but not allowed to do this.
  forbidden,

  /// Out of budget.
  budgetExceeded,

  /// Refused on safety grounds. Retrying unchanged will refuse again, so this
  /// must not offer a retry.
  blocked,

  /// The knowledge index is still building.
  indexing,

  /// A dependency timed out or is degraded.
  degraded,

  /// Everything else.
  unknown,
}

class AuraFailure {
  const AuraFailure({
    required this.kind,
    required this.title,
    required this.message,
    required this.canRetry,
    this.retryAfter,
    this.detail,
  });

  final AuraFailureKind kind;

  /// What happened, in the user's terms.
  final String title;

  /// Why, what is safe, and what to do — the three things an error message
  /// owes the reader.
  final String message;

  /// Whether offering "Retry" is honest. False for [blocked] and
  /// [forbidden], where the same request will fail the same way.
  final bool canRetry;

  /// How long to wait, when the server said.
  final Duration? retryAfter;

  /// The server's own message, kept for the cases where it carries specifics
  /// the generic copy cannot. Never the only thing shown.
  final String? detail;

  static AuraFailure from(Object? error) {
    if (error is AuraApiError) return _fromApiError(error);
    if (error is SignInRefused) {
      return AuraFailure(
        kind: AuraFailureKind.forbidden,
        title: 'AURA did not accept this sign-in',
        message: 'The server refused this Google account, so you have not been signed in. '
            'Try another account, or ask whoever runs this AURA server to check this one.',
        canRetry: false,
        detail: error.detail,
      );
    }
    return AuraFailure(
      kind: AuraFailureKind.unknown,
      title: 'Something went wrong',
      message: 'AURA hit an error it could not classify. Your work is unaffected.',
      canRetry: true,
      detail: error?.toString(),
    );
  }

  static AuraFailure _fromApiError(AuraApiError error) {
    final detail = error.message.isEmpty ? null : error.message;

    // Branch on the backend's own slug first; fall back to status code for
    // responses that never reached the domain error handler (a proxy's 502,
    // say).
    switch (error.error) {
      case 'unreachable':
        return AuraFailure(
          kind: AuraFailureKind.unreachable,
          title: "Couldn't reach AURA",
          message: 'Nothing answered — this may be your connection or the server. '
              'Anything you have already saved is safe on this device.',
          canRetry: true,
          detail: detail,
        );
      case 'all_providers_failed':
      case 'provider_error':
        return AuraFailure(
          kind: AuraFailureKind.inferenceUnavailable,
          title: 'AURA reached the server, but the model is not answering',
          message: 'The Control Plane is up; inference is not. Your workspace and '
              'conversations are untouched.',
          canRetry: true,
          retryAfter: error.retryAfter,
          detail: detail,
        );
      case 'capability_unavailable':
        return AuraFailure(
          kind: AuraFailureKind.featureUnavailable,
          title: kFeatureUnavailableTitle,
          message: kFeatureUnavailableMessage,
          canRetry: false,
          detail: detail,
        );
      case 'overloaded':
        return AuraFailure(
          kind: AuraFailureKind.busy,
          title: 'AURA is busy',
          message: 'Too much is running at once. Waiting a moment usually clears it.',
          canRetry: true,
          retryAfter: error.retryAfter,
          detail: detail,
        );
      case 'rate_limited':
        return AuraFailure(
          kind: AuraFailureKind.rateLimited,
          title: 'Too many requests',
          message: _waitSentence(error.retryAfter) ??
              'You have hit the request limit. Try again shortly.',
          canRetry: true,
          retryAfter: error.retryAfter,
          detail: detail,
        );
      case 'user_concurrency_limit':
        return AuraFailure(
          kind: AuraFailureKind.turnInFlight,
          title: 'AURA is still working on your last request',
          message: 'Only one turn runs at a time. Wait for it to finish, or stop it first.',
          canRetry: false,
          detail: detail,
        );
      case 'authentication_error':
        return AuraFailure(
          kind: AuraFailureKind.authRequired,
          title: 'Sign in again',
          message: 'Your session expired. Signing in again picks up exactly where you left off.',
          canRetry: false,
          detail: detail,
        );
      case 'authorization_error':
        return AuraFailure(
          kind: AuraFailureKind.forbidden,
          title: 'Not allowed',
          message: 'Your account does not have permission for this. Retrying will not change that.',
          canRetry: false,
          detail: detail,
        );
      case 'budget_exceeded':
        return AuraFailure(
          kind: AuraFailureKind.budgetExceeded,
          title: 'Budget reached',
          message: 'This request would go over the configured spend limit.',
          canRetry: false,
          detail: detail,
        );
      case 'safety_violation':
        return AuraFailure(
          kind: AuraFailureKind.blocked,
          title: 'AURA declined this request',
          message: 'It was refused on safety grounds. Sending the same thing again will be '
              'refused the same way — rephrasing is what changes the outcome.',
          canRetry: false,
          detail: detail,
        );
      case 'index_not_ready':
        return AuraFailure(
          kind: AuraFailureKind.indexing,
          title: 'Still indexing',
          message: 'AURA is building the search index for this content. It will work once '
              'that finishes.',
          canRetry: true,
          detail: detail,
        );
      case 'timeout':
      case 'dependency_unavailable':
        return AuraFailure(
          kind: AuraFailureKind.degraded,
          title: 'AURA is degraded',
          message: 'Part of the system did not respond in time. Your data is unaffected.',
          canRetry: true,
          detail: detail,
        );
    }

    // No recognised slug — fall back to the status code.
    final status = error.statusCode;
    if (status == null) {
      return AuraFailure(
        kind: AuraFailureKind.unreachable,
        title: "Couldn't reach AURA",
        message: 'Nothing answered — this may be your connection or the server. '
            'Anything you have already saved is safe on this device.',
        canRetry: true,
        detail: detail,
      );
    }
    if (status == 401) {
      return AuraFailure(
        kind: AuraFailureKind.authRequired,
        title: 'Sign in again',
        message: 'Your session expired. Signing in again picks up exactly where you left off.',
        canRetry: false,
        detail: detail,
      );
    }
    if (status == 403) {
      return AuraFailure(
        kind: AuraFailureKind.forbidden,
        title: 'Not allowed',
        message: 'Your account does not have permission for this. Retrying will not change that.',
        canRetry: false,
        detail: detail,
      );
    }
    if (status == 429) {
      return AuraFailure(
        kind: AuraFailureKind.rateLimited,
        title: 'Too many requests',
        message: _waitSentence(error.retryAfter) ??
            'You have hit the request limit. Try again shortly.',
        canRetry: true,
        retryAfter: error.retryAfter,
        detail: detail,
      );
    }
    if (status >= 500) {
      return AuraFailure(
        kind: AuraFailureKind.degraded,
        title: 'AURA had a problem',
        message: 'The server failed to complete this. Your data is unaffected.',
        canRetry: true,
        retryAfter: error.retryAfter,
        detail: detail,
      );
    }
    return AuraFailure(
      kind: AuraFailureKind.unknown,
      title: 'Something went wrong',
      message: 'AURA could not complete this request. Your work is unaffected.',
      canRetry: error.isRetryable,
      detail: detail,
    );
  }

  static String? _waitSentence(Duration? retryAfter) {
    if (retryAfter == null) return null;
    final seconds = retryAfter.inSeconds;
    if (seconds <= 0) return null;
    if (seconds < 60) {
      return 'You have hit the request limit. Try again in $seconds second'
          '${seconds == 1 ? '' : 's'}.';
    }
    final minutes = (seconds / 60).ceil();
    return 'You have hit the request limit. Try again in about $minutes minute'
        '${minutes == 1 ? '' : 's'}.';
  }
}
