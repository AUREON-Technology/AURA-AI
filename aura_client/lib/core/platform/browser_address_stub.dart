/// Not a browser: there is no address bar to rewrite.
void dropAddressPathAndQuery() {}

/// The path the app is served from (its `<base href>`): always the root off the web.
String browserBasePath() => '/';
