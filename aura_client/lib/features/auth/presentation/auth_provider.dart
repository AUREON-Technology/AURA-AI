import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/engineering/data/agent_task_outbox.dart';
import 'package:aura_client/core/auth/account_client.dart';
import 'package:aura_client/core/auth/desktop_oauth_client.dart';
import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/auth/platform_login_support.dart';
import 'package:aura_client/core/auth/sign_in_refused.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/app_links.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/aura_secure_storage.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';

/// Reads the display-only claims (name/email/picture) out of a Google ID token's (or, after the
/// browser hand-off, the AURA access token's) payload without
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

/// The browser hand-off [AuthNotifier.signInWithDesktopBrowser] runs (`signInWithBrowserHandoff`),
/// behind a provider so a test can play the browser.
typedef DesktopBrowserHandoff = Future<DesktopHandoffResult> Function({
  required Uri signInPage,
  required Uri exchangeEndpoint,
  String? deviceName,
});

final desktopBrowserHandoffProvider =
    Provider<DesktopBrowserHandoff>((ref) => signInWithBrowserHandoff);

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

  /// Google, Apple and email/password all sign in through Firebase (SaaS P1-B). Linux and
  /// Windows have no Firebase or google_sign_in plugin and keep the loopback desktop flow below.
  IdentityService get _identity => ref.read(identityServiceProvider);

  @override
  FutureOr<UserProfile?> build() async {
    if (!supportsNativeGoogleSignIn()) {
      return null;
    }
    try {
      await _identity.initialize();
    } catch (e) {
      debugPrint("[AuthNotifier] identity provider initialisation failed: $e");
    }
    return null; // Require biometric/explicit sign-in at launch
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

      IdentityUser? restored;
      if (supportsNativeGoogleSignIn()) {
        try {
          restored = await _identity.restore();
          if (restored != null) {
            final idToken = await _identity.idToken();
            if (idToken != null) {
              await _storage.write(key: 'auth_token', value: idToken);
            }
          }
        } catch (e) {
          debugPrint("[AuthNotifier] Silent identity restore after cached sign-in failed: $e");
        }
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

      ref.read(sessionEndedForPlanProvider.notifier).set(false);
      ref.read(sessionExpiredProvider.notifier).set(false);
      state = AsyncValue.data(UserProfile(
        id: restored?.uid ?? cachedId,
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
      // The code exchange runs on the backend (it holds the OAuth client secret Google demands
      // and this binary must not carry): same base URL every other v3 call uses.
      final backendUrl = ref.read(backendUrlProvider);
      final oauthResult = await signInWithGoogleLoopback(
        clientId: AppConfig.googleDesktopClientId,
        clientSecret: AppConfig.googleDesktopClientSecret,
        backendExchangeEndpoint: Uri.parse('$backendUrl/api/v3/auth/desktop/exchange'),
      );
      final tokens = ref.read(tokenManagerProvider);
      final deviceToken = await tokens.exchangeDesktopLogin(
        oauthResult.idToken,
        deviceName: desktopDeviceName(),
      );
      if (deviceToken == null) {
        // A refusal (desktop sign-in is for operators only, F-09) gets its own words and the
        // server's reason; anything else did not get an answer at all.
        final refusal = tokens.lastDesktopLoginRefusal;
        if (refusal != null) throw SignInRefused(refusal, tokens.lastDesktopLoginPlanRequired);
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
      // The session now carries a device-bound refresh token the TokenManager persists itself
      // (FSA-004), so signInCached's ordinary `hasCachedRefreshToken` path restores it on
      // relaunch. The 30-day access token is no longer written; the two keys stay readable
      // (`_restorePersistedDesktopToken`) only so a token issued before this change keeps
      // working until it expires, and `expireSession` still deletes them.
      debugPrint("[AuthNotifier] desktop session until ${deviceToken.expiry.toIso8601String()}");

      state = AsyncValue.data(profile);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  /// Desktop login (Linux/Windows) with any Firebase method -- Google, Apple or email -- through
  /// the browser (module readiness WP12). The web app's `/desktop-sign-in` page signs in with
  /// Firebase and hands a one-time code back to this app's loopback listener; the code and the
  /// PKCE verifier are redeemed at `/api/v3/auth/desktop/firebase-exchange` for the same
  /// device-bound session `/desktop/login` issues (`signInWithBrowserHandoff`). Same state and
  /// storage handling as [signInWithDesktopOAuth], which stays as it is.
  Future<void> signInWithDesktopBrowser() async {
    state = const AsyncValue.loading();
    try {
      final signInPage = desktopSignInPageFor(ref.read(appBaseUrlProvider));
      if (signInPage.isEmpty) {
        // The login page only offers this when the build knows where the web app is.
        throw StateError('No APP_BASE_URL in this build: browser sign-in is not set up.');
      }
      final backendUrl = ref.read(backendUrlProvider);
      final DesktopHandoffResult handoff;
      try {
        handoff = await ref.read(desktopBrowserHandoffProvider)(
          signInPage: Uri.parse(signInPage),
          exchangeEndpoint: Uri.parse('$backendUrl/api/v3/auth/desktop/firebase-exchange'),
          deviceName: desktopDeviceName(),
        );
      } on DesktopOAuthCancelledException catch (e) {
        // The server said no to this account (operators only, or a plan without the desktop
        // Engineer): the same refusal [signInWithDesktopOAuth] reports, with the server's reason.
        if (e.statusCode == 403) throw SignInRefused(e.message, e.refusal == 'plan_required');
        rethrow;
      }
      final deviceToken =
          await ref.read(tokenManagerProvider).applyDesktopSession(handoff.tokenResponse);

      // The AURA access token's own claims (`services/jwt_auth.create_access_token`), read for
      // display only: the server issued it a moment ago and verifies it on every request.
      final claims = _unverifiedJwtPayload(handoff.accessToken);
      final email = (claims['email'] as String?) ?? '';
      final name = (claims['name'] as String?)?.trim();
      final profile = UserProfile(
        id: (claims['sub'] as String?) ?? '',
        email: email,
        displayName: (name != null && name.isNotEmpty)
            ? name
            : (email.contains('@') ? email.split('@').first : 'AURA user'),
        photoUrl: claims['picture'] as String?,
      );
      await _forgetDataOfOtherAccount(profile.id);

      await _storage.write(key: 'user_id', value: profile.id);
      await _storage.write(key: 'user_email', value: profile.email);
      await _storage.write(key: 'user_name', value: profile.displayName);
      if (profile.photoUrl != null) {
        await _storage.write(key: 'user_photo', value: profile.photoUrl);
      }
      debugPrint("[AuthNotifier] desktop browser session until ${deviceToken.expiry.toIso8601String()}");

      state = AsyncValue.data(profile);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  Future<void> signInWithGoogle() => _signInWith(_identity.signInWithGoogle);

  Future<void> signInWithApple() => _signInWith(_identity.signInWithApple);

  Future<void> signInWithEmail(String email, String password) =>
      _signInWith(() => _identity.signInWithEmail(email, password));

  /// Creates an email/password account (Firebase sends the verification email) and signs in.
  /// Teams stay closed to it until the address is verified (the backend checks).
  Future<void> registerWithEmail(String email, String password, {String? displayName}) =>
      _signInWith(() => _identity.registerWithEmail(email, password, displayName: displayName));

  Future<void> sendPasswordReset(String email) => _identity.sendPasswordReset(email);

  Future<void> resendVerification() => _identity.resendVerification();

  /// After the person clicked the verification link: re-read them from the identity provider
  /// and, if verified now, start a new backend session with a force-refreshed ID token. The
  /// refresh token would otherwise keep re-issuing "unverified" for the life of the session,
  /// since `/refresh` reuses the identity it was issued for (review B#1). Returns whether the
  /// address is verified.
  Future<bool> confirmEmailVerified() async {
    final user = await _identity.reload();
    if (user == null || !user.emailVerified) return false;
    final idToken = await _identity.idToken(forceRefresh: true);
    if (idToken == null) return false;
    await ref.read(tokenManagerProvider).startSession(idToken);
    await _storage.write(key: 'auth_token', value: idToken);
    return true;
  }

  /// The second sign-in step answered (SaaS P1-I): the resolved user goes through the same
  /// completion as every sign-in, which starts the backend session.
  Future<void> completeSecondFactor(IdentityUser user) => _signInWith(() async => user);

  /// The second sign-in step abandoned: nobody is signed in, and no session was started.
  Future<void> cancelSecondFactor() async {
    await _abandonIdentity();
    state = const AsyncValue.data(null);
  }

  /// Every sign-in method ends here: exchange the provider's ID token for a backend session,
  /// forget another account's local data, cache the profile.
  Future<void> _signInWith(Future<IdentityUser> Function() signIn) async {
    state = const AsyncValue.loading();
    try {
      final user = await signIn();
      final idToken = await _identity.idToken();
      if (idToken == null) {
        await _abandonIdentity();
        throw const SignInRefused('The sign-in provider returned no identity token for this account.');
      }

      // Start the backend session now, not on the first API call, so a refusal is shown here
      // rather than leaving the user on a session nothing can renew. Only a refusal stops the
      // sign-in: offline, or a device that failed attestation (main.dart's banner, C1), still
      // gets in and keeps everything that works without the backend.
      final outcome = await ref.read(tokenManagerProvider).startSession(idToken);
      if (outcome == SessionStart.refused) {
        await _abandonIdentity();
        throw const SignInRefused();
      }

      final profile = UserProfile(
        id: user.uid,
        email: user.email,
        displayName: user.displayName.isEmpty ? 'AURA user' : user.displayName,
        photoUrl: user.photoUrl,
      );
      await _forgetDataOfOtherAccount(profile.id);

      await _storage.write(key: 'user_id', value: profile.id);
      await _storage.write(key: 'user_email', value: profile.email);
      await _storage.write(key: 'user_name', value: profile.displayName);
      if (profile.photoUrl != null) {
        await _storage.write(key: 'user_photo', value: profile.photoUrl);
      }
      await _storage.write(key: 'auth_token', value: idToken);

      state = AsyncValue.data(profile);
    } on IdentityError catch (err, stack) {
      // Closing the provider's sheet is a choice, not a failure: nothing to show.
      state = err.kind == IdentityErrorKind.cancelled
          ? const AsyncValue.data(null)
          : AsyncValue.error(err, stack);
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  /// Signs out of the identity provider after a sign-in that will not go ahead, so the next
  /// attempt offers the account picker again instead of silently reusing the refused account.
  Future<void> _abandonIdentity() async {
    try {
      await _identity.signOut();
    } catch (_) {}
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
  ///
  /// [planRequired]: the server ended it because the account's plan no longer includes the
  /// desktop Engineer (a downgrade, WP11); the login page then says so instead of "expired".
  Future<void> expireSession({bool planRequired = false}) async {
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
    ref.read(sessionEndedForPlanProvider.notifier).set(planRequired);
    state = const AsyncValue.data(null);
  }

  /// The backend said this account was deleted (`account_deleted`) -- from another device, or by
  /// an operator. Unlike [expireSession], the local data goes too: it belongs to an account that
  /// no longer exists, and keeping it (with its unsynced outbox) would push it into whatever
  /// account signs in next under the same Google identity (audit INT-010). Google is signed out
  /// as well, so nothing can silently re-exchange its way back in; only a deliberate sign-in
  /// starts a fresh account. [TokenManager] calls this instead of falling back to `/token`.
  Future<void> endDeletedAccount() async {
    final current = state.value;
    if (current != null && current.id == _mockUserId) return;
    try {
      ref.read(undoableDeletesProvider.notifier).discardAll();
      // Out of Google, Apple or the email account too (SaaS P1-B), so nothing can silently
      // re-exchange its way back in.
      await _abandonIdentity();
      // The server already revoked every refresh token of the account.
      await _clearThisDevice(revoke: false);
    } catch (e) {
      debugPrint("[Auth] local cleanup after a remote account deletion failed: $e");
      await ref.read(tokenManagerProvider).clear();
    } finally {
      ref.read(sessionExpiredProvider.notifier).set(true);
      state = const AsyncValue.data(null);
    }
  }

  /// [expireSession] keeps the local data for the account that was signed
  /// in. The local DB has no user column, so if someone else signs in next,
  /// that data must go first -- the same reason [signOut] wipes it.
  Future<void> _forgetDataOfOtherAccount(String newUserId) async {
    ref.read(sessionExpiredProvider.notifier).set(false);
    ref.read(sessionEndedForPlanProvider.notifier).set(false);
    final previous = await _storage.read(key: 'user_id');
    if (previous != null && previous.isNotEmpty && previous != newUserId) {
      await ref.read(databaseProvider).wipeUserData();
      await ref.read(activeWorkspaceProvider.notifier).clear();
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
    await _abandonIdentity(); // also fine when this device never used the identity provider

    // Last chance to upload offline edits before they are wiped below.
    // Best effort and bounded: signing out must work offline too, and an
    // edit that cannot be pushed now is lost either way once the local
    // data goes.
    try {
      await ref.read(syncClientProvider).push().timeout(const Duration(seconds: 20));
    } catch (e) {
      debugPrint("[Auth] final push before sign-out failed (continuing): $e");
    }

    await _clearThisDevice(revoke: true);
  }

  /// Deletes the account on the server -- memories, documents, synced
  /// records, conversations, every session and device -- then clears this
  /// device the way [signOut] does. Irreversible; the caller confirms first.
  ///
  /// If the server call fails (offline, 5xx) this throws and nothing local
  /// changes, so the user is still signed in and can try again. Once the
  /// server has answered, this device signs out whatever the answer: the
  /// server revokes the sessions even when it reports a store it could not
  /// erase ([AccountDeletionResult.forgotten] false), and retrying then means
  /// signing in again, which the result's copy says.
  ///
  /// Unlike [signOut] there is no final push: the account is gone, and an
  /// upload made with the still-unexpired access token would write rows back
  /// for it. Deletes waiting out their Undo window are dropped for the same
  /// reason.
  Future<AccountDeletionResult> deleteAccount() async {
    final current = state.value;
    if (current == null) throw StateError('not signed in');
    if (current.id == _mockUserId) {
      // The developer bypass has no server account to delete.
      await signOut();
      return const AccountDeletionResult(forgotten: true);
    }
    final result = await ref.read(accountClientProvider).deleteAccount();
    // From here the account is gone on the server: whatever fails locally, this ends signed
    // out, and the result is still returned, so the screen does not say "not deleted".
    state = const AsyncValue.loading();
    try {
      ref.read(undoableDeletesProvider.notifier).discardAll();
      await _abandonIdentity();
      // The server already revoked every refresh token of the account.
      await _clearThisDevice(revoke: false);
    } catch (e) {
      debugPrint("[Auth] local cleanup after account deletion failed: $e");
    } finally {
      state = const AsyncValue.data(null);
    }
    return result;
  }

  Future<void> _clearThisDevice({required bool revoke}) async {
    // Revokes the refresh token server-side (when [revoke]), then clears
    // TokenManager's in-memory access/refresh token cache — without the
    // latter, a still-valid (up to 15min) cached v2 access token from the
    // outgoing session could authenticate v2 API calls as the wrong user if
    // a different account signs in before the app restarts.
    await ref.read(tokenManagerProvider).clear(revoke: revoke);

    // The local DB is not keyed by user, so the next account to sign in
    // would otherwise inherit this one's data, unsynced outbox and cursor.
    await ref.read(databaseProvider).wipeUserData();

    // The next account must not start in this one's team (F2 review #1).
    await ref.read(activeWorkspaceProvider.notifier).clear();

    // Nor may it replay this one's queued agent tasks (they are saved locally, not in the DB).
    await ref.read(agentTaskOutboxProvider.notifier).clear();

    await _storage.deleteAll();
    // Shared storage also drops the DB encryption-key copy. Tokens must be
    // re-issued on login; EncryptionService restores the Drift key from
    // `.aura_db_key` on the next init() so encrypted rows stay readable.
    state = const AsyncValue.data(null);
  }

  Future<String?> getIdToken() async {
    try {
      // Firebase refreshes its ID token itself; this is always a live one when a user is signed in.
      if (supportsNativeGoogleSignIn()) {
        final idToken = await _identity.idToken();
        if (idToken != null) {
          await _storage.write(key: 'auth_token', value: idToken);
          return idToken;
        }
      }
      // Fallback: cached token or mock token. Not claimed as fresh.
      return await _storage.read(key: 'auth_token');
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

/// Whether the session that [sessionExpiredProvider] reports ended because the account's plan no
/// longer includes the desktop Engineer (`X-Aura-Refusal: plan_required` on `/refresh`, WP11).
/// Cleared with it by the next sign-in.
final sessionEndedForPlanProvider =
    NotifierProvider<SessionExpiredNotifier, bool>(SessionExpiredNotifier.new);
