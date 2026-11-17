"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { AdminStatus } from "@/lib/types";

export default function SettingsPage() {
  const { t, errorText } = useI18n();
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<AdminStatus>("/admin/status")
      .then((status) => setSettings(status.settings ?? {}))
      .catch((err: unknown) => setError(err));
  }, []);

  return (
    <>
      <h1>{t("nav.settings")}</h1>
      <p className="sub">{t("settings.sub")}</p>
      {error ? <div className="banner">{errorText(error)}</div> : null}
      <pre className="card">{JSON.stringify(settings, null, 2)}</pre>
    </>
  );
}
