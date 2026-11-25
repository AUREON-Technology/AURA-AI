/// What a browser build of AURA cannot do, in one place.
///
/// The web app is the mobile app's structure in a browser (docs/plans/client-web-app_plan.md);
/// these gates are where it differs. Each takes `isWeb` so a test can ask the web's answer on the
/// test host, the way `chatHeaderShowsLiveVoice` and `DesktopSignInPage._isWeb` do.
library;

import 'package:flutter/foundation.dart';

/// Whether this build can record a voice note into the chat composer. The recorder writes the
/// clip to a file (`dart:io`) and a browser has none.
bool voiceNoteAvailable({bool isWeb = kIsWeb}) => !isWeb;

/// Whether live (duplex) voice can run. Needs a streaming microphone and a PCM sink; the web has
/// neither (`live_mic_source.dart`, `live_audio_sink.dart`). `/voice` is sent back to the chat.
bool liveVoiceRouteAvailable({bool isWeb = kIsWeb}) => !isWeb;

/// Whether this build can supervise and pair a local `aura_hostd` (ADR-011): it starts a process
/// and talks to a loopback control server, neither of which a browser can do. A paired computer's
/// remote control still works from the web through the backend.
bool localDaemonAvailable({bool isWeb = kIsWeb}) => !isWeb;
