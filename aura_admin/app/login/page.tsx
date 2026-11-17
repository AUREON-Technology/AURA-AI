"use client";

import { useEffect, useState } from "react";

import { LanguageToggle } from "@/components/language-toggle";
import { type AuraApiError, errorFromBody } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

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
  const { t, errorText } = useI18n();
  // "role": sent here by the console layout; "refused": a refusal with no message to show.
  const [error, setError] = useState<AuraApiError | "role" | "refused" | null>(null);

  async function exchange(idToken: string) {
    const response = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ idToken }),
    });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      const refusal = errorFromBody(response.status, body);
      setError(refusal.message ? refusal : "refused");
      return;
    }
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    window.location.assign(base || "/");
  }

  useEffect(() => {
    // The console layout sends a signed-in account below Admin here (audit INT-042).
    if (new URLSearchParams(window.location.search).get("reason") === "role") {
      setError("role");
    }
  }, []);

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
    <div className="login-page">
      <div className="login card">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span className="brand-text">
            <span>AURA</span>
            <small>{t("brand.workspace")}</small>
          </span>
          <LanguageToggle />
        </div>
        <h1>{t("brand.console")}</h1>
        <p className="sub">{t("login.subtitle")}</p>
        {error ? (
          <div className="banner">
            {error === "role"
              ? t("login.roleRequired")
              : error === "refused"
                ? t("login.refused")
                : errorText(error)}
          </div>
        ) : null}
        <div id="google-btn" />
        {process.env.NEXT_PUBLIC_DEV_LOGIN === "1" ? (
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn primary" type="button" onClick={() => void exchange("mock_token")}>
              {t("login.devMock")}
            </button>
            <button className="btn" type="button" onClick={() => void exchange("mock_token_super")}>
              {t("login.superMock")}
            </button>
          </div>
        ) : null}
        <p className="fine">{t("login.fine")}</p>
      </div>
    </div>
  );
}
