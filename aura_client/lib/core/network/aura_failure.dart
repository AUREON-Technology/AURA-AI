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

import 'package:aura_client/l10n/l10n.dart';

import '../auth/desktop_oauth_client.dart' show DesktopOAuthCancelledException;
import '../auth/sign_in_refused.dart';
import 'aura_api_error.dart';

/// Copy for [AuraFailureKind.featureUnavailable] (backend slug
/// `capability_unavailable`). Shared with the chat transcript, which shows a
/// failed turn as message text rather than through [AuraFailureView].
/// Owner-approved wording, 2026-11-08. Getters, not constants: the text is read in the
/// language on screen ([currentL10n]) at the moment it is built.
String get kFeatureUnavailableTitle => currentL10n.failureFeatureUnavailableTitle;
String get kFeatureUnavailableMessage => currentL10n.failureFeatureUnavailableMessage;

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

  /// The workspace's plan does not allow this (SaaS P1-C, HTTP 402 `plan_limit`): this month's
  /// messages, the Engineer agent, storage or team seats. Retrying is refused the same way until
  /// the month resets or the plan changes.
  planLimit,

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
    if (error is SignInRefused && error.planRequired) {
      return AuraFailure(
        kind: AuraFailureKind.forbidden,
        title: currentL10n.failureDesktopPlanRequiredTitle,
        message: currentL10n.failureDesktopPlanRequiredMessage,
        canRetry: false,
        detail: error.detail,
      );
    }
    if (error is SignInRefused) {
      return AuraFailure(
        kind: AuraFailureKind.forbidden,
        title: currentL10n.failureSignInRefusedTitle,
        message: currentL10n.failureSignInRefusedMessage,
        canRetry: false,
        detail: error.detail,
      );
    }
    if (error is DesktopOAuthCancelledException) {
      // The desktop sign-in flow's own failures (cancelled in the browser, Google or the AURA
      // server refusing the code, nothing answering). Each message is written to be read --
      // by the app or by the server's `detail` -- and carries no credential. Until this case
      // existed they all rendered as "could not classify", which is how a sign-in that failed
      // after the browser step looked identical to an unrelated crash.
      return AuraFailure(
        kind: AuraFailureKind.unknown,
        title: currentL10n.failureSignInIncompleteTitle,
        message: error.message,
        canRetry: true,
      );
    }
    return AuraFailure(
      kind: AuraFailureKind.unknown,
      title: currentL10n.commonSomethingWentWrong,
      message: currentL10n.failureUnclassifiedMessage,
      canRetry: true,
      detail: error?.toString(),
    );
  }

  static const _months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];

  static AuraFailure _planLimit(Map<String, dynamic> details, String? detail) {
    final upgrade = 'Upgrade the plan to raise the limit.';
    switch (details['limit']) {
      case 'messages':
        final resets = DateTime.tryParse('${details['resets_at']}')?.toUtc();
        final when = resets == null ? 'next month' : 'on ${_months[resets.month - 1]} ${resets.day}';
        return AuraFailure(
          kind: AuraFailureKind.planLimit,
          title: "You've used this month's messages",
          message: 'This workspace has sent all the messages its plan includes. They come back '
              '$when. $upgrade',
          canRetry: false,
          detail: detail,
        );
      case 'engineer':
        return AuraFailure(
          kind: AuraFailureKind.planLimit,
          title: 'The Engineer agent is on Pro and Team plans',
          message: "This workspace's plan doesn't include it. Everything else keeps working. $upgrade",
          canRetry: false,
          detail: detail,
        );
      case 'storage':
        return AuraFailure(
          kind: AuraFailureKind.planLimit,
          title: 'Knowledge storage is full',
          message: 'Delete documents you no longer need to make room, or upgrade the plan for more storage.',
          canRetry: false,
          detail: detail,
        );
      case 'seats':
        return AuraFailure(
          kind: AuraFailureKind.planLimit,
          title: 'This team has no free seats',
          message: 'Remove a member or a pending invite, or add seats to the plan.',
          canRetry: false,
          detail: detail,
        );
    }
    return AuraFailure(
      kind: AuraFailureKind.planLimit,
      title: "This isn't included in your plan",
      message: upgrade,
      canRetry: false,
      detail: detail,
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
          title: currentL10n.failureUnreachableTitle,
          message: currentL10n.failureUnreachableMessage,
          canRetry: true,
          detail: detail,
        );
      case 'all_providers_failed':
      case 'provider_error':
        return AuraFailure(
          kind: AuraFailureKind.inferenceUnavailable,
          title: currentL10n.failureInferenceUnavailableTitle,
          message: currentL10n.failureInferenceUnavailableMessage,
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
          title: currentL10n.failureBusyTitle,
          message: currentL10n.failureBusyMessage,
          canRetry: true,
          retryAfter: error.retryAfter,
          detail: detail,
        );
      case 'rate_limited':
        return AuraFailure(
          kind: AuraFailureKind.rateLimited,
          title: currentL10n.failureRateLimitedTitle,
          message: _waitSentence(error.retryAfter) ?? currentL10n.failureRateLimitedMessage,
          canRetry: true,
          retryAfter: error.retryAfter,
          detail: detail,
        );
      case 'user_concurrency_limit':
        return AuraFailure(
          kind: AuraFailureKind.turnInFlight,
          title: currentL10n.failureTurnInFlightTitle,
          message: currentL10n.failureTurnInFlightMessage,
          canRetry: false,
          detail: detail,
        );
      case 'authentication_error':
        return AuraFailure(
          kind: AuraFailureKind.authRequired,
          title: currentL10n.failureAuthRequiredTitle,
          message: currentL10n.failureAuthRequiredMessage,
          canRetry: false,
          detail: detail,
        );
      case 'authorization_error':
        return AuraFailure(
          kind: AuraFailureKind.forbidden,
          title: currentL10n.failureForbiddenTitle,
          message: currentL10n.failureForbiddenMessage,
          canRetry: false,
          detail: detail,
        );
      case 'plan_limit':
        return _planLimit(error.details, detail);
      case 'budget_exceeded':
        return AuraFailure(
          kind: AuraFailureKind.budgetExceeded,
          title: currentL10n.failureBudgetTitle,
          message: currentL10n.failureBudgetMessage,
          canRetry: false,
          detail: detail,
        );
      case 'safety_violation':
        return AuraFailure(
          kind: AuraFailureKind.blocked,
          title: currentL10n.failureBlockedTitle,
          message: currentL10n.failureBlockedMessage,
          canRetry: false,
          detail: detail,
        );
      case 'index_not_ready':
        return AuraFailure(
          kind: AuraFailureKind.indexing,
          title: currentL10n.failureIndexingTitle,
          message: currentL10n.failureIndexingMessage,
          canRetry: true,
          detail: detail,
        );
      case 'timeout':
      case 'dependency_unavailable':
        return AuraFailure(
          kind: AuraFailureKind.degraded,
          title: currentL10n.failureDegradedTitle,
          message: currentL10n.failureDegradedMessage,
          canRetry: true,
          detail: detail,
        );
      case 'confirmation_invalid':
        return AuraFailure(
          kind: AuraFailureKind.unknown,
          title: currentL10n.commonSomethingWentWrong,
          message: currentL10n.chatApprovalExpiredNotice,
          canRetry: false,
          detail: detail,
        );
    }

    // No recognised slug — fall back to the status code.
    final status = error.statusCode;
    if (status == 402) return _planLimit(error.details, detail);
    if (status == null) {
      return AuraFailure(
        kind: AuraFailureKind.unreachable,
        title: currentL10n.failureUnreachableTitle,
        message: currentL10n.failureUnreachableMessage,
        canRetry: true,
        detail: detail,
      );
    }
    if (status == 401) {
      return AuraFailure(
        kind: AuraFailureKind.authRequired,
        title: currentL10n.failureAuthRequiredTitle,
        message: currentL10n.failureAuthRequiredMessage,
        canRetry: false,
        detail: detail,
      );
    }
    if (status == 403) {
      return AuraFailure(
        kind: AuraFailureKind.forbidden,
        title: currentL10n.failureForbiddenTitle,
        message: currentL10n.failureForbiddenMessage,
        canRetry: false,
        detail: detail,
      );
    }
    if (status == 429) {
      return AuraFailure(
        kind: AuraFailureKind.rateLimited,
        title: currentL10n.failureRateLimitedTitle,
        message: _waitSentence(error.retryAfter) ?? currentL10n.failureRateLimitedMessage,
        canRetry: true,
        retryAfter: error.retryAfter,
        detail: detail,
      );
    }
    if (status >= 500) {
      return AuraFailure(
        kind: AuraFailureKind.degraded,
        title: currentL10n.failureServerErrorTitle,
        message: currentL10n.failureServerErrorMessage,
        canRetry: true,
        retryAfter: error.retryAfter,
        detail: detail,
      );
    }
    return AuraFailure(
      kind: AuraFailureKind.unknown,
      title: currentL10n.commonSomethingWentWrong,
      message: currentL10n.failureUnknownMessage,
      canRetry: error.isRetryable,
      detail: detail,
    );
  }

  static String? _waitSentence(Duration? retryAfter) {
    if (retryAfter == null) return null;
    final seconds = retryAfter.inSeconds;
    if (seconds <= 0) return null;
    if (seconds < 60) return currentL10n.failureRateLimitedWaitSeconds(seconds);
    final minutes = (seconds / 60).ceil();
    return currentL10n.failureRateLimitedWaitMinutes(minutes);
  }
}
