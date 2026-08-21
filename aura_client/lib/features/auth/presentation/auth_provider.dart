import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/security/aura_secure_storage.dart';

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
    // Initialise Google Sign In using Client ID
    _googleSignIn = GoogleSignIn.instance;
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
      // Ignore if initialization fails (e.g. running on unsupported platform)
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
          debugPrint("[AuthNotifier] Cached profile found but no Google session or v2 refresh token.");
          state = const AsyncValue.data(null);
          return;
        }
      }

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
        id: 'mock_user_123',
        email: email,
        displayName: name,
        photoUrl: 'https://api.dicebear.com/7.x/bottts/png?seed=$name',
      );

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

  Future<void> signOut() async {
    state = const AsyncValue.loading();
    try {
      await _googleSignIn.signOut();
    } catch (_) {} // Might fail if never logged into google
    _currentAccount = null;
    await _storage.deleteAll();
    // Shared storage also drops the DB encryption-key copy. Tokens must be
    // re-issued on login; EncryptionService restores the Drift key from
    // `.aura_db_key` on the next init() so encrypted rows stay readable.

    // Clears TokenManager's in-memory access/refresh token cache — without
    // this, a still-valid (up to 15min) cached v2 access token from the
    // outgoing session could authenticate v2 API calls as the wrong user
    // if a different account signs in before the app restarts.
    await ref.read(tokenManagerProvider).clear();
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
