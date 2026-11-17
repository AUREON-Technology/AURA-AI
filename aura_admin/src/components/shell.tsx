"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CommandPalette } from "@/components/command-palette";
import { Icon } from "@/components/icons";
import { NAV_GROUPS, OPEN_PALETTE_EVENT, groupFor } from "@/lib/nav";
import type { Me } from "@/lib/types";

export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const pathname = usePathname();
  const current = groupFor(pathname);
  const initial = (me.email || "?").trim().charAt(0);

  return (
    <div className="shell">
      <aside className="nav" aria-label="Console navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span className="brand-text">
            <span>AURA Console</span>
            <small>Operator workspace</small>
          </span>
        </div>
        {NAV_GROUPS.map((group) => (
          <nav className="nav-group" key={group.label} aria-label={group.label}>
            <p className="nav-label">{group.label}</p>
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
                  {item.label}
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
            Sign out
          </button>
        </div>
      </aside>
      <div className="content">
        <header className="topbar">
          <div className="crumbs">
            <span>{current?.group ?? "Console"}</span>
            <span aria-hidden="true">/</span>
            <strong>{current?.item.label ?? "Page"}</strong>
          </div>
          <button
            className="kbd-btn"
            type="button"
            aria-label="Open command palette"
            onClick={() => window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))}
          >
            <Icon name="search" size={15} />
            <span>Jump to…</span>
            <kbd>⌘K</kbd>
          </button>
        </header>
        <main className="main">
          <CommandPalette />
          {children}
        </main>
      </div>
    </div>
  );
}
