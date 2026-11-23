"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ChangeRow, CloudflareProviderRow, CloudflareValidateResult } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

import { problemText } from "./refusal";

/** What a removal deletes, read from a dry run on the gateway, and what happens to the token. */
function RemoveBody({ name }: { name: string }) {
  const { t, errorText } = useI18n();
  const [result, setResult] = useState<CloudflareValidateResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api<CloudflareValidateResult>("/admin/control/cloudflare/providers/validate", {
      method: "POST",
      body: JSON.stringify({ operation: "remove", name }),
    })
      .then(setResult)
      .catch((err: unknown) => setProblem(problemText(err, t, errorText)));
  }, [name, t, errorText]);

  if (problem) return <Alert className="mb-0" data-testid="remove-problem">{problem}</Alert>;
  if (!result) return <Skeleton className="h-16 w-full" />;
  return (
    <div className="flex flex-col gap-3 text-[13px]" data-testid="remove-body">
      <div>
        <p className="mb-1.5 text-muted">{t("settings.cloudflare.providers.removeDeletes")}</p>
        <ul className="flex flex-col gap-1">
          {result.variables.map((variable) => (
            <li key={variable.name}>
              <code dir="ltr" className="text-[12px] break-all" data-testid="remove-variable">{variable.name}</code>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-muted" data-testid="remove-token-note">
        {result.revoke_token_id
          ? t("settings.cloudflare.providers.removeToken")
          : t("settings.cloudflare.providers.removeNoToken")}
      </p>
    </div>
  );
}

/** Remove one Cloudflare provider: lists what goes, then the typed provider name and a fresh sign-in. */
export function RemoveProvider({ row, onDone }: { row: CloudflareProviderRow; onDone: (name: string) => void }) {
  const { t, errorText } = useI18n();
  const stepUp = useStepUp();

  async function remove() {
    try {
      await stepUp.write<ChangeRow>(`/admin/control/cloudflare/providers/${encodeURIComponent(row.name)}/remove`, {
        confirm: row.name,
      });
    } catch (err) {
      throw new Error(problemText(stepUpError(err, t), t, errorText));
    }
    onDone(row.name);
  }

  return (
    <ConfirmDialog
      title={t("settings.cloudflare.providers.removeTitle", { name: row.name })}
      confirmText={row.name}
      danger
      label={t("settings.cloudflare.providers.removeFor", { name: row.name })}
      body={<RemoveBody name={row.name} />}
      onConfirm={remove}
    >
      <Trash2 aria-hidden="true" />
      {t("settings.cloudflare.providers.remove")}
    </ConfirmDialog>
  );
}
