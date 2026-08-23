"use client";

import { useEffect, useState } from "react";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (el: HTMLElement, config: Record<string, unknown>) => void;
        };
      };
    };
  }
}

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);

  async function exchange(idToken: string) {
    const response = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ idToken }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || body.message || "Sign-in was refused");
      return;
    }
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    window.location.assign(base || "/");
  }

  useEffect(() => {
    const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
    if (!clientId) {
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      const host = document.getElementById("google-btn");
      if (!host || !window.google) {
        return;
      }
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (response: { credential: string }) => {
          void exchange(response.credential);
        },
      });
      window.google.accounts.id.renderButton(host, { theme: "filled_black", size: "large", width: 320 });
    };
    document.body.appendChild(script);
  }, []);

  return (
    <div className="login card">
      <h1>AURA Console</h1>
      <p className="sub">Operator access only. Consumer accounts are refused.</p>
      {error ? <div className="banner">{error}</div> : null}
      <div id="google-btn" />
      {process.env.NEXT_PUBLIC_DEV_LOGIN === "1" ? (
        <div className="row" style={{ marginTop: 16 }}>
          <button className="btn primary" type="button" onClick={() => void exchange("mock_token")}>
            Developer mock login
          </button>
          <button className="btn" type="button" onClick={() => void exchange("mock_token_super")}>
            SuperAdmin mock login
          </button>
        </div>
      ) : null}
    </div>
  );
}
