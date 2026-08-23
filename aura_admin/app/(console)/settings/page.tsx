"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { AdminStatus } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AdminStatus>("/admin/status")
      .then((status) => setSettings(status.settings ?? {}))
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">Read-only effective configuration. Secrets are redacted by the gateway.</p>
      {error ? <div className="banner">{error}</div> : null}
      <pre className="card">{JSON.stringify(settings, null, 2)}</pre>
    </>
  );
}
