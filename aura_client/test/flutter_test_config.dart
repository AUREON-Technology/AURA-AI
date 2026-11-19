// Runs before every test file (Flutter's test convention).
//
// The network layer reads the saved active workspace from shared_preferences before choosing a
// request's scope (SaaS P1-F2), and in a widget test an unmocked SharedPreferences never answers
// inside the fake clock -- every request would wait forever. Give each file the empty store a
// real device starts with; a test that needs saved values still sets its own.

import 'dart:async';

import 'package:shared_preferences/shared_preferences.dart';

Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  SharedPreferences.setMockInitialValues({});
  await testMain();
}
