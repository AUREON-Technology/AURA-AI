import 'dart:async';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';

/// Who signed in, as the identity provider vouches for them (SaaS P1-B).
///
/// [uid] is the Firebase uid. For anyone who used AURA before Plan B it equals the Google
/// subject their data is stored under: `aura_backend/scripts/import_users_to_firebase.py` created
/// them in Firebase with that uid, and Firebase's "link accounts that use the same email" setting
/// keeps every later Google sign-in on it. (Firebase does not merge a *different* method for an
/// address that already has an account on its own: that sign-in fails with
/// `account-exists-with-different-credential` until the methods are linked.)
@immutable
class IdentityUser {
  const IdentityUser({
    required this.uid,
    required this.email,
    required this.displayName,
    required this.emailVerified,
    required this.provider,
    this.photoUrl,
  });

  final String uid;
  final String email;
  final String displayName;
  final String? photoUrl;
  final bool emailVerified;

  /// `google.com`, `apple.com` or `password`.
  final String provider;
}

enum IdentityErrorKind {
  cancelled,
  wrongCredentials,
  emailInUse,
  weakPassword,
  invalidEmail,
  network,
  accountExistsWithDifferentMethod,
  /// The sign-in being linked already belongs to another account (SaaS P1-F2).
  credentialInUse,
  /// Firebase wants a fresh sign-in before changing a second factor (SaaS P1-I).
  reauthRequired,
  /// An authenticator code that did not match (SaaS P1-I).
  wrongCode,
  unavailable,
}

/// A sign-in the identity provider did not complete. [kind] is what the login page words; the
/// provider's own [message] is for logs only.
class IdentityError implements Exception {
  const IdentityError(this.kind, this.message);

  final IdentityErrorKind kind;
  final String message;

  @override
  String toString() => 'IdentityError(${kind.name}): $message';
}

/// Maps a `FirebaseAuthException.code` (web and native spellings) to what the page can say.
IdentityErrorKind identityErrorKindFor(String code) {
  switch (code) {
    case 'wrong-password':
    case 'user-not-found':
    case 'invalid-credential':
    case 'invalid-login-credentials':
      return IdentityErrorKind.wrongCredentials;
    case 'email-already-in-use':
      return IdentityErrorKind.emailInUse;
    case 'weak-password':
      return IdentityErrorKind.weakPassword;
    case 'invalid-email':
      return IdentityErrorKind.invalidEmail;
    case 'network-request-failed':
      return IdentityErrorKind.network;
    case 'account-exists-with-different-credential':
      return IdentityErrorKind.accountExistsWithDifferentMethod;
    case 'credential-already-in-use':
    case 'provider-already-linked':
      return IdentityErrorKind.credentialInUse;
    case 'requires-recent-login':
      return IdentityErrorKind.reauthRequired;
    case 'invalid-verification-code':
    case 'invalid-mfa-verification-code':
      return IdentityErrorKind.wrongCode;
    case 'popup-closed-by-user':
    case 'cancelled-popup-request':
    case 'web-context-canceled':
    case 'web-context-cancelled':
    case 'canceled':
      return IdentityErrorKind.cancelled;
    default:
      return IdentityErrorKind.unavailable;
  }
}

/// A second sign-in step the account has enrolled (SaaS P1-I: authenticator codes only).
@immutable
class SecondFactor {
  const SecondFactor({required this.uid, required this.displayName, this.enrolledAt});
  final String uid;
  final String displayName;
  final DateTime? enrolledAt;
}

/// An authenticator secret being set up. Shown once on the enroll screen; never stored or logged.
class TotpEnrollment {
  const TotpEnrollment({required this.secretKey, required this.otpauthUrl, this.raw});

  /// For typing into an authenticator by hand.
  final String secretKey;

  /// `otpauth://totp/...`: opens an authenticator app, or renders as a QR code.
  final String otpauthUrl;

  /// The plugin's `TotpSecret`, which [IdentityService.finishTotpEnrollment] needs back.
  final Object? raw;
}

