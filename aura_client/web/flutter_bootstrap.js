{{flutter_js}}
{{flutter_build_config}}

// Start the app once the Firebase SDK is in place (firebase-sdk.js, loaded in index.html's head),
// so firebase_core_web never has to inject its own inline script.
(async function () {
  try {
    if (window.__auraFirebaseReady) await window.__auraFirebaseReady;
  } catch (e) {
    // firebase-sdk.js handles its own failure; start the app regardless.
  }
  _flutter.loader.load({
    serviceWorkerSettings: {
      serviceWorkerVersion: {{flutter_service_worker_version}},
    },
  });
})();
