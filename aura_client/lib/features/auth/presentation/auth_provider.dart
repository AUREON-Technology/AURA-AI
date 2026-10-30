import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:aura_client/core/auth/desktop_oauth_client.dart';
import 'package:aura_client/core/auth/platform_login_support.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/aura_secure_storage.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';

/// Reads the display-only claims (name/email/picture) out of a Google ID token's payload without
/// verifying its signature -- safe here because it's only for populating [UserProfile] in the
/// UI; the backend independently verifies the same token's signature/issuer/audience for real
/// (`services/security.py::verify_identity_token`) during `signInWithDesktopOAuth`'s exchange.
Map<String, dynamic> _unverifiedJwtPayload(String jwt) {
  final parts = jwt.split('.');
  if (parts.length != 3) return const {};
  var payload = parts[1];
  payload += '=' * ((4 - payload.length % 4) % 4);
  try {
    return jsonDecode(utf8.decode(base64Url.decode(payload))) as Map<String, dynamic>;
  } catch (_) {
    return const {};
  }
}

class UserProfile {
  final String id;
  final String email;
  final String displayName;
  final String? photoUrl;

  UserProfile({
    required this.id,
    required this.email,
    required this.displayName,
    this.photoUrl,
  });

  Map<String, String?> toMap() {
    return {
      'id': id,
      'email': email,
      'displayName': displayName,
      'photoUrl': photoUrl,
    };
  }

  factory UserProfile.fromMap(Map<String, String> map) {
    return UserProfile(
      id: map['id'] ?? '',
      email: map['email'] ?? '',
      displayName: map['displayName'] ?? '',
      photoUrl: map['photoUrl'],
    );
  }
}

class AuthNotifier extends AsyncNotifier<UserProfile?> {
  final _storage = auraSecureStorage;
  late final GoogleSignIn _googleSignIn;
  GoogleSignInAccount? _currentAccount;

  @override
  FutureOr<UserProfile?> build() async {
    // Getting the singleton reference is cheap (no plugin channel call yet) -- always assign it
    // so every other method that reads `_googleSignIn` below stays valid on every platform (a
    // `late final` left unassigned throws on first read). What's actually unsafe on Linux/Windows
    // (ADR-011 Unit 7) is initializing/using it, guarded below.
    _googleSignIn = GoogleSignIn.instance;

    // google_sign_in has no plugin implementation on Linux/Windows -- skip initializing it there
    // entirely rather than relying on the try/catch below to swallow the MissingPluginException
    // on every launch. signInCached's desktop-device-token path
    // (_restorePersistedDesktopToken) and signInWithDesktopOAuth cover login on these platforms.
    if (!supportsNativeGoogleSignIn()) {
      return null;
    }

    // Initialise Google Sign In using Client ID
    try {
      await _googleSignIn.initialize(
        clientId: kIsWeb ? '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com' : null,
        serverClientId: '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com',
      );

      // Track the current user account via stream events (new in google_sign_in 7.x)
      _googleSignIn.authenticationEvents.listen((event) {
        if (event is GoogleSignInAuthenticationEventSignIn) {
          _currentAccount = event.user;
        } else if (event is GoogleSignInAuthenticationEventSignOut) {
          _currentAccount = null;
        }
      }, onError: (Object error) {
        debugPrint("[AuthNotifier] Google authenticationEvents: $error");
      });

      // Attempt silently restoring session
      await _googleSignIn.attemptLightweightAuthentication();
    } catch (e) {
      debugPrint("[AuthNotifier] Google init or lightweight auth failed: $e");
    }

    return null; // Require biometric/google sign-in explicitly at launch
  }

  Future<bool> hasCachedCredentials() async {
    final cachedId = await _storage.read(key: 'user_id');
    return cachedId != null;
  }

