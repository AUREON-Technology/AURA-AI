// Firebase configuration for the web build.
//
// Android reads google-services.json and iOS/macOS their plist; a browser has neither, so its
// Firebase app is named at build time:
//
//   --dart-define=FIREBASE_WEB_API_KEY=...        --dart-define=FIREBASE_WEB_APP_ID=...
//   --dart-define=FIREBASE_WEB_PROJECT_ID=...     --dart-define=FIREBASE_WEB_AUTH_DOMAIN=...   (optional)
//   --dart-define=FIREBASE_WEB_MESSAGING_SENDER_ID=...                                         (optional)
//
// These are public identifiers (the web SDK sends them from every visitor's browser), not
// secrets; access is governed by Firebase's authorized domains and the backend's own token
// checks. They are kept out of the repository only so a fork or a staging project can supply its
// own. The deploy workflow reads them from repository variables.
//
// With none set, [firebaseWebOptions] is null: the app boots, and web sign-in reports that it is
// unavailable (docs/deployment/customer-sign-in.md §4).

import 'package:firebase_core/firebase_core.dart';

const _apiKey = String.fromEnvironment('FIREBASE_WEB_API_KEY');
const _appId = String.fromEnvironment('FIREBASE_WEB_APP_ID');
const _projectId = String.fromEnvironment('FIREBASE_WEB_PROJECT_ID');
const _authDomain = String.fromEnvironment('FIREBASE_WEB_AUTH_DOMAIN');
const _messagingSenderId = String.fromEnvironment('FIREBASE_WEB_MESSAGING_SENDER_ID');

/// The options the web build passes to `Firebase.initializeApp`, or null when the build was not
/// given the three values Firebase cannot work without (API key, app id, project id).
FirebaseOptions? firebaseWebOptions() => firebaseWebOptionsFrom(
      apiKey: _apiKey,
      appId: _appId,
      projectId: _projectId,
      authDomain: _authDomain,
      messagingSenderId: _messagingSenderId,
    );

/// [firebaseWebOptions] with its inputs as arguments, so a test can supply them (a compile-time
/// `String.fromEnvironment` cannot change inside one test run).
FirebaseOptions? firebaseWebOptionsFrom({
  required String apiKey,
  required String appId,
  required String projectId,
  String authDomain = '',
  String messagingSenderId = '',
}) {
  if (apiKey.isEmpty || appId.isEmpty || projectId.isEmpty) return null;
  return FirebaseOptions(
    apiKey: apiKey,
    appId: appId,
    projectId: projectId,
    // The popup sign-in loads its handler from the auth domain. Firebase's default for a project
    // is `<project>.firebaseapp.com`.
    authDomain: authDomain.isEmpty ? '$projectId.firebaseapp.com' : authDomain,
    // The Firebase JS SDK requires a sender id to be present, not to be meaningful; the project
    // number is the first part of every app id (`1:<number>:web:<hash>`).
    messagingSenderId: messagingSenderId.isEmpty ? _projectNumberOf(appId) : messagingSenderId,
  );
}

String _projectNumberOf(String appId) {
  final parts = appId.split(':');
  return parts.length >= 2 ? parts[1] : '';
}