/// Thrown by every sign-in method when the account has a second step. The sign-in finishes
/// only through [resolve]; until then Firebase issues no ID token, so no backend session exists.
class SecondFactorRequired implements Exception {
  const SecondFactorRequired({required this.totp, required this.resolve});

  /// False when the account's only second factor is one phase 1 cannot answer (SMS).
  final bool totp;

  /// Throws [IdentityError] with [IdentityErrorKind.wrongCode] for a code that does not match;
  /// the same challenge can then be tried again.
  final Future<IdentityUser> Function(String code) resolve;

  @override
  String toString() => 'SecondFactorRequired(totp: $totp)';
}

/// The one way the app proves who someone is. Every method ends with a Firebase user whose ID
/// token [idToken] returns; `AuthNotifier` exchanges that token for a backend session.
abstract class IdentityService {
  IdentityUser? get current;

  /// One-time setup (Google's client ids). Safe to call more than once.
  Future<void> initialize();

  /// The user Firebase restored from a previous launch, if any.
  Future<IdentityUser?> restore();

  Future<IdentityUser> signInWithGoogle();
  Future<IdentityUser> signInWithApple();
  Future<IdentityUser> signInWithEmail(String email, String password);

  /// Creates the account and sends the verification email.
  Future<IdentityUser> registerWithEmail(String email, String password, {String? displayName});

  /// Always completes normally for a well-formed address, so the page can never reveal whether
  /// an account exists.
  Future<void> sendPasswordReset(String email);
  Future<void> resendVerification();

  /// Re-reads the signed-in user from the provider (e.g. after they clicked the verification
  /// link), returning the fresh state.
  Future<IdentityUser?> reload();
  Future<String?> idToken({bool forceRefresh = false});
  Future<void> signOut();

  /// The sign-in methods on this account: `google.com`, `apple.com`, `password` (SaaS P1-F2).
  List<String> linkedProviders();

  /// Adds a sign-in method to the signed-in account, so the same AURA account opens with it.
  /// Firebase keeps one account per email and does not merge on its own (spec §5.1).
  Future<void> linkGoogle();
  Future<void> linkApple();
  Future<void> linkEmailPassword(String email, String password);

  // -- two-step verification (SaaS P1-I) ---------------------------------------------------
  Future<List<SecondFactor>> secondFactors();
  Future<TotpEnrollment> startTotpEnrollment();
  Future<void> finishTotpEnrollment(TotpEnrollment enrollment, String code);
  Future<void> removeSecondFactor(String uid);
}

const _googleWebClientId = '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com';

class FirebaseIdentityService implements IdentityService {
  FirebaseIdentityService({FirebaseAuth? auth, GoogleSignIn? google})
      : _authOverride = auth,
        _google = google ?? GoogleSignIn.instance;

  final FirebaseAuth? _authOverride;
  final GoogleSignIn _google;
  bool _googleReady = false;

  FirebaseAuth get _auth => _authOverride ?? FirebaseAuth.instance;

  @override
  IdentityUser? get current => _toUser(_auth.currentUser);

  @override
  Future<void> initialize() async {
    if (_googleReady || kIsWeb) return; // the web signs in to Google through a Firebase popup
    await _google.initialize(serverClientId: _googleWebClientId);
    _googleReady = true;
  }

  @override
  Future<IdentityUser?> restore() async {
    final now = _auth.currentUser;
    if (now != null) return _toUser(now);
    try {
      // Firebase restores its persisted user asynchronously on start-up.
      final restored = await _auth.authStateChanges().first.timeout(const Duration(seconds: 3));
      return _toUser(restored);
    } on TimeoutException {
      return null;
    }
  }

  @override
  Future<IdentityUser> signInWithGoogle() => _guard(() async {
        if (kIsWeb) return (await _auth.signInWithPopup(GoogleAuthProvider())).user;
        await initialize();
        final account = await _google.authenticate();
        final idToken = account.authentication.idToken;
        if (idToken == null) {
          throw const IdentityError(IdentityErrorKind.unavailable, 'Google returned no ID token');
        }
        return (await _auth.signInWithCredential(GoogleAuthProvider.credential(idToken: idToken))).user;
      });

