"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

import { stepUpError } from "@/components/change-tray";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useStepUp } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableEmpty, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { ChangeRow, CloudflareTokenRow } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

/**
 * The Cloudflare API tokens the stored management token can see, with Revoke on every one the
 * gateway does not use. Shared by the Keys tab (where a new key is created) and the Cloudflare tab.
 * A token's value is never in the list. ``onDone`` reports what was done so the page can say so and
 * refresh; ``refreshKey`` makes the list reload after a change made elsewhere on the page.
 */
export function CloudflareTokens({ refreshKey, onDone }: { refreshKey: number; onDone: (message: string) => void }) {
  const { t, lang, errorText } = useI18n();
  const stepUp = useStepUp();
  const [tokens, setTokens] = useState<CloudflareTokenRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<{ tokens: CloudflareTokenRow[] }>("/admin/control/keys/cloudflare/tokens")
      .then((body) => {
        setTokens(body.tokens);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, [refreshKey]);

  return (
    <section aria-labelledby="cf-tokens" className="flex flex-col gap-2">
      <h3 id="cf-tokens" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">{t("settings.keys.cloudflareTokens")}</h3>
      <p className="text-[12.5px] text-muted">{t("settings.keys.cloudflareNote")}</p>
      {error ? <Alert className="mb-0">{errorText(error)}</Alert> : null}
      <Card className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.railway.name")}</TableHead>
              <TableHead>{t("common.status")}</TableHead>
              <TableHead>{t("settings.keys.issued")}</TableHead>
              <TableHead><span className="sr-only">{t("settings.railway.actions")}</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens === null && !error ? (
              <TableRow><TableCell colSpan={4}><Skeleton className="h-6 w-full" /></TableCell></TableRow>
            ) : null}
            {tokens?.length === 0 ? <TableEmpty colSpan={4}>{t("settings.keys.noTokens")}</TableEmpty> : null}
            {tokens?.map((token) => (
              <TableRow key={token.token_id} data-testid={`cf-token-${token.name}`}>
                <TableCell><code dir="ltr" className="text-[12.5px]">{token.name}</code></TableCell>
                <TableCell className="flex flex-wrap gap-1">
                  <Badge tone={token.status === "active" ? "ok" : "neutral"}>{token.status || "—"}</Badge>
                  {token.in_use ? <Badge tone="brand">{t("settings.keys.inUse")}</Badge> : null}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted">
                  {token.issued_on ? new Date(token.issued_on).toLocaleString(lang === "ar" ? "ar" : "en") : "—"}
                </TableCell>
                <TableCell className="text-end">
                  {!token.in_use ? (
                    <ConfirmDialog
                      title={t("settings.keys.revokeTitle", { name: token.name })}
                      confirmText={token.name}
                      danger
                      label={t("settings.keys.revoke", { name: token.name })}
                      onConfirm={async () => {
                        try {
                          await stepUp.write<ChangeRow>(`/admin/control/keys/cloudflare/tokens/${token.token_id}/revoke`, { confirm: token.name });
                          onDone(t("settings.keys.revoked", { name: token.name }));
                        } catch (err) {
                          throw stepUpError(err, t);
                        }
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </ConfirmDialog>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </section>
  );
}
