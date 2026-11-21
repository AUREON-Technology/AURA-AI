"use client";

import { useEffect } from "react";

// Where Google sends the step-up popup back (src/lib/control.ts). It hands the ID token to the
// console window that opened it -- on this origin only -- and closes. It reads nothing else and
// calls nothing: the opener redeems the token with the gateway.
export default function StepUpCallback() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const idToken = params.get("id_token");
    const error = params.get("error");
    // Drop the token from the address bar and history straight away.
    window.history.replaceState(null, "", window.location.pathname);
    if (window.opener) {
      window.opener.postMessage(
        { type: "aura-step-up", idToken: idToken ?? undefined, error: error ?? (idToken ? undefined : "no_token") },
        window.location.origin,
      );
    }
    window.close();
  }, []);
  return null;
}
