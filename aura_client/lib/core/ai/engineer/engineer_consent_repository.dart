import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../network/aura_api_client.dart';
import '../../network/aura_api_error.dart';

/// The persona id whose turns need Anthropic consent (D-02, D-03).
const kEngineerAgentId = 'engineer';

/// The version of the Engineer consent notice this app shows (`engineerConsent*` in
/// `lib/l10n/app_en.arb`). The text on screen IS this version: when the server reports a
/// different `current_version`, the app says "update the app" instead of showing old words and
/// recording consent to a notice the user never read. Change it only together with the notice
/// strings and the backend's `CURRENT_NOTICE_VERSION`.
const kEngineerConsentNoticeVersion = '2026-11-17';

/// What `GET|POST|DELETE /api/v3/engineering/consent` answers.
class EngineerConsent {
  const EngineerConsent({
    required this.accepted,
    required this.currentVersion,
    this.noticeVersion,
    this.acceptedAt,
  });

  factory EngineerConsent.fromJson(Map<String, dynamic> json) {
    final acceptedAt = json['accepted_at'];
    return EngineerConsent(
      accepted: json['accepted'] == true,
      noticeVersion: json['notice_version'] as String?,
      currentVersion: json['current_version'] as String,
      acceptedAt: acceptedAt is String ? DateTime.parse(acceptedAt) : null,
    );
  }

  final bool accepted;

  /// The version the user accepted; null when they never did or withdrew.
  final String? noticeVersion;

  /// The version the server requires now.
  final String currentVersion;
  final DateTime? acceptedAt;

  /// Whether the server will serve an Engineer turn: accepted, and to the version it requires.
  /// An acceptance of an older notice does not count -- the server refuses it too.
  bool get isCurrent => accepted && noticeVersion != null && noticeVersion == currentVersion;

  /// Whether this app carries the notice the server requires. When false, the app must not
  /// show its own notice as if it were the current one.
  bool get appNoticeIsCurrent => currentVersion == kEngineerConsentNoticeVersion;
}

/// The server answered 409 `consent_version_mismatch`: the notice this app shows is not the
/// one the server requires, so the user has to update the app before agreeing.
class EngineerConsentVersionMismatch implements Exception {
  const EngineerConsentVersionMismatch({this.currentVersion, this.message});

  final String? currentVersion;
  final String? message;

  @override
  String toString() => message ?? 'consent_version_mismatch';
}

/// Client for `/api/v3/engineering/consent` (WP1, SEC-02).
///
/// Through [AuraApiClient], like [EngineerWorkspacesClient]: JWT, device attestation, one retry
/// on 401, a deadline, and an idempotency key on the POST and DELETE.
class EngineerConsentRepository {
  EngineerConsentRepository(Ref ref, {http.Client? httpClient})
      : _api = httpClient == null
            ? ref.read(auraApiClientProvider)
            : AuraApiClient(ref, httpClient: httpClient);

  final AuraApiClient _api;

  static const path = '/api/v3/engineering/consent';

  /// Throws [AuraApiError] when the request fails and [FormatException] on a body that is not
  /// the documented shape.
  Future<EngineerConsent> fetch() async => _parse(await _api.json(method: 'GET', path: path));

  /// Records consent to [noticeVersion] -- always the notice this app shows, unless a test says
  /// otherwise. Throws [EngineerConsentVersionMismatch] on the server's 409.
  Future<EngineerConsent> accept({String noticeVersion = kEngineerConsentNoticeVersion}) async {
    try {
      return _parse(await _api.json(
        method: 'POST',
        path: path,
        body: {'notice_version': noticeVersion},
      ));
    } on AuraApiError catch (e) {
      if (e.statusCode == 409 || e.error == 'consent_version_mismatch') {
        final current = e.details['current_version'];
        throw EngineerConsentVersionMismatch(
          currentVersion: current is String ? current : null,
          message: e.message,
        );
      }
      rethrow;
    }
  }

  /// Withdraws consent. Idempotent on the server: withdrawing twice is not an error.
  Future<EngineerConsent> withdraw() async =>
      _parse(await _api.json(method: 'DELETE', path: path));

  EngineerConsent _parse(Map<String, dynamic> body) {
    try {
      return EngineerConsent.fromJson(body);
    } catch (e) {
      throw FormatException('Unexpected consent response: $e');
    }
  }
}

final engineerConsentRepositoryProvider = Provider<EngineerConsentRepository>((ref) {
  return EngineerConsentRepository(ref);
});

/// The caller's Engineer consent, as the server last reported it.
///
/// Fetched once and kept: [accept] and [withdraw] replace it with the server's answer, and a
/// `consent_required` refusal invalidates it (consent withdrawn on another device). Never
/// retried automatically -- a failed fetch is shown, and the server still enforces consent on
/// every turn whatever this says.
class EngineerConsentNotifier extends AsyncNotifier<EngineerConsent> {
  @override
  Future<EngineerConsent> build() => ref.read(engineerConsentRepositoryProvider).fetch();

  Future<EngineerConsent> accept() async {
    final result = await ref.read(engineerConsentRepositoryProvider).accept();
    state = AsyncData(result);
    return result;
  }

  Future<EngineerConsent> withdraw() async {
    final result = await ref.read(engineerConsentRepositoryProvider).withdraw();
    state = AsyncData(result);
    return result;
  }
}

final engineerConsentProvider =
    AsyncNotifierProvider<EngineerConsentNotifier, EngineerConsent>(
  EngineerConsentNotifier.new,
  retry: (_, _) => null,
);

/// A turn the server refused with `consent_required` although the app thought consent was
/// current (withdrawn on another device, or a newer notice). The chat surface that is showing
/// shows the consent sheet for it, and resends the turn if the user agrees.
class EngineerConsentPrompt {
  const EngineerConsentPrompt({
    required this.conversationId,
    required this.messageId,
    this.currentVersion,
  });

  final String conversationId;

  /// The assistant row that holds the refusal notice; resending replaces it.
  final String messageId;

  /// `details.current_version` from the refusal, when the server sent it.
  final String? currentVersion;
}

/// The pending [EngineerConsentPrompt], if any. One surface handles it: whoever [take]s it first.
class EngineerConsentPrompts extends Notifier<EngineerConsentPrompt?> {
  @override
  EngineerConsentPrompt? build() => null;

  void request(EngineerConsentPrompt prompt) => state = prompt;

  EngineerConsentPrompt? take() {
    final prompt = state;
    if (prompt != null) state = null;
    return prompt;
  }
}

final engineerConsentPromptProvider =
    NotifierProvider<EngineerConsentPrompts, EngineerConsentPrompt?>(EngineerConsentPrompts.new);
