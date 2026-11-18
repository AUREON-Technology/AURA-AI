// The owner's legal pages (spec §5.5): terms of service, privacy policy, refund policy.
// Required by Fawry, the app stores and Egypt's Law 151/2020; the owner supplies the text.
// Set per build with `--dart-define=TERMS_URL=… PRIVACY_URL=… REFUND_URL=…`. A link that is not
// set is not shown -- never a dead link.

import 'package:flutter/foundation.dart';

@immutable
class LegalLinks {
  const LegalLinks({this.terms, this.privacy, this.refund});

  factory LegalLinks.fromEnvironment() => LegalLinks(
        terms: _uri(const String.fromEnvironment('TERMS_URL')),
        privacy: _uri(const String.fromEnvironment('PRIVACY_URL')),
        refund: _uri(const String.fromEnvironment('REFUND_URL')),
      );

  final Uri? terms;
  final Uri? privacy;
  final Uri? refund;

  bool get isEmpty => terms == null && privacy == null && refund == null;

  static Uri? _uri(String raw) {
    final uri = Uri.tryParse(raw.trim());
    return uri != null && uri.scheme == 'https' && uri.host.isNotEmpty ? uri : null;
  }
}
