import 'dart:js_interop';

@JS('window.history')
external _History get _history;

@JS('window.location.hash')
external String get _locationHash;

@JS('document.baseURI')
external String get _baseUri;

extension type _History(JSObject _) implements JSObject {
  external JSAny? get state;
  external void replaceState(JSAny? data, String unused, String url);
}

/// Replaces the current history entry's path and query with the app's base address (its
/// `<base href>`), keeping the `#fragment` the router uses. Nothing is navigated or reloaded, and
/// no new history entry is added, so the removed query is gone from both the address bar and the
/// back button.
void dropAddressPathAndQuery() {
  final base = Uri.parse(_baseUri);
  final root = '${base.scheme}://${base.authority}${base.path}';
  _history.replaceState(_history.state, '', '$root$_locationHash');
}
