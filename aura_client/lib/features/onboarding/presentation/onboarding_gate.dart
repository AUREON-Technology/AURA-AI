import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/security/aura_secure_storage.dart';

const kOnboardingDoneKey = 'aura_onboarding_done';

/// `null` while the flag is loading, then true/false.
class OnboardingGate extends Notifier<bool?> {
  @override
  bool? build() {
    _load();
    return null;
  }

  Future<void> _load() async {
    final value = await auraSecureStorage.read(key: kOnboardingDoneKey);
    state = value == '1';
  }

  Future<void> complete() async {
    await auraSecureStorage.write(key: kOnboardingDoneKey, value: '1');
    state = true;
  }
}

final onboardingGateProvider =
    NotifierProvider<OnboardingGate, bool?>(OnboardingGate.new);