  @override
  Future<IdentityUser> signInWithApple() => _guard(() async {
        final apple = AppleAuthProvider()
          ..addScope('email')
          ..addScope('name');
        return kIsWeb
            ? (await _auth.signInWithPopup(apple)).user
            : (await _auth.signInWithProvider(apple)).user;
      });

  @override
  Future<IdentityUser> signInWithEmail(String email, String password) => _guard(() async {
        return (await _auth.signInWithEmailAndPassword(email: email.trim(), password: password)).user;
      });

  @override
  Future<IdentityUser> registerWithEmail(String email, String password, {String? displayName}) =>
      _guard(() async {
        final user = (await _auth.createUserWithEmailAndPassword(email: email.trim(), password: password)).user;
        if (user != null) {
          final name = displayName?.trim();
          if (name != null && name.isNotEmpty) await user.updateDisplayName(name);
          await user.sendEmailVerification();
          await user.reload();
        }
        return _auth.currentUser ?? user;
      });

  @override
  Future<void> sendPasswordReset(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
    } on FirebaseAuthException catch (e) {
      final kind = identityErrorKindFor(e.code);
      // "No such user" must look exactly like success, or the form becomes an account oracle.
      if (kind == IdentityErrorKind.wrongCredentials) return;
      throw IdentityError(kind, e.message ?? e.code);
    }
  }

  @override
  Future<void> resendVerification() async {
    final user = _auth.currentUser;
    if (user == null || user.emailVerified) return;
    try {
      await user.sendEmailVerification();
    } on FirebaseAuthException catch (e) {
      throw IdentityError(identityErrorKindFor(e.code), e.message ?? e.code);
    }
  }

  @override
  Future<IdentityUser?> reload() async {
    try {
      await _auth.currentUser?.reload();
    } on FirebaseAuthException catch (e) {
      throw IdentityError(identityErrorKindFor(e.code), e.message ?? e.code);
    }
    return current;
  }

  @override
  Future<String?> idToken({bool forceRefresh = false}) async =>
      _auth.currentUser?.getIdToken(forceRefresh);

  @override
  Future<void> signOut() async {
    try {
      if (!kIsWeb && _googleReady) await _google.signOut();
    } catch (_) {} // never signed in with Google on this device
    await _auth.signOut();
  }

  @override
  List<String> linkedProviders() =>
      _auth.currentUser?.providerData.map((p) => p.providerId).toList() ?? const [];

  @override
  Future<void> linkGoogle() => _guard(() async {
        final user = _requireUser();
        if (kIsWeb) return (await user.linkWithPopup(GoogleAuthProvider())).user;
        await initialize();
        final account = await _google.authenticate();
        final idToken = account.authentication.idToken;
        if (idToken == null) {
          throw const IdentityError(IdentityErrorKind.unavailable, 'Google returned no ID token');
        }
        return (await user.linkWithCredential(GoogleAuthProvider.credential(idToken: idToken))).user;
      });

  @override
  Future<void> linkApple() => _guard(() async {
        final user = _requireUser();
        final apple = AppleAuthProvider()..addScope('email');
        return kIsWeb ? (await user.linkWithPopup(apple)).user : (await user.linkWithProvider(apple)).user;
      });

  @override
  Future<void> linkEmailPassword(String email, String password) => _guard(() async {
        final user = _requireUser();
        final credential = EmailAuthProvider.credential(email: email.trim(), password: password);
        return (await user.linkWithCredential(credential)).user;
      });

  @override
  Future<List<SecondFactor>> secondFactors() async {
    final user = _requireUser();
    final factors = await _firebase(() => user.multiFactor.getEnrolledFactors());
    return [
      for (final f in factors)
        if (f.factorId == 'totp')
          SecondFactor(
            uid: f.uid,
            displayName: f.displayName ?? '',
            enrolledAt: DateTime.fromMillisecondsSinceEpoch((f.enrollmentTimestamp * 1000).round(), isUtc: true),
          ),
    ];
  }

  @override
  Future<TotpEnrollment> startTotpEnrollment() async {
    final user = _requireUser();
    return _firebase(() async {
      final session = await user.multiFactor.getSession();
      final secret = await TotpMultiFactorGenerator.generateSecret(session);
      final url = await secret.generateQrCodeUrl(accountName: user.email ?? user.uid, issuer: 'AURA');
      return TotpEnrollment(secretKey: secret.secretKey, otpauthUrl: url, raw: secret);
    });
  }

  @override
  Future<void> finishTotpEnrollment(TotpEnrollment enrollment, String code) async {
    final user = _requireUser();
    final secret = enrollment.raw;
    if (secret is! TotpSecret) throw const IdentityError(IdentityErrorKind.unavailable, 'not a TOTP secret');
    await _firebase(() async {
      final assertion = await TotpMultiFactorGenerator.getAssertionForEnrollment(secret, code.trim());
      await user.multiFactor.enroll(assertion, displayName: 'Authenticator app');
    });
  }

  @override
  Future<void> removeSecondFactor(String uid) async {
    final user = _requireUser();
    await _firebase(() => user.multiFactor.unenroll(factorUid: uid));
  }

  /// A Firebase call outside a sign-in, with its errors in [IdentityError] terms.
  Future<T> _firebase<T>(Future<T> Function() call) async {
    try {
      return await call();
    } on FirebaseAuthException catch (e) {
      throw IdentityError(identityErrorKindFor(e.code), e.message ?? e.code);
    } on IdentityError {
      rethrow;
    } on Exception catch (e) {
      throw IdentityError(IdentityErrorKind.unavailable, e.toString());
    }
  }

  /// The second step of a sign-in: an authenticator code when the account has one enrolled.
  SecondFactorRequired _challenge(FirebaseAuthMultiFactorException e) {
    final resolver = e.resolver;
    final hint = resolver.hints.whereType<TotpMultiFactorInfo>().firstOrNull;
    if (hint == null) {
      return SecondFactorRequired(
        totp: false,
        resolve: (_) async => throw const IdentityError(IdentityErrorKind.unavailable, 'unsupported second factor'),
      );
    }
    return SecondFactorRequired(
      totp: true,
      resolve: (code) => _guard(() async {
        final assertion = await TotpMultiFactorGenerator.getAssertionForSignIn(hint.uid, code.trim());
        return (await resolver.resolveSignIn(assertion)).user;
      }),
    );
  }

  User _requireUser() {
    final user = _auth.currentUser;
    if (user == null) throw const IdentityError(IdentityErrorKind.unavailable, 'not signed in');
    return user;
  }

  Future<IdentityUser> _guard(Future<User?> Function() signIn) async {
    try {
      final user = _toUser(await signIn());
      if (user == null) throw const IdentityError(IdentityErrorKind.unavailable, 'no user');
      return user;
    } on IdentityError {
      rethrow;
    } on SecondFactorRequired {
      rethrow;
    } on FirebaseAuthMultiFactorException catch (e) {
      // Every sign-in method meets the second step here (SaaS P1-I).
      throw _challenge(e);
    } on FirebaseAuthException catch (e) {
      throw IdentityError(identityErrorKindFor(e.code), e.message ?? e.code);
    } on GoogleSignInException catch (e) {
      final kind = e.code == GoogleSignInExceptionCode.canceled
          ? IdentityErrorKind.cancelled
          : IdentityErrorKind.unavailable;
      throw IdentityError(kind, e.description ?? e.code.name);
    } on Exception catch (e) {
      // No Firebase app configured for this platform (e.g. web before `flutterfire configure`).
      throw IdentityError(IdentityErrorKind.unavailable, e.toString());
    }
  }

  static IdentityUser? _toUser(User? user) {
    if (user == null) return null;
    final providers = user.providerData.map((p) => p.providerId).toList();
    return IdentityUser(
      uid: user.uid,
      email: user.email ?? '',
      displayName: user.displayName ?? (user.email?.split('@').first ?? ''),
      photoUrl: user.photoURL,
      emailVerified: user.emailVerified,
      provider: providers.isEmpty ? 'password' : providers.first,
    );
  }
}

final identityServiceProvider = Provider<IdentityService>((ref) => FirebaseIdentityService());
