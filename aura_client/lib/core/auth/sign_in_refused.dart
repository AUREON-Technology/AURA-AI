/// The backend refused to start a session for the account that just signed in with Google
/// (`TokenManager.startSession` returned `SessionStart.refused`), or Google returned no identity
/// token to exchange. Thrown by `AuthNotifier.signInWithGoogle` so the login page can say so,
/// instead of letting the user into a session nothing can renew (audit F-33).
class SignInRefused implements Exception {
  const SignInRefused([this.detail]);

  /// What the server (or Google) said, for developers. Never the only thing shown.
  final String? detail;

  @override
  String toString() => 'SignInRefused${detail == null ? '' : ': $detail'}';
}
