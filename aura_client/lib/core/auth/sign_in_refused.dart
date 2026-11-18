/// The backend refused to start a session for the account that just signed in with Google
/// (`TokenManager.startSession` returned `SessionStart.refused`), or Google returned no identity
/// token to exchange. Thrown by `AuthNotifier.signInWithGoogle` so the login page can say so,
/// instead of letting the user into a session nothing can renew (audit F-33).
class SignInRefused implements Exception {
  const SignInRefused([this.detail, this.planRequired = false]);

  /// What the server (or Google) said, for developers. Never the only thing shown.
  final String? detail;

  /// The server marked this refusal `X-Aura-Refusal: plan_required`: a Pro or Team plan would
  /// let this account in (module readiness WP11), so the app says that instead of a bare "no".
  final bool planRequired;

  @override
  String toString() => 'SignInRefused${detail == null ? '' : ': $detail'}';
}
