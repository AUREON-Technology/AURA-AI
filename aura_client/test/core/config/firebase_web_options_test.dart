import 'package:aura_client/core/config/firebase_web_options.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('firebaseWebOptionsFrom', () {
    test('is null until the API key, app id and project id are all given', () {
      expect(firebaseWebOptionsFrom(apiKey: '', appId: '', projectId: ''), isNull);
      expect(firebaseWebOptionsFrom(apiKey: 'k', appId: '', projectId: 'p'), isNull);
      expect(firebaseWebOptionsFrom(apiKey: 'k', appId: '1:2:web:3', projectId: ''), isNull);
      expect(firebaseWebOptionsFrom(apiKey: '', appId: '1:2:web:3', projectId: 'p'), isNull);
    });

    test('a build given none starts without Firebase (the test run has no defines)', () {
      expect(firebaseWebOptions(), isNull);
    });

    test('defaults the auth domain and sender id from the project and app id', () {
      final o = firebaseWebOptionsFrom(apiKey: 'k', appId: '1:87789110323:web:abc', projectId: 'demo')!;
      expect(o.authDomain, 'demo.firebaseapp.com');
      expect(o.messagingSenderId, '87789110323');
      expect(o.apiKey, 'k');
      expect(o.projectId, 'demo');
    });

    test('explicit values win', () {
      final o = firebaseWebOptionsFrom(
        apiKey: 'k',
        appId: '1:2:web:3',
        projectId: 'p',
        authDomain: 'login.example.test',
        messagingSenderId: '99',
      )!;
      expect(o.authDomain, 'login.example.test');
      expect(o.messagingSenderId, '99');
    });
  });
}
