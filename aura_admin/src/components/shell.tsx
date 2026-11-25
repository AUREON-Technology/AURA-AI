"use client";

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight, LogOut, Search } from "lucide-react";

import { Brand } from "@/components/brand";
import { CommandPalette } from "@/components/command-palette";
import { LanguageToggle } from "@/components/language-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useI18n } from "@/lib/i18n/context";
import { OPEN_PALETTE_EVENT, groupFor, navGroupsFor } from "@/lib/nav";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Where the sliding highlight sits: the active link's box inside the sidebar. */
type Indicator = { top: number; height: number; ready: boolean };

export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const current = groupFor(pathname);
  const initial = (me.email || "?").trim().charAt(0);
  const activeHref = current?.item.href;

  const navRef = useRef<HTMLElement>(null);
  const links = useRef(new Map<string, HTMLAnchorElement>());
  const [indicator, setIndicator] = useState<Indicator>({ top: 0, height: 0, ready: false });
  const [animate, setAnimate] = useState(false);

  const measure = useCallback(() => {
    const link = activeHref ? links.current.get(activeHref) : undefined;
    if (!link || !navRef.current) {
      setIndicator((prev) => ({ ...prev, ready: false }));
      return;
    }
    setIndicator({ top: link.offsetTop, height: link.offsetHeight, ready: true });
  }, [activeHref]);

  // Measure before paint so the highlight never flashes at a stale spot; the first placement is
  // instant, later ones slide.
  useLayoutEffect(() => {
    measure();
    const frame = requestAnimationFrame(() => setAnimate(true));
    return () => cancelAnimationFrame(frame);
  }, [measure]);

  useLayoutEffect(() => {
    const node = navRef.current;
    if (!node || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => measure());
    observer.observe(node);
    return () => observer.disconnect();
  }, [measure]);

  return (
    <div className="grid min-h-screen grid-cols-1 desk:grid-cols-[252px_minmax(0,1fr)]">
      <aside
        ref={navRef}
        aria-label={t("shell.navAria")}
        className={cn(
          "relative flex flex-wrap items-center gap-0.5 border-b border-line bg-surface p-3",
          "desk:sticky desk:top-0 desk:h-screen desk:flex-col desk:flex-nowrap desk:items-stretch desk:gap-0 desk:overflow-y-auto desk:border-e desk:border-b-0 desk:px-3 desk:py-4.5",
        )}
      >
        <Brand title={t("brand.console")} className="mb-2.5 w-full desk:mx-2 desk:mb-5.5 desk:w-auto" />

        {/* The sliding active highlight (desktop only; the collapsed bar marks the link itself). */}
        <span
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute inset-x-3 hidden rounded-sm bg-brand-soft desk:block",
            animate && "motion-safe:transition-[transform,height,opacity] motion-safe:duration-300 motion-safe:ease-out-soft",
            indicator.ready ? "opacity-100" : "opacity-0",
          )}
          style={{ top: 0, height: indicator.height, transform: `translateY(${indicator.top}px)` }}
        >
          <span className="absolute top-2 bottom-2 -start-3 w-[3px] rounded-e-[3px] bg-linear-180 from-brand to-brand-2" />
        </span>

        {navGroupsFor(me).map((group) => (
          <nav key={group.label} aria-label={t(group.label)} className="contents desk:mb-4 desk:block">
            <p className="mx-2.5 mb-1.5 hidden text-[11px] font-semibold tracking-[0.08em] text-faint uppercase desk:block">
              {t(group.label)}
            </p>
            {group.items.map((item) => {
              const active = activeHref === item.href;
              const ItemIcon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  ref={(node) => {
                    if (node) {
                      links.current.set(item.href, node);
                    } else {
                      links.current.delete(item.href);
                    }
                  }}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative z-10 flex items-center gap-2.5 rounded-sm px-2.5 py-[7px] font-medium transition-colors duration-150 desk:mb-0.5",
                    active
                      ? cn("text-fg max-desk:bg-brand-soft", !indicator.ready && "desk:bg-brand-soft")
                      : "text-muted hover:bg-surface-variant hover:text-fg",
                  )}
                >
                  <ItemIcon
                    aria-hidden="true"
                    strokeWidth={1.8}
                    className={cn(
                      "size-[18px] shrink-0 transition-[color,transform] duration-200",
                      active ? "text-accent" : "opacity-85 motion-safe:group-hover:scale-110",
                    )}
                  />
                  {t(item.label)}
                </Link>
              );
            })}
          </nav>
        ))}

        <div className="mt-2.5 flex w-full items-center justify-between gap-2 border-t border-line pt-3 desk:mt-auto desk:block desk:pt-3.5">
          <div className="flex min-w-0 items-center gap-2.5 px-2 desk:pb-2.5">
            <span
              aria-hidden="true"
              className="grid size-8 shrink-0 place-items-center rounded-full bg-linear-135 from-brand to-brand-2 p-px"
            >
              <span className="grid size-full place-items-center rounded-full bg-surface-variant text-[13px] font-semibold uppercase">
                {initial}
              </span>
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[13px]">{me.email}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted capitalize">
                {me.role}
                {me.is_super_admin && me.role !== "SuperAdmin" ? <Badge tone="brand" className="px-2 py-0 text-[11px] normal-case">SuperAdmin</Badge> : null}
              </div>
            </div>
          </div>
          <Button
            variant="ghost"
            className="desk:w-full"
            onClick={async () => {
              await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/auth/logout`, { method: "POST" });
              window.location.assign(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/login`);
            }}
          >
            <LogOut aria-hidden="true" className="rtl:-scale-x-100" />
            {t("shell.signOut")}
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-4 border-b border-line bg-bg/80 px-4 backdrop-blur-md backdrop-saturate-150 desk:px-8">
          <div className="flex min-w-0 items-center gap-1.5 text-[13px] text-muted">
            <span className="truncate">{current ? t(current.group) : t("shell.crumbConsole")}</span>
            <ChevronRight aria-hidden="true" className="size-3.5 shrink-0 text-faint rtl:-scale-x-100" />
            <strong key={current?.item.label} className="truncate font-semibold text-fg motion-safe:animate-fade-in">
              {current ? t(current.item.label) : t("shell.crumbPage")}
            </strong>
          </div>
          <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label={t("shell.openPalette")}
            onClick={() => window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))}
            className={cn(
              "inline-flex h-8 items-center gap-2.5 rounded-sm border border-line bg-surface pe-2 ps-3 text-[13px] text-muted",
              "transition-[border-color,color,box-shadow] duration-150 hover:border-line-strong hover:text-fg motion-safe:active:scale-[0.98] desk:min-w-[220px]",
            )}
          >
            <Search aria-hidden="true" className="size-[15px]" />
            <span className="hidden flex-1 text-start desk:inline">{t("shell.jumpTo")}</span>
            <Kbd>⌘K</Kbd>
          </button>
          <LanguageToggle />
          </div>
        </header>
        <main className="w-full max-w-[1280px] px-4 pt-5 pb-10 desk:px-8 desk:pt-7 desk:pb-14">
          <CommandPalette me={me} />
          {children}
        </main>
      </div>
    </div>
  );
}
