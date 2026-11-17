"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
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
      <PageHeader title="Settings">Read-only effective configuration. Secrets are redacted by the gateway.</PageHeader>
      {error ? <Alert>{error}</Alert> : null}
      <Card className="p-0">
        <pre className="overflow-x-auto p-5 text-[12.5px] leading-relaxed text-fg">{JSON.stringify(settings, null, 2)}</pre>
      </Card>
    </>
  );
}
