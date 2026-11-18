"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CommandPalette } from "@/components/command-palette";
import { Icon } from "@/components/icons";
import { LanguageToggle } from "@/components/language-toggle";
import { useI18n } from "@/lib/i18n/context";
import { NAV_GROUPS, OPEN_PALETTE_EVENT, groupFor } from "@/lib/nav";
import type { Me } from "@/lib/types";

export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const current = groupFor(pathname);
  const initial = (me.email || "?").trim().charAt(0);

  return (
    <div className="shell">
      <aside className="nav" aria-label={t("shell.navAria")}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span className="brand-text">
            <span>{t("brand.console")}</span>
            <small>{t("brand.workspace")}</small>
          </span>
        </div>
        {NAV_GROUPS.map((group) => (
          <nav className="nav-group" key={group.label} aria-label={t(group.label)}>
            <p className="nav-label">{t(group.label)}</p>
            {group.items.map((item) => {
              const active = current?.item.href === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "active" : ""}
                  aria-current={active ? "page" : undefined}
                >
                  <Icon name={item.icon} />
                  {t(item.label)}
                </Link>
              );
            })}
          </nav>
        ))}
        <div className="nav-foot">
          <div className="account">
            <span className="avatar" aria-hidden="true">
              {initial}
            </span>
            <div className="account-meta">
              <div className="email">{me.email}</div>
              <div className="role">
                {me.role}
                {me.is_super_admin && me.role !== "SuperAdmin" ? <span className="badge">SuperAdmin</span> : null}
              </div>
            </div>
          </div>
          <button
            className="btn ghost"
            type="button"
            onClick={async () => {
              await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/auth/logout`, { method: "POST" });
              window.location.assign(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/login`);
            }}
          >
            <Icon name="logout" size={16} />
            {t("shell.signOut")}
          </button>
        </div>
      </aside>
      <div className="content">
        <header className="topbar">
          <div className="crumbs">
            <span>{current ? t(current.group) : t("shell.crumbConsole")}</span>
            <span aria-hidden="true">/</span>
            <strong>{current ? t(current.item.label) : t("shell.crumbPage")}</strong>
          </div>
          <div className="topbar-actions">
            <button
              className="kbd-btn"
              type="button"
              aria-label={t("shell.openPalette")}
              onClick={() => window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))}
            >
              <Icon name="search" size={15} />
              <span>{t("shell.jumpTo")}</span>
              <kbd>⌘K</kbd>
            </button>
            <LanguageToggle />
          </div>
        </header>
        <main className="main">
          <CommandPalette />
          {children}
        </main>
      </div>
    </div>
  );
}
