"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { StepUpContext } from "@/components/settings-context";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchMe } from "@/lib/api";
import { StepUp } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";

const TABS: { href: string; label: MessageKey }[] = [
  { href: "/settings", label: "settings.tab.overview" },
  { href: "/settings/railway", label: "settings.tab.railway" },
  { href: "/settings/space", label: "settings.tab.space" },
  { href: "/settings/aura", label: "settings.tab.aura" },
  { href: "/settings/changes", label: "settings.tab.changes" },
];

/**
 * The control center's frame (ADR-037). Only a Super Admin gets past it: anyone else sees one
 * line and no tab renders, so no page even calls the gateway (which would refuse it anyway).
 */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const { t, errorText } = useI18n();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    fetchMe()
      .then(setMe)
      .catch((err: unknown) => setError(err));
  }, []);

  const stepUp = useMemo(() => (me ? new StepUp(me.email) : null), [me]);

  return (
    <>
      <PageHeader title={t("nav.settings")}>{t("settings.sub")}</PageHeader>
      {error ? <Alert>{errorText(error)}</Alert> : null}
      {!me && !error ? <Skeleton className="h-24 w-full" /> : null}
      {me && !me.is_super_admin ? <Alert data-testid="settings-super-only">{t("settings.superAdminOnly")}</Alert> : null}
      {me?.is_super_admin && stepUp ? (
        <StepUpContext.Provider value={stepUp}>
          <nav aria-label={t("settings.tabsAria")} className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
            {TABS.map((tab) => {
              const active = tab.href === "/settings" ? pathname === "/settings" : pathname.startsWith(tab.href);
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "-mb-px shrink-0 border-b-2 px-3 py-2 text-[13.5px] font-medium whitespace-nowrap",
                    active ? "border-brand text-fg" : "border-transparent text-muted hover:text-fg",
                  )}
                >
                  {t(tab.label)}
                </Link>
              );
            })}
          </nav>
          {children}
        </StepUpContext.Provider>
      ) : null}
    </>
  );
}
