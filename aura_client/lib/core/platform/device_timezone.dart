import 'package:flutter/foundation.dart';
import 'package:flutter_timezone/flutter_timezone.dart';

/// The header the backend reads the device's IANA zone from (`services/user_time.py`). It
/// stores it on the user, so the scheduled insights and daily briefing compute "today" in the
/// user's own day rather than UTC's (audit F-17).
const clientTimezoneHeader = 'X-Client-Timezone';

/// Looks up the device's IANA zone name, or null when the platform cannot say.
typedef DeviceTimezoneLookup = Future<String?> Function();

/// The device's IANA zone ("Asia/Riyadh"), or null. Null is safe to send as "no header": the
/// backend then keeps whatever zone it already has, or UTC.
Future<String?> deviceTimezone() async {
  try {
    final zone = (await FlutterTimezone.getLocalTimezone()).identifier.trim();
    return zone.isEmpty ? null : zone;
  } catch (e) {
    // MissingPluginException in tests and on any platform without the plugin registered.
    debugPrint('[deviceTimezone] unavailable: $e');
    return null;
  }
}
