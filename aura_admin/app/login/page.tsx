"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Brand } from "@/components/brand";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
    <div className="relative grid min-h-screen place-items-center overflow-hidden p-6">
      {/* Split glow: indigo top-left, violet bottom-right, drifting slowly. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-56 -start-40 size-[560px] rounded-full bg-brand opacity-35 blur-[90px] motion-safe:animate-drift"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -end-44 -bottom-64 size-[560px] rounded-full bg-brand-2 opacity-35 blur-[90px] motion-safe:animate-drift-reverse"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(var(--line)_1px,transparent_1px),linear-gradient(90deg,var(--line)_1px,transparent_1px)] bg-size-[44px_44px] [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)] opacity-60"
      />

      <Card className="relative z-10 w-full max-w-[440px] rounded-lg p-8 shadow-raised motion-safe:animate-card-in">
        <div className="mb-7 flex items-center justify-between gap-3">
          <Brand title="AURA" />
          <LanguageToggle />
        </div>
        <h1 className="mb-1.5 text-[26px] leading-tight font-bold tracking-[-0.02em]">{t("brand.console")}</h1>
        <p className="mb-6 text-muted">{t("login.subtitle")}</p>
        {error ? (
          <Alert>
            {error === "role"
              ? t("login.roleRequired")
              : error === "refused"
                ? t("login.refused")
                : errorText(error)}
          </Alert>
        ) : null}
        <div id="google-btn" />
        {process.env.NEXT_PUBLIC_DEV_LOGIN === "1" ? (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" size="lg" className="flex-1" onClick={() => void exchange("mock_token")}>
              {t("login.devMock")}
            </Button>
            <Button size="lg" className="flex-1" onClick={() => void exchange("mock_token_super")}>
              {t("login.superMock")}
            </Button>
          </div>
        ) : null}
        <p className="mt-6 flex items-center gap-2 border-t border-line pt-4 text-xs text-faint">
          <ShieldCheck aria-hidden="true" className="size-3.5 shrink-0" />
          {t("login.fine")}
        </p>
      </Card>
    </div>
  );
}
