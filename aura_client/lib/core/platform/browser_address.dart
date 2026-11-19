/// Rewrites the browser's address bar without navigating, for a web build that keeps Flutter's
/// default hash URL strategy. A no-op on every other platform.
///
/// Under that strategy the router only ever changes the `#fragment`. A link opened as a real path
/// (`https://app.example/invite?token=…`) therefore keeps its path and query in the address bar
/// and in history whatever the router does next -- so a single-use secret in that query has to be
/// removed here, outside the router.
library;

export 'browser_address_stub.dart'
    if (dart.library.js_interop) 'browser_address_web.dart';
