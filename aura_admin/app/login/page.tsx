"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Brand } from "@/components/brand";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { errorFromBody } from "@/lib/api";
import { CONSOLE_ROLE_MESSAGE } from "@/lib/session";

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
      const body: unknown = await response.json().catch(() => null);
      setError(errorFromBody(response.status, body).message || "Sign-in was refused");
      return;
    }
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    window.location.assign(base || "/");
  }

  useEffect(() => {
    // The console layout sends a signed-in account below Admin here (audit INT-042).
    if (new URLSearchParams(window.location.search).get("reason") === "role") {
      setError(CONSOLE_ROLE_MESSAGE);
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
        className="pointer-events-none absolute -top-56 -left-40 size-[560px] rounded-full bg-brand opacity-35 blur-[90px] motion-safe:animate-drift"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-44 -bottom-64 size-[560px] rounded-full bg-brand-2 opacity-35 blur-[90px] motion-safe:animate-drift-reverse"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(var(--line)_1px,transparent_1px),linear-gradient(90deg,var(--line)_1px,transparent_1px)] bg-size-[44px_44px] [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)] opacity-60"
      />

      <Card className="relative z-10 w-full max-w-[440px] rounded-lg p-8 shadow-raised motion-safe:animate-card-in">
        <Brand title="AURA" className="mb-7" />
        <h1 className="mb-1.5 text-[26px] leading-tight font-bold tracking-[-0.02em]">AURA Console</h1>
        <p className="mb-6 text-muted">Operator access only. Consumer accounts are refused.</p>
        {error ? <Alert>{error}</Alert> : null}
        <div id="google-btn" />
        {process.env.NEXT_PUBLIC_DEV_LOGIN === "1" ? (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" size="lg" className="flex-1" onClick={() => void exchange("mock_token")}>
              Developer mock login
            </Button>
            <Button size="lg" className="flex-1" onClick={() => void exchange("mock_token_super")}>
              SuperAdmin mock login
            </Button>
          </div>
        ) : null}
        <p className="mt-6 flex items-center gap-2 border-t border-line pt-4 text-xs text-faint">
          <ShieldCheck aria-hidden="true" className="size-3.5 shrink-0" />
          Confirmed changes made here write an audit event.
        </p>
      </Card>
    </div>
  );
}
