"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CommandPalette } from "@/components/command-palette";
import type { Me } from "@/lib/types";

const LINKS = [
  ["/", "Overview"],
  ["/providers", "Providers"],
  ["/knowledge", "Knowledge"],
  ["/prompts", "Prompts"],
  ["/models", "Models"],
  ["/approvals", "Approvals"],
  ["/learning", "Learning"],
  ["/audit", "Audit"],
  ["/access", "Access"],
  ["/settings", "Settings"],
] as const;

export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="shell">
      <aside className="nav">
        <p className="brand">
          AURA Console
          {me.is_super_admin ? <span className="badge">SuperAdmin</span> : null}
        </p>
        {LINKS.map(([href, label]) => (
          <Link key={href} href={href} className={pathname === href ? "active" : ""}>
            {label}
          </Link>
        ))}
        <p className="muted" style={{ margin: "24px 10px 0" }}>
          {me.email}
          <br />
          {me.role}
        </p>
        <button
          className="btn"
          type="button"
          style={{ margin: "12px 10px" }}
          onClick={async () => {
            await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/auth/logout`, { method: "POST" });
            window.location.assign(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/login`);
          }}
        >
          Sign out
        </button>
      </aside>
      <main className="main">
        <CommandPalette />
        {children}
      </main>
    </div>
  );
}
