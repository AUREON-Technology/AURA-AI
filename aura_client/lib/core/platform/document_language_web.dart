import 'dart:js_interop';

@JS('document.documentElement')
external _Element get _documentElement;

extension type _Element(JSObject _) implements JSObject {
  external String? getAttribute(String name);
  external void setAttribute(String name, String value);
}

/// Sets `<html lang>` when it differs (the builder calls this on every rebuild).
void setDocumentLanguage(String languageCode) {
  if (_documentElement.getAttribute('lang') != languageCode) {
    _documentElement.setAttribute('lang', languageCode);
  }
}
