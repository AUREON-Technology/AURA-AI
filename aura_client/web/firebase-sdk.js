// Loads the Firebase JS SDK from our own page instead of letting firebase_core_web inject it.
//
// firebase_core_web otherwise adds an INLINE <script> that `import()`s the SDK from gstatic.com,
// which the host's Content-Security-Policy forbids (no 'unsafe-inline'; aura_web/nginx/
// security-headers-app.conf). It skips that injection when `window.firebase_core` already exists,
// so this file puts the SDK modules there first, and flutter_bootstrap.js waits for it before it
// starts the app.
//
// The version MUST equal the one firebase_core_web supports (`supportedFirebaseJsSdkVersion` in
// its firebase_sdk_version.dart); test/web/web_shell_test.dart fails when they differ, which is
// what a firebase_core upgrade will do. Core loads first and auth after it, not concurrently:
// firebase_core_web documents a Safari module-evaluation race otherwise.
//
// A failed load (offline, blocked) does not stop the app: it starts, and sign-in reports that it
// is unavailable.
window.__auraFirebaseReady = (async function () {
  var version = '12.19.0';
  var base = 'https://www.gstatic.com/firebasejs/' + version + '/';
  try {
    window.firebase_core = await import(base + 'firebase-app.js');
    window.firebase_auth = await import(base + 'firebase-auth.js');
  } catch (e) {
    delete window.firebase_core;
    delete window.firebase_auth;
    console.warn('AURA: the Firebase SDK did not load; sign-in will be unavailable.', e);
  }
})();
