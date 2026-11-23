"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { CloudflareProviders } from "@/components/cloudflare/providers-and-models";
import { CloudflareTokens } from "@/components/cloudflare-tokens";
import { PlatformCard } from "@/components/platform-card";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { CloudflareOverview, ControlOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";

/**
 * The Cloudflare tab (console plan, stage 6): the connection to the Cloudflare account and the API
 * tokens the console manages. Later work packages add the model catalog, the gateway's Cloudflare
 * providers (add, replace, remove), usage and R2 status to this page.
 */
export default function CloudflarePage() {
  const { t, errorText } = useI18n();
  const [platforms, setPlatforms] = useState<ControlOverview | null>(null);
  const [cloudflare, setCloudflare] = useState<CloudflareOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<{ message: string; watched: boolean } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => {
    Promise.all([
      api<ControlOverview>("/admin/control/overview"),
      api<CloudflareOverview>("/admin/control/cloudflare"),
    ])
      .then(([platformBody, cloudflareBody]) => {
        setPlatforms(platformBody);
        setCloudflare(cloudflareBody);
        setError(null);
      })
      .catch((err: unknown) => setError(err));
  }, []);

  useEffect(load, [load]);

  if (error && !platforms) return <Alert>{errorText(error)}</Alert>;
  if (!platforms || !cloudflare) return <Skeleton className="h-40 w-full" />;

  const row = platforms.platforms.find((p) => p.platform === "cloudflare");

  /** ``watched``: the change is a gateway deploy that is followed on the Changes tab. */
  function done(message: string, watched = false) {
    setNotice({ message, watched });
    setRefreshKey((n) => n + 1);
    load();
  }

  return (
    <div className="flex flex-col gap-5" data-testid="cloudflare-panel">
      <p className="text-[13px] text-muted">{t("settings.cloudflare.intro")}</p>
      {!platforms.vault_ready ? <Alert data-testid="vault-missing">{t("settings.vaultMissing")}</Alert> : null}
      {notice ? (
        <p className="text-[13px] text-ok" role="status" data-testid="cloudflare-notice">
          {notice.message}
          {notice.watched ? (
            <>
              {" "}
              <Link className="underline" href="/settings/changes" data-testid="cloudflare-changes-link">
                {t("settings.cloudflare.providers.seeChanges")}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}

      <section aria-labelledby="cf-connection" className="flex flex-col gap-2">
        <h3 id="cf-connection" className="text-[13px] font-semibold tracking-[0.06em] text-faint uppercase">
          {t("settings.cloudflare.connection")}
        </h3>
        <div className="max-w-xl">
          {row ? <PlatformCard row={row} vaultReady={platforms.vault_ready} onChanged={load} /> : null}
        </div>
        {cloudflare.connected && !cloudflare.account_id ? (
          <Alert className="mb-0" data-testid="cf-needs-account">{t("settings.cloudflare.needAccount")}</Alert>
        ) : null}
      </section>

      <CloudflareProviders overview={cloudflare} onDone={done} />

      {cloudflare.connected ? (
        <CloudflareTokens refreshKey={refreshKey} onDone={done} />
      ) : (
        <p className="text-[13px] text-muted" data-testid="cf-connect-first">{t("settings.cloudflare.connectFirst")}</p>
      )}
    </div>
  );
}
