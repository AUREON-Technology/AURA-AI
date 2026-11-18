/// Keeps the web page's `<html lang>` in step with the language on screen, so a screen reader
/// reading Flutter's semantics DOM announces Arabic as Arabic. A no-op on every other platform.
///
/// Only `lang` is set, never `dir`: Flutter lays out right-to-left itself, and flipping the
/// document's direction could move the engine's own semantics overlay.
library;

export 'document_language_stub.dart'
    if (dart.library.js_interop) 'document_language_web.dart';
