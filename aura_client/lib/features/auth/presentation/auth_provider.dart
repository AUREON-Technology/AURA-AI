import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:google_sign_in/google_sign_in.dart';

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
  final _storage = const FlutterSecureStorage();
  late final GoogleSignIn _googleSignIn;

  @override
  FutureOr<UserProfile?> build() async {
    // Initialise Google Sign In using Client ID
    _googleSignIn = GoogleSignIn.instance;
    try {
      await _googleSignIn.initialize(
        clientId: kIsWeb ? '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com' : null,
        serverClientId: '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com',
      );
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

      if (cachedId != null && cachedEmail != null && cachedName != null) {
        state = AsyncValue.data(UserProfile(
          id: cachedId,
          email: cachedEmail,
          displayName: cachedName,
          photoUrl: cachedPhoto,
        ));
      } else {
        state = const AsyncValue.data(null);
      }
    } catch (err, stack) {
      state = AsyncValue.error(err, stack);
    }
  }

  Future<void> signInWithGoogle() async {
    state = const AsyncValue.loading();
    try {
      final googleUser = await _googleSignIn.authenticate();
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
    
    await _storage.deleteAll();
    state = const AsyncValue.data(null);
  }
}

final authProvider = AsyncNotifierProvider<AuthNotifier, UserProfile?>(() {
  return AuthNotifier();
});
