"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useI18n } from "@/lib/i18n/context";
import { api } from "@/lib/api";
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
      <PageHeader title={t("nav.settings")}>{t("settings.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      <Card className="p-0">
        <pre className="overflow-x-auto p-5 text-[12.5px] leading-relaxed text-fg">{JSON.stringify(settings, null, 2)}</pre>
      </Card>
    </>
  );
}