  Future<void> signInCached() async {
    state = const AsyncValue.loading();
    try {
      final cachedId = await _storage.read(key: 'user_id');
      final cachedEmail = await _storage.read(key: 'user_email');
      final cachedName = await _storage.read(key: 'user_name');
      final cachedPhoto = await _storage.read(key: 'user_photo');

      if (cachedId == null || cachedEmail == null || cachedName == null) {
        state = const AsyncValue.data(null);
        return;
      }

      GoogleSignInAccount? restored;
      try {
        restored = await _googleSignIn.attemptLightweightAuthentication();
        if (restored != null) {
          _currentAccount = restored;
          final idToken = restored.authentication.idToken;
          if (idToken != null) {
            await _storage.write(key: 'auth_token', value: idToken);
          }
        }
      } catch (e) {
        debugPrint("[AuthNotifier] Silent Google restore after cached sign-in failed: $e");
      }

      if (restored == null) {
        final hasRefresh = await ref.read(tokenManagerProvider).hasCachedRefreshToken();
        final cachedGoogleToken = await _storage.read(key: 'auth_token');
        // Allow the cached UI session when a v2 refresh token exists, or a
        // leftover Google/mock token is on disk (developer bypass). Do not
        // treat that leftover Google ID token as freshly refreshed.
        if (!hasRefresh && (cachedGoogleToken == null || cachedGoogleToken.isEmpty)) {
          // Neither applies on desktop (Linux/Windows -- no google_sign_in, no refresh token on
          // a device-scoped login, ADR-011 Unit 7): fall back to a persisted device token
          // instead of treating this the same as "never logged in."
          if (!await _restorePersistedDesktopToken()) {
            debugPrint("[AuthNotifier] Cached profile found but no Google session, v2 refresh token, or desktop device token.");
            state = const AsyncValue.data(null);
            return;
          }
        }
      }

      ref.read(sessionExpiredProvider.notifier).set(false);
      state = AsyncValue.data(UserProfile(
        id: restored?.id ?? cachedId,
        email: restored?.email ?? cachedEmail,
        displayName: restored?.displayName ?? cachedName,
        photoUrl: restored?.photoUrl ?? cachedPhoto,
      ));
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  static const _mockUserId = 'mock_user_123';
  static const _desktopTokenKey = 'desktop_device_access_token';
  static const _desktopTokenExpiryKey = 'desktop_device_token_expiry';

  /// Loads a persisted desktop device token (Unit 7) into [TokenManager], if one exists and
  /// hasn't expired. Returns whether it did -- see [signInCached]'s call site.
  Future<bool> _restorePersistedDesktopToken() async {
    final accessToken = await _storage.read(key: _desktopTokenKey);
    final expiryRaw = await _storage.read(key: _desktopTokenExpiryKey);
    if (accessToken == null || accessToken.isEmpty || expiryRaw == null) return false;
    final expiry = DateTime.tryParse(expiryRaw);
    if (expiry == null || !DateTime.now().isBefore(expiry)) return false;
    ref.read(tokenManagerProvider).applyPersistedDesktopToken(accessToken: accessToken, expiry: expiry);
    return true;
  }

  /// Desktop login (Linux/Windows -- ADR-011 Unit 7): a real loopback OAuth 2.0 + PKCE exchange
  /// against Google (`core/auth/desktop_oauth_client.dart`), since `google_sign_in` has no
  /// plugin implementation on either platform. The resulting Google ID token is exchanged for a
  /// device-scoped access token via the backend's `/api/v3/auth/desktop/login`
  /// (`TokenManager.exchangeDesktopLogin`) rather than `signInWithGoogle`'s `/token` path, which
  /// needs device attestation this platform has no equivalent for.
  Future<void> signInWithDesktopOAuth() async {
    state = const AsyncValue.loading();
    try {
      final oauthResult = await signInWithGoogleLoopback(
        clientId: AppConfig.googleDesktopClientId,
        clientSecret: AppConfig.googleDesktopClientSecret,
      );
      final deviceToken = await ref.read(tokenManagerProvider).exchangeDesktopLogin(oauthResult.idToken);
      if (deviceToken == null) {
        throw StateError('The backend did not accept this Google sign-in for desktop login.');
      }

      final claims = _unverifiedJwtPayload(oauthResult.idToken);
      final profile = UserProfile(
        id: (claims['sub'] as String?) ?? '',
        email: (claims['email'] as String?) ?? '',
        displayName: (claims['name'] as String?) ?? 'Google User',
        photoUrl: claims['picture'] as String?,
      );
      await _forgetDataOfOtherAccount(profile.id);

      await _storage.write(key: 'user_id', value: profile.id);
      await _storage.write(key: 'user_email', value: profile.email);
      await _storage.write(key: 'user_name', value: profile.displayName);
      if (profile.photoUrl != null) {
        await _storage.write(key: 'user_photo', value: profile.photoUrl);
      }
      // The device token itself, not the (short-lived, single-use) Google ID token -- this is
      // what lets signInCached restore the session on relaunch without another browser round
      // trip. No refresh token exists to renew it once it expires (ADR-011); the user re-runs
      // this flow at that point, the same disclosed gap Unit 6's aura_hostd pairing has.
      await _storage.write(key: _desktopTokenKey, value: deviceToken.accessToken);
      await _storage.write(key: _desktopTokenExpiryKey, value: deviceToken.expiry.toIso8601String());

      state = AsyncValue.data(profile);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  Future<void> signInWithGoogle() async {
    state = const AsyncValue.loading();
    try {
      final googleUser = await _googleSignIn.authenticate();
      _currentAccount = googleUser;
      final idToken = googleUser.authentication.idToken;

      final profile = UserProfile(
        id: googleUser.id,
        email: googleUser.email,
        displayName: googleUser.displayName ?? 'Google User',
        photoUrl: googleUser.photoUrl,
      );
      await _forgetDataOfOtherAccount(profile.id);

      // Cache details
      await _storage.write(key: 'user_id', value: profile.id);
      await _storage.write(key: 'user_email', value: profile.email);
      await _storage.write(key: 'user_name', value: profile.displayName);
      if (profile.photoUrl != null) {
        await _storage.write(key: 'user_photo', value: profile.photoUrl);
      }
      if (idToken != null) {
        await _storage.write(key: 'auth_token', value: idToken);
      }

      state = AsyncValue.data(profile);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  // Developer bypass bypass for desktop testing without full OAuth redirect config
  Future<void> signInMock(String name, String email) async {
    state = const AsyncValue.loading();
    try {
      final profile = UserProfile(
        id: _mockUserId,
        email: email,
        displayName: name,
        photoUrl: 'https://api.dicebear.com/7.x/bottts/png?seed=$name',
      );
      await _forgetDataOfOtherAccount(profile.id);

      await _storage.write(key: 'user_id', value: profile.id);
      await _storage.write(key: 'user_email', value: profile.email);
      await _storage.write(key: 'user_name', value: profile.displayName);
      await _storage.write(key: 'user_photo', value: profile.photoUrl);
      await _storage.write(key: 'auth_token', value: 'mock_token');

      state = AsyncValue.data(profile);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  /// The backend refused to renew this session: a request was answered 401,
  /// and the refresh (or re-exchange) that followed was refused too
  /// ([TokenManager] decides that and calls this). Until this existed the
  /// user stayed "signed in" on a dead session, and every screen said "Sign in
  /// again" with nothing to press (audit F-33).
  ///
  /// Ends the session only -- signing out is the router's cue to show the
  /// login page. Unlike [signOut] it keeps this device's data and unsynced
  /// outbox: an expiry is not a choice to leave, and signing back in picks up
  /// where the user was, which is what the "Sign in again" copy promises. The
  /// cached profile id stays too, so a *different* account signing in next
  /// still gets a clean device ([_forgetDataOfOtherAccount]).
  Future<void> expireSession() async {
    final current = state.value;
    // The developer bypass never had a backend session to lose.
    if (current == null || current.id == _mockUserId) return;
    await ref.read(tokenManagerProvider).clear();
    // The credentials signInCached would otherwise revive the dead session
    // from without a real sign-in.
    await _storage.delete(key: 'auth_token');
    await _storage.delete(key: _desktopTokenKey);
    await _storage.delete(key: _desktopTokenExpiryKey);
    ref.read(sessionExpiredProvider.notifier).set(true);
    state = const AsyncValue.data(null);
  }

  /// [expireSession] keeps the local data for the account that was signed
  /// in. The local DB has no user column, so if someone else signs in next,
  /// that data must go first -- the same reason [signOut] wipes it.
  Future<void> _forgetDataOfOtherAccount(String newUserId) async {
    ref.read(sessionExpiredProvider.notifier).set(false);
    final previous = await _storage.read(key: 'user_id');
    if (previous != null && previous.isNotEmpty && previous != newUserId) {
      await ref.read(databaseProvider).wipeUserData();
    }
  }

  Future<void> signOut() async {
    state = const AsyncValue.loading();
    // A delete still inside its Undo window has not run yet; run it now so
    // it reaches the final push below instead of being wiped unsent.
    try {
      await ref.read(undoableDeletesProvider.notifier).commitAll();
    } catch (e) {
      debugPrint("[Auth] pending deletes before sign-out failed (continuing): $e");
    }
    try {
      await _googleSignIn.signOut();
    } catch (_) {} // Might fail if never logged into google
    _currentAccount = null;

    // Last chance to upload offline edits before they are wiped below.
    // Best effort and bounded: signing out must work offline too, and an
    // edit that cannot be pushed now is lost either way once the local
    // data goes.
    try {
      await ref.read(syncClientProvider).push().timeout(const Duration(seconds: 20));
    } catch (e) {
      debugPrint("[Auth] final push before sign-out failed (continuing): $e");
    }

    // Revokes the refresh token server-side, then clears TokenManager's
    // in-memory access/refresh token cache — without the latter, a
    // still-valid (up to 15min) cached v2 access token from the outgoing
    // session could authenticate v2 API calls as the wrong user if a
    // different account signs in before the app restarts.
    await ref.read(tokenManagerProvider).clear(revoke: true);

    // The local DB is not keyed by user, so the next account to sign in
    // would otherwise inherit this one's data, unsynced outbox and cursor.
    await ref.read(databaseProvider).wipeUserData();

    await _storage.deleteAll();
    // Shared storage also drops the DB encryption-key copy. Tokens must be
    // re-issued on login; EncryptionService restores the Drift key from
    // `.aura_db_key` on the next init() so encrypted rows stay readable.
    state = const AsyncValue.data(null);
  }

  Future<String?> getIdToken() async {
    try {
      // google_sign_in 7.x: authentication.idToken is a snapshot from the last
      // authenticate / lightweight restore, not a live refresh. Re-run silent
      // restore whenever a Google account is (or might be) present.
      try {
        final restored = await _googleSignIn.attemptLightweightAuthentication();
        if (restored != null) {
          _currentAccount = restored;
        }
      } catch (e) {
        debugPrint("[AuthNotifier] Silent Google ID token refresh failed: $e");
      }

      final currentAccount = _currentAccount;
      if (currentAccount != null) {
        final idToken = currentAccount.authentication.idToken;
        if (idToken != null) {
          await _storage.write(key: 'auth_token', value: idToken);
          return idToken;
        }
      }

      // Fallback: cached Google ID token or mock token. Not claimed as fresh.
      final cached = await _storage.read(key: 'auth_token');
      return cached;
    } catch (e) {
      debugPrint("[AuthNotifier] Error refreshing or reading ID Token: $e");
      try {
        return await _storage.read(key: 'auth_token');
      } catch (_) {}
    }
    return null;
  }
}

final authProvider = AsyncNotifierProvider<AuthNotifier, UserProfile?>(() {
  return AuthNotifier();
});

/// Set when the session ended because the backend refused to renew it
/// ([AuthNotifier.expireSession]), so the login page can say why the user is
/// looking at it. Cleared by the next sign-in.
class SessionExpiredNotifier extends Notifier<bool> {
  @override
  bool build() => false;

  void set(bool value) => state = value;
}

final sessionExpiredProvider =
    NotifierProvider<SessionExpiredNotifier, bool>(SessionExpiredNotifier.new);
